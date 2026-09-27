"""Playwright adapter. All browser objects belong to the runtime's event loop."""
import asyncio
import re
import time
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

from services.gimpo.validation import AGREEMENTS, AIRPORT, PARKING, PARKING_NAME, SEOUL
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
    async def prepare(self, *, bootstrap=False) -> dict: ...
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
                or amounts["calculateAmt"] < 0
                or not 0 <= amounts["discountAmt"] <= amounts["calculateAmt"]
                or (inputs["discountSelection"] == "DC001" and amounts["discountAmt"] != 0)):
            raise BrowserFault("보증금 또는 할인 요금이 예상과 달라 직접 확인이 필요합니다.")
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
        self.check_page_ready = False

    async def _start(self):
        if self.context:
            if not await self.alive():
                raise BrowserFault("공식 브라우저가 종료되었습니다. 다시 조회해주세요.", "SESSION_EXPIRED")
            return
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", service_workers="block")
        await Stealth(navigator_languages_override=("ko-KR", "ko")).apply_stealth_async(self.context)
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
                    raise Conflict("결제 정보가 변경되었습니다. 다시 조회해주세요.")
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
        if job["paymentMayHaveBeenSent"] or not self.allow_confirmation:
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

    async def _load_search_form(self, entry_at=None, exit_at=None):
        await self._start()
        job = self.owner.store.get(self.job_id)
        if job["paymentMayHaveBeenSent"] or self.sealed_form is not None:
            raise BrowserFault("결제 확인 단계에서는 새 조회를 시작할 수 없습니다.")
        self.version = {k: job[k] for k in ("inputVersion", "generation")}
        self.dialog_error = False
        self.allow_confirmation = False
        if not self.check_page_ready or self.page.url != START_URL:
            self.check_page_ready = False
            response = await self.page.goto(START_URL, wait_until="load")
            if response.status != 200:
                raise BrowserFault("공항 시작 화면을 열 수 없습니다.", "ERROR")
            await self.page.wait_for_function("typeof rescheck === 'function'")
            await self.page.evaluate("() => new Promise(resolve => $(resolve))")
            self.check_page_ready = True
        # Reuse the loaded search form, restoring the requested values if the
        # visible browser was edited between polling attempts.
        if await self.page.locator("#parkingDivCd").input_value() != AIRPORT:
            await self.page.select_option("#parkingDivCd", AIRPORT)
        await self.page.wait_for_function("Array.from(document.querySelectorAll('#parkingNm option')).some(o => o.value === '2')")
        name = await self.page.locator('#parkingNm option[value="2"]').text_content()
        if name.strip() != PARKING_NAME:
            raise BrowserFault("공식 주차장 선택 목록이 변경되었습니다.")
        if await self.page.locator("#parkingNm").input_value() != PARKING:
            await self.page.select_option("#parkingNm", PARKING)
        # Official date widgets are readonly: update displayed values in this tab.
        await self.page.evaluate("([a,b]) => {$('#resInDttm').val(a); $('#resOutDttm').val(b)}",
                                 [(entry_at or self.inputs["entryAt"]) + ":00", (exit_at or self.inputs["exitAt"]) + ":00"])

    async def check(self):
        await self._load_search_form()
        return await self._check_loaded_search_form()

    async def _check_loaded_search_form(self):
        async with self.page.expect_response("**/reservation/reservationCheck.json") as pending:
            await self.page.click("#parkCheckBtn")
        return await self._read_code(await pending.value) == "00"

    async def _form(self):
        return await self.page.locator("#reservationVO").evaluate("""form => {
            const fields = {}; for (const [k,v] of new FormData(form)) (fields[k] ||= []).push(v); return fields;
        }""")

    @staticmethod
    def _bootstrap_windows():
        start = (datetime.now(SEOUL) + timedelta(days=14)).replace(hour=10, minute=0, second=0, microsecond=0)
        windows = []
        while len(windows) < 5:
            if start.weekday() < 5:
                windows.append((start.strftime("%Y-%m-%d %H:%M"),
                                (start + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M")))
            start += timedelta(days=1)
        return windows

    async def _find_application_window(self):
        for index, (entry, end) in enumerate(self._bootstrap_windows()):
            if index:
                await asyncio.sleep(self.inputs["intervalSeconds"])
            await self._load_search_form(entry, end)
            if await self._check_loaded_search_form():
                return entry, end
        raise BrowserFault("예약신청 화면 진입용 평일 시간대를 찾지 못했습니다. 잠시 후 다시 시작해주세요.")

    async def _apply_requested_dates(self, bootstrap):
        # Obtain the requested period's price from the official calculator, not
        # from the short bootstrap stay rendered into the application page.
        quote = await self.page.evaluate("""async values => {
            const response = await fetch('/main/calculateAmt.json', {
                method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                body: new URLSearchParams(values)
            });
            return {status: response.status, data: await response.json()};
        }""", {"sectnId": self.inputs["parkingId"], "inDttm": self.inputs["entryAt"] + ":00",
                "outDttm": self.inputs["exitAt"] + ":00", "discountCd": "DC001"})
        data = quote.get("data")
        amount = data.get("calculateAmt") if isinstance(data, dict) else None
        if quote.get("status") != 200 or not re.fullmatch(r"[0-9]{1,10}", str(amount)):
            raise BrowserFault("실제 예약 기간의 공식 예상요금을 확인할 수 없습니다.")
        await self.page.evaluate("""([oldStart, oldEnd, start, end, amount]) => {
            if (typeof settingAmt !== 'function') throw new Error('Official amount calculator missing');
            const form = document.getElementById('reservationVO');
            form.querySelector('#resInDttm').value = start;
            form.querySelector('#resOutDttm').value = end;
            form.querySelector('#calculateAmt').value = String(amount);
            form.querySelector('#discountAmt').value = '0';
            settingAmt(amount);
            // The official page embeds the bootstrap dates in display text and
            // its native confirmation message. Keep both consistent with the form.
            const replaceDates = text => text.split(oldStart).map(part => part.split(oldEnd).join(end)).join(start);
            const walker = document.createTreeWalker(form, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const node = walker.currentNode;
                if (!node.parentElement.closest('script, style')) node.textContent = replaceDates(node.textContent);
            }
            const nativeConfirm = window.confirm.bind(window);
            window.confirm = message => nativeConfirm(
                typeof message === 'string' && message.startsWith('작성 내용을 다시 한번 확인해주세요.')
                    ? replaceDates(message) : message);
        }""", [bootstrap[0] + ":00", bootstrap[1] + ":00", self.inputs["entryAt"] + ":00",
                self.inputs["exitAt"] + ":00", int(amount)])

    async def prepare(self, *, bootstrap=False):
        entry_window = await self._find_application_window() if bootstrap else None
        async with self.page.expect_navigation(wait_until="load") as pending:
            await self.page.click("#requestBtn")
        response = await pending.value
        if response is None or response.status != 200 or urlparse(self.page.url).path != "/reservation/resInsert.do":
            raise BrowserFault("공식 예약신청 화면에 진입하지 못했습니다.", "SESSION_EXPIRED")
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
        # Check the server-rendered bootstrap identity before replacing the dates.
        initial = {**self.inputs, "discountSelection": "DC001"}
        if entry_window:
            initial.update(entryAt=entry_window[0], exitAt=entry_window[1])
        OfficialContract.summary(await self._form(), initial)
        if entry_window:
            await self._apply_requested_dates(entry_window)
        OfficialContract.summary(await self._form(), {**self.inputs, "discountSelection": "DC001"})
        if self.inputs["discountSelection"] != "DC001":
            async with self.page.expect_response("**/reservation/calculateDiscountAmt.json?*") as pending:
                await self.page.select_option("#discountCd", self.inputs["discountSelection"])
            response = await pending.value
            if not response.ok:
                raise BrowserFault("공식 할인 요금을 조회할 수 없습니다.")
            try:
                amount = (await response.json())["discountAmt"]
                if not re.fullmatch(r"[0-9]{1,10}", str(amount)):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise BrowserFault("공식 할인 요금 응답을 확인할 수 없습니다.") from None
            await self.page.wait_for_function(
                "amount => document.getElementById('discountAmt').value === String(amount)", arg=amount)
        summary = OfficialContract.summary(await self._form(), self.inputs)
        for selector, key in (("#carNo", "carNumber"), ("#mobile", "phone"),
                              ("#password", "reservationPassword"), ("#passwordCk", "reservationPassword")):
            await self.page.fill(selector, self.inputs[key])
        for name in AGREEMENTS:
            await self.page.locator("#" + name).evaluate("element => { element.checked = true; element.dispatchEvent(new Event('change', {bubbles: true})); }")
        if self.inputs["mode"] == "watch":
            # Keep manual clicks from creating a second, untracked request chain.
            await self.page.evaluate("""() => {
                const button = document.querySelector('#reservationBtn');
                button.disabled = true;
                button.textContent = '자동 예약 진행 중';
            }""")
        return summary

    async def proceed(self):
        if self.sealed_form is not None:
            raise BrowserFault("이미 진행한 결제는 다시 요청할 수 없습니다.")
        if not await self.alive() or urlparse(self.page.url).path != "/reservation/resInsert.do":
            raise BrowserFault("예약신청 화면이 닫혔거나 변경되었습니다.", "SESSION_EXPIRED")
        OfficialContract.summary(await self._form(), self.inputs)
        self.dialog_error = False
        self.allow_confirmation = True
        responses = {path: asyncio.Queue() for path in ("/reservation/duplicateReservation.json", "/reservation/reservationCheck.json")}
        async def collect(response):
            path = urlparse(response.url).path
            if path in responses and response.request.frame == self.page.main_frame:
                await responses[path].put(response)
        self.page.on("response", collect)
        try:
            notice = self.page.locator("#flashMessage")
            if await notice.is_visible():
                message = await notice.locator("#alertMassage").inner_text()
                if not re.fullmatch(r"예약가능\s*주차면수\s*:\s*0", message.strip()):
                    raise BrowserFault("공식 만차 안내 문구가 변경되었습니다.")
                await notice.locator("#flashMessageClose").click()
                await notice.wait_for(state="hidden")
            # Dispatch exactly one owned click; the visible control remains locked.
            await self.page.locator("#reservationBtn").evaluate(
                "button => button.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))")
            timeout = self.owner.config.browser_timeout_sec
            duplicate = await asyncio.wait_for(responses["/reservation/duplicateReservation.json"].get(), timeout)
            if not duplicate.url.endswith("duplicateReservation.json") and urlparse(duplicate.url).path != "/reservation/duplicateReservation.json":
                raise BrowserFault("중복 검사를 확인할 수 없습니다.")
            code = await self._read_code(duplicate, True)
            if code != "00":
                raise BrowserFault("같은 기간의 예약이 이미 있습니다." if code == "10" else "예약부도 이력으로 예약이 제한되었습니다.")
            final = await asyncio.wait_for(responses["/reservation/reservationCheck.json"].get(), timeout)
            if urlparse(final.url).path != "/reservation/reservationCheck.json":
                raise BrowserFault("신청 화면의 잔여석 검사를 확인할 수 없습니다.")
            code = await self._read_code(final)
            checked_at = self.owner.store.clock()
            if code == "10":
                # Only dismiss the known full-parking notice. Other alerts require review.
                notice = self.page.locator("#flashMessage")
                await notice.wait_for(state="visible")
                message = await notice.locator("#alertMassage").inner_text()
                if not re.fullmatch(r"예약가능\s*주차면수\s*:\s*0", message.strip()):
                    raise BrowserFault("공식 만차 안내 문구가 변경되었습니다.")
                # Leave the result visible until the next scheduled attempt.
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
