"""Playwright adapter. All browser objects belong to the runtime's event loop."""
import asyncio
import re
import time
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

from services.gimpo.validation import AGREEMENTS, AIRPORT, PARKING, PARKING_NAME
from services.gimpo.store import Conflict, READY

ORIGIN = "https://park.airport.co.kr"
START_URL = ORIGIN + "/reservation/recheck.do"
PAYMENT_PATH = "/reservation/payment.json"


class BrowserFault(Exception):
    def __init__(self, message, state="REVIEW_REQUIRED", retryable=False):
        super().__init__(message)
        self.state = state
        self.retryable = retryable


class BrowserClient(Protocol):
    async def check(self) -> bool: ...
    async def prepare(self) -> dict: ...
    async def proceed(self) -> tuple[bool, float]: ...
    async def alive(self) -> bool: ...
    async def inspect(self) -> bool: ...
    async def show(self) -> None: ...
    async def close(self) -> None: ...


class OfficialContract:
    """Fail closed on unknown JSON, unexpected forms and nonpositive payment amounts."""
    @staticmethod
    def code(data, duplicate=False):
        try:
            code = data["code"] if duplicate else data["result"]["code"]
        except (KeyError, TypeError):
            raise BrowserFault("공식 조회 응답 형식이 변경되었습니다.") from None
        if code not in ({"00", "10", "20"} if duplicate else {"00", "10"}):
            raise BrowserFault("공식 조회에서 확인되지 않은 결과를 반환했습니다.")
        return code

    @staticmethod
    def summary(fields, inputs):
        expected = {"resInDttm": inputs["entryAt"] + ":00", "resOutDttm": inputs["exitAt"] + ":00",
                    "parkingDivCd": inputs["airportCode"], "sectnId": inputs["parkingId"],
                    "discountCd": inputs["discountSelection"], "parkingName": PARKING_NAME, "airportNm": "김포공항"}
        if any(fields.get(k) != [v] for k, v in expected.items()):
            raise BrowserFault("공식 화면의 날짜·주차장·할인이 입력과 일치하지 않습니다.")
        amounts = {}
        for name in ("calculateAmt", "depositAmt", "paymentAmt", "receiptAmt", "discountAmt"):
            value = fields.get(name, [])
            if len(value) != 1 or not re.fullmatch(r"-?[0-9]{1,10}", value[0]):
                raise BrowserFault("공식 요금을 확인할 수 없습니다.")
            amounts[name] = int(value[0])
        if (amounts["paymentAmt"] <= 0 or amounts["depositAmt"] != amounts["paymentAmt"]
                or amounts["calculateAmt"] < 0 or amounts["discountAmt"] != 0):
            raise BrowserFault("보증금 또는 일반 요금이 예상과 달라 직접 확인이 필요합니다.")
        return {"airportName": "김포공항", "parkingName": PARKING_NAME,
                "entryAt": inputs["entryAt"], "exitAt": inputs["exitAt"], **amounts}


class PlaywrightGimpoClient:
    def __init__(self, owner, job, inputs, *, headless=False):
        self.owner, self.job_id, self.inputs = owner, job["id"], inputs
        self.version = {k: job[k] for k in ("inputVersion", "generation")}
        self.headless = headless
        self.context = self.browser = self.playwright = self.page = None
        self.sealed_form = None
        self.dialog_error = False
        self.allow_confirmation = False
        self.progress_seen = False
        self.payment_response_ok = False
        self.closed = False

    async def _start(self):
        if self.context:
            return
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", service_workers="block")
        self.context.set_default_timeout(self.owner.config.browser_timeout_sec * 1000)
        await self.context.expose_binding("__gimpoCancel", self._cancel_signal)
        await self.context.route("**/*", self._guard)
        self.context.on("page", self._on_page)
        self.page = await self.context.new_page()

    def _cancel_signal(self, source):
        if source["page"] == self.page:
            self.owner.handoff_cancelled(self.job_id)

    def _on_page(self, page):
        page.on("dialog", self._dialog)
        page.on("framenavigated", lambda frame: self._observe_navigation(page, frame))
        page.on("response", self._response)
        page.on("requestfailed", self._failed_request)

    def _observe_navigation(self, page, frame):
        if frame != page.main_frame:
            return
        job = self.owner.store.get(self.job_id)
        if job["paymentMayHaveBeenSent"] and urlparse(frame.url).scheme == "https" and urlparse(frame.url).hostname != "park.airport.co.kr":
            self.progress_seen = True
            self._mark_progress()

    def _mark_progress(self):
        if self.progress_seen and self.payment_response_ok:
            try:
                self.owner.store.transition(self.job_id, "PAYMENT_IN_PROGRESS", "공항 결제창에서 결제를 마친 뒤 예약 내역을 확인해주세요.",
                                            expected={"PAYMENT_DISPATCHING"})
            except Conflict:
                pass

    async def _response(self, response):
        if urlparse(response.url).path != PAYMENT_PATH:
            return
        job = self.owner.store.get(self.job_id)
        if not job["paymentMayHaveBeenSent"]:
            return
        try:
            data = await response.json()
            self.payment_response_ok = response.ok and isinstance(data.get("paymentMap", {}).get("paymentForm"), str) and bool(data["paymentMap"]["paymentForm"])
        except Exception:
            self.payment_response_ok = False
        if not self.payment_response_ok:
            self.owner.payment_unknown(self.job_id)
        else:
            self._mark_progress()

    async def _failed_request(self, request):
        if urlparse(request.url).path == PAYMENT_PATH and self.owner.store.get(self.job_id)["paymentMayHaveBeenSent"]:
            # Extra blocked requests must never affect the first accepted one.
            if getattr(self, "accepted_request", None) == request:
                self.owner.payment_unknown(self.job_id)

    async def _guard(self, route):
        request = route.request
        parsed = urlparse(request.url)
        path = unquote(parsed.path)
        own = parsed.hostname == "park.airport.co.kr"
        if own and path == PAYMENT_PATH:
            job = self.owner.store.get(self.job_id)
            if job["paymentMayHaveBeenSent"]:
                await route.abort()
                return
            try:
                body = parse_qs(request.post_data or "", keep_blank_values=True)
                if (request.method != "POST" or request.frame != self.page.main_frame
                        or not self.sealed_form or body != self.sealed_form or not await self.inspect()):
                    raise Conflict("결제 정보가 변경되었습니다. 다시 준비해주세요.")
                self.owner.store.dispatch_payment(self.job_id, self.version)
            except Exception:
                await route.abort()
                return
            self.accepted_request = request
            try:
                await route.fallback()  # No retries, payload changes or response rewriting.
            except Exception:
                self.owner.payment_unknown(self.job_id)
            return
        if own and path == "/reservation/insertAction.do":
            if not self.owner.store.get(self.job_id)["paymentMayHaveBeenSent"]:
                await route.abort()
                self.owner.browser_fault(self.job_id, "자동 단계의 직접 예약 생성 요청을 차단했습니다.")
                return
        if self.closed:
            await route.abort()
            return
        await route.fallback()

    async def _dialog(self, dialog):
        job = self.owner.store.get(self.job_id)
        if job["paymentMayHaveBeenSent"]:
            # Native dialogs during PG belong to the user; do not acknowledge them.
            return
        values = (self.inputs["entryAt"] + ":00", self.inputs["exitAt"] + ":00",
                  PARKING_NAME, self.inputs["carNumber"], self.inputs["phone"])
        if (self.allow_confirmation and dialog.type == "confirm"
                and dialog.message.startswith("작성 내용을 다시 한번 확인해주세요.")
                and all(v in dialog.message for v in values)):
            await dialog.accept()
        else:
            self.dialog_error = True
            await dialog.dismiss()

    async def _read_code(self, response, duplicate=False):
        if response.status in {401, 403, 429}:
            raise BrowserFault("공항 사이트에서 접근 또는 조회를 제한했습니다.", "ERROR")
        if response.status >= 500:
            raise BrowserFault("공항 서버 응답이 지연되고 있습니다.", "ERROR", retryable=True)
        if response.status != 200:
            raise BrowserFault("공항 세션 또는 화면을 확인할 수 없습니다.", "SESSION_EXPIRED")
        try:
            data = await response.json()
        except Exception:
            raise BrowserFault("정상 조회 대신 오류 화면을 받았습니다.", "SESSION_EXPIRED") from None
        return OfficialContract.code(data, duplicate)

    async def check(self):
        await self._start()
        response = await self.page.goto(START_URL, wait_until="load")
        if response.status != 200:
            raise BrowserFault("공항 시작 화면을 열 수 없습니다.", "ERROR")
        await self.page.wait_for_function("typeof rescheck === 'function'")
        await self.page.evaluate("() => new Promise(resolve => $(resolve))")
        await self.page.select_option("#parkingDivCd", AIRPORT)
        await self.page.wait_for_function("Array.from(document.querySelectorAll('#parkingNm option')).some(o => o.value === '2')")
        name = await self.page.locator('#parkingNm option[value="2"]').text_content()
        if name.strip() != PARKING_NAME:
            raise BrowserFault("공식 주차장 선택 목록이 변경되었습니다.")
        await self.page.select_option("#parkingNm", PARKING)
        # Official date widgets are readonly: set their displayed values, before submitting step 1.
        await self.page.evaluate("([a,b]) => {$('#resInDttm').val(a); $('#resOutDttm').val(b)}",
                                 [self.inputs["entryAt"] + ":00", self.inputs["exitAt"] + ":00"])
        async with self.page.expect_response("**/reservation/reservationCheck.json") as pending:
            await self.page.click("#parkCheckBtn")
        return await self._read_code(await pending.value) == "00"

    async def _form(self):
        return await self.page.locator("#reservationVO").evaluate("""form => {
            const fields = {}; for (const [k,v] of new FormData(form)) (fields[k] ||= []).push(v); return fields;
        }""")

    async def prepare(self):
        async with self.page.expect_navigation(wait_until="load"):
            await self.page.click("#requestBtn")
        await self.page.locator("#carNo").wait_for(state="visible")
        await self.page.evaluate("() => new Promise(resolve => $(resolve))")
        await self.page.evaluate("""() => {
            window.__gimpoHandoffCancelled = false;
            const modal = document.getElementById('confirm');
            const cancel = () => {
                if (window.__gimpoHandoffCancelled) return;
                window.__gimpoHandoffCancelled = true;
                window.__gimpoCancel().catch(() => {});
            };
            document.addEventListener('click', event => {
                if (event.target.closest('#confirmNo, #confirm [data-dismiss="modal"]') || event.target === modal) cancel();
            }, true);
            let visible = false;
            new MutationObserver(() => {
                const current = getComputedStyle(modal).display !== 'none' && !modal.hidden;
                if (visible && !current) cancel();
                visible = current;
            }).observe(modal, {attributes: true, attributeFilter: ['class', 'style', 'hidden']});
            window.addEventListener('beforeunload', () => { if (visible) cancel(); });
        }""")
        summary = OfficialContract.summary(await self._form(), self.inputs)
        for selector, key in (("#carNo", "carNumber"), ("#mobile", "phone"),
                              ("#password", "reservationPassword"), ("#passwordCk", "reservationPassword")):
            await self.page.fill(selector, self.inputs[key])
        for name in AGREEMENTS:
            await self.page.locator("#" + name).evaluate("element => { element.checked = true; element.dispatchEvent(new Event('change', {bubbles: true})); }")
        return summary

    async def proceed(self):
        if self.sealed_form is not None:
            raise BrowserFault("이미 진행한 결제는 다시 요청할 수 없습니다.")
        OfficialContract.summary(await self._form(), self.inputs)
        self.allow_confirmation = True
        responses = asyncio.Queue()
        async def collect(response):
            path = urlparse(response.url).path
            if path in {"/reservation/duplicateReservation.json", "/reservation/reservationCheck.json"}:
                await responses.put(response)
        self.page.on("response", collect)
        try:
            await self.page.click("#reservationBtn")
            timeout = self.owner.config.browser_timeout_sec
            duplicate = await asyncio.wait_for(responses.get(), timeout)
            if not duplicate.url.endswith("duplicateReservation.json") and urlparse(duplicate.url).path != "/reservation/duplicateReservation.json":
                raise BrowserFault("중복 검사를 확인할 수 없습니다.")
            code = await self._read_code(duplicate, True)
            if code != "00":
                raise BrowserFault("같은 기간의 예약이 이미 있습니다." if code == "10" else "예약부도 이력으로 예약이 제한되었습니다.")
            final = await asyncio.wait_for(responses.get(), timeout)
            code = await self._read_code(final)
            checked_at = self.owner.store.clock()
            if code == "10":
                return False, checked_at
            await self.page.locator("#confirm").wait_for(state="visible")
            if self.dialog_error or "결제 하시겠습니까?" not in await self.page.locator("#confirmMassage").inner_text():
                raise BrowserFault("공식 예약내용 또는 결제 확인 문구가 일치하지 않습니다.")
            self.sealed_form = await self._form()
            OfficialContract.summary(self.sealed_form, self.inputs)
            return True, checked_at
        finally:
            self.allow_confirmation = False
            self.page.remove_listener("response", collect)

    async def alive(self):
        return not self.closed and self.page is not None and not self.page.is_closed() and self.browser.is_connected()

    async def inspect(self):
        if not await self.alive():
            return False
        if urlparse(self.page.url).path != "/reservation/resInsert.do":
            return False
        if await self.page.evaluate("Boolean(window.__gimpoHandoffCancelled)"):
            return False
        if not await self.page.locator("#confirm").is_visible():
            return False
        return bool(self.sealed_form) and await self._form() == self.sealed_form

    async def show(self):
        if not self.page or self.page.is_closed():
            raise BrowserFault("열려 있는 공항 예약창이 없습니다.", "SESSION_EXPIRED")
        await self.page.bring_to_front()

    async def close(self):
        self.closed = True
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
        finally:
            if self.playwright:
                await self.playwright.stop()
            self.inputs = {}
            self.sealed_form = None
