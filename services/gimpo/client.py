"""Playwright adapter. All browser objects belong to the runtime's event loop."""
import asyncio
import re
import random
import time
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

from services.gimpo.validation import AGREEMENTS, AIRPORT, PARKING, PARKING_NAME, SEOUL
from services.gimpo.store import Conflict, PAYMENT_STATES, READY
from services.gimpo.watch import jittered

ORIGIN = "https://park.airport.co.kr"
START_URL = ORIGIN + "/reservation/recheck.do"
PAYMENT_PATH = "/reservation/payment.json"
COMPLETE_PATH = "/reservation/resComplete.do"
# 완료 화면의 완료 문구와 모바일 예약 내역 표(PC 화면에도 문서에 있다)의 라벨·값 쌍을 읽는다.
COMPLETION_SCRIPT = """() => ({
    message: (document.querySelector('.complete p')?.textContent || '').trim(),
    fields: Object.fromEntries([...document.querySelectorAll('div.table.mobile th[scope=row]')]
        .map(th => [th.textContent.trim(), (th.nextElementSibling?.textContent || '').trim()]))
})"""
RESERVATION_NO = re.compile(r"[A-Z0-9]{6,20}")
# 확인창 문구 보정: 공식 확인창에 표시되는 신청 화면 진입 시점의 기간을 현재 폼 기간으로 치환하고,
# 자동 확인 모드에서는 기대 문구와 대조한다. 입차·출차는 단일 패스로 치환해 연쇄 치환을 방지한다.
CONFIRM_SCRIPT = """pageDates => {
    const originalConfirm = window.confirm.bind(window);
    window.__gimpoReplaceDates = (text, [from1, from2], [to1, to2]) =>
        text.split(from1).map(part => part.split(from2).join(to2)).join(to1);
    window.__gimpoPageDates = pageDates;
    window.__gimpoFormDates = pageDates;
    window.__gimpoExpected = [];
    window.__gimpoAutomaticConfirmation = false;
    window.__gimpoConfirmationMismatch = false;
    window.confirm = message => {
        if (typeof message === 'string' && message.startsWith('작성 내용을 다시 한번 확인해주세요.'))
            message = window.__gimpoReplaceDates(message, window.__gimpoPageDates, window.__gimpoFormDates);
        if (!window.__gimpoAutomaticConfirmation) return originalConfirm(message);
        const valid = typeof message === 'string'
            && message.startsWith('작성 내용을 다시 한번 확인해주세요.')
            && window.__gimpoExpected.every(value => message.includes(value));
        if (!valid) window.__gimpoConfirmationMismatch = true;
        return valid;
    };
}"""
SET_PERIOD_SCRIPT = """([oldStart, oldEnd, start, end, amount]) => {
    if (typeof settingAmt !== 'function') throw new Error('Official amount calculator missing');
    const form = document.getElementById('reservationVO');
    form.querySelector('#resInDttm').value = start;
    form.querySelector('#resOutDttm').value = end;
    form.querySelector('#calculateAmt').value = String(amount);
    form.querySelector('#discountAmt').value = '0';
    settingAmt(amount);
    const walker = document.createTreeWalker(form, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
        const node = walker.currentNode;
        if (!node.parentElement.closest('script, style'))
            node.textContent = window.__gimpoReplaceDates(node.textContent, [oldStart, oldEnd], [start, end]);
    }
    window.__gimpoFormDates = [start, end];
}"""
# 캐시된 할인액 적용: 공식 할인 응답 처리와 동일하게 할인액을 설정하고 공식 discountReqAmt를 호출해
# 폼 금액 필드와 화면 요금 표시를 settingAmt(요금 − 할인액) 기준으로 함께 갱신한다.
CACHED_DISCOUNT_SCRIPT = """amount => {
    if (typeof discountReqAmt !== 'function') throw new Error('Official discount handler missing');
    document.getElementById('discountAmt').value = String(amount);
    discountReqAmt();
}"""


class BrowserFault(Exception):
    def __init__(self, message, state="REVIEW_REQUIRED", retryable=False):
        super().__init__(message)
        self.state = state
        self.retryable = retryable


def check_status(status, otherwise):
    """공항 응답 상태 분류. 401·403·429는 중단 오류, 5xx는 재시도 오류, 그 밖의 비정상 응답은 otherwise를 발생시킨다."""
    if status in {401, 403, 429}:
        raise BrowserFault("공항 사이트에서 접근 또는 조회를 제한했습니다.", "ERROR")
    if status >= 500:
        raise BrowserFault("공항 서버 응답이 지연되고 있습니다.", "ERROR", retryable=True)
    if status != 200:
        raise otherwise


class BrowserClient(Protocol):
    async def check(self) -> bool: ...
    async def prepare(self, *, bootstrap=False, exit_at=None) -> dict: ...
    async def proceed(self, exit_at=None) -> tuple[bool, float, dict]: ...
    async def alive(self) -> bool: ...
    async def inspect(self) -> bool: ...
    async def show(self) -> None: ...
    async def close(self) -> None: ...
    async def closed_by_user(self) -> bool: ...


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

    @staticmethod
    def completion(page_data, inputs):
        """공항 예약확인 화면이 이 작업의 결제 완료를 보여 줄 때만 예약번호를 돌려준다."""
        fields = page_data.get("fields") or {}
        number = fields.get("예약번호", "")
        expected = {"예약상태": "결제완료", "차량입차": inputs["entryAt"] + ":00", "차량출차": inputs["exitAt"] + ":00",
                    "주차장": "김포공항 " + PARKING_NAME, "차량번호": inputs["carNumber"]}
        if ("예약이 완료되었습니다" not in page_data.get("message", "")
                or not RESERVATION_NO.fullmatch(number)
                or any(fields.get(k) != v for k, v in expected.items())):
            raise BrowserFault("예약확인 화면을 확인하지 못했습니다.")
        return number


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
        self.form_period = (inputs.get("entryAt"), inputs.get("exitAt"))
        self.quotes = {}

    @property
    def exit_at(self):
        return self.form_period[1]

    def _period_inputs(self):
        """현재 폼 기간을 반영한 입력값. 폼 대조·확인창 검증·완료 판정의 기준으로 사용한다."""
        return {**self.inputs, "entryAt": self.form_period[0], "exitAt": self.form_period[1]}

    def _expected_confirmation(self):
        current = self._period_inputs()
        return [current["entryAt"] + ":00", current["exitAt"] + ":00", PARKING_NAME, current["carNumber"], current["phone"]]

    async def _sync_expected(self):
        await self.page.evaluate("values => { window.__gimpoExpected = values; }", self._expected_confirmation())

    async def _pace(self, stage):
        # Cancellable pauses between actions; never shorten the polling interval
        # or block the runtime loop that processes stop/payment commands.
        bounds = {"page": (1.5, 2.5), "transition": (2.0, 3.5),
                  "field": (0.4, 0.9), "submit": (1.0, 1.8)}
        await asyncio.sleep(random.uniform(*bounds[stage]))

    async def _start(self):
        if self.context:
            if not await self.alive():
                raise BrowserFault("공식 브라우저가 종료되었습니다. 다시 조회해주세요.", "SESSION_EXPIRED", retryable=True)
            return
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
        self.playwright = await async_playwright().start()
        self.browser = await self._launch(self.playwright)
        self.context = await self.browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", service_workers="block")
        await Stealth(navigator_languages_override=("ko-KR", "ko")).apply_stealth_async(self.context)
        self.context.set_default_timeout(self.owner.config.browser_timeout_sec * 1000)
        await self.context.expose_binding("__gimpoCancel", self._cancel_signal)
        await self.context.route("**/*", self._guard)
        self.context.on("page", self._on_page)
        self.page = await self.context.new_page()

    async def _launch(self, playwright):
        # 테스트는 이 지점만 바꿔 이미 떠 있는 브라우저에 붙는다(작업마다 Chromium을 새로 띄우지 않는다).
        return await playwright.chromium.launch(headless=self.headless)

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
        target = urlparse(frame.url)
        if target.scheme != "https":
            return
        if (page is self.page and target.hostname == "park.airport.co.kr" and target.path == COMPLETE_PATH
                and job["paymentMayHaveBeenSent"] and job["state"] in PAYMENT_STATES):
            # 콜백은 동기라 기다리지 않고, 문서를 다 읽은 뒤 판정하도록 예약만 한다.
            self.owner.loop.create_task(self._verify_completion())
            return
        if job["paymentMayHaveBeenSent"] and target.hostname != "park.airport.co.kr":
            self.progress_seen = True
            self._mark_progress()
        elif (target.hostname == "park.airport.co.kr" and job["state"] == "PAYMENT_IN_PROGRESS"
              and not job.get("returnedFromPayment")):
            self.owner.store.mark_returned(self.job_id)

    def _mark_progress(self):
        if self.progress_seen and self.payment_response_ok:
            try:
                self.owner.store.transition(self.job_id, "PAYMENT_IN_PROGRESS", "공항 결제창에서 결제를 진행하고 있습니다.",
                                            expected={"PAYMENT_DISPATCHING"})
            except Conflict:
                pass

    async def _verify_completion(self):
        try:
            await self.page.wait_for_load_state("load")
            number = OfficialContract.completion(await self.page.evaluate(COMPLETION_SCRIPT), self._period_inputs())
        except asyncio.CancelledError:
            raise
        except Exception:
            self.owner.completion_unverified(self.job_id)
            return
        self.owner.reserved(self.job_id, number)

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
        values = tuple(self._expected_confirmation())
        if (self.allow_confirmation and dialog.type == "confirm"
                and dialog.message.startswith("작성 내용을 다시 한번 확인해주세요.")
                and all(v in dialog.message for v in values)):
            await dialog.accept()
        else:
            self.dialog_error = True
            await dialog.dismiss()

    async def _read_code(self, response, duplicate=False):
        check_status(response.status, BrowserFault("공항 세션 또는 화면을 확인할 수 없습니다.", "SESSION_EXPIRED", retryable=True))
        try:
            data = await response.json()
        except Exception:
            raise BrowserFault("정상 조회 대신 오류 화면을 받았습니다.", "SESSION_EXPIRED", retryable=True) from None
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
            check_status(response.status, BrowserFault("공항 시작 화면을 열 수 없습니다.", "ERROR"))
            await self.page.wait_for_function("typeof rescheck === 'function'")
            await self.page.evaluate("() => new Promise(resolve => $(resolve))")
            self.check_page_ready = True
            await self._pace("page")
        # Reuse the loaded search form, restoring the requested values if the
        # visible browser was edited between polling attempts.
        if await self.page.locator("#parkingDivCd").input_value() != AIRPORT:
            await self.page.select_option("#parkingDivCd", AIRPORT)
            await self._pace("field")
        await self.page.wait_for_function("Array.from(document.querySelectorAll('#parkingNm option')).some(o => o.value === '2')")
        name = await self.page.locator('#parkingNm option[value="2"]').text_content()
        if name.strip() != PARKING_NAME:
            raise BrowserFault("공식 주차장 선택 목록이 변경되었습니다.")
        if await self.page.locator("#parkingNm").input_value() != PARKING:
            await self.page.select_option("#parkingNm", PARKING)
            await self._pace("field")
        # Official date widgets are readonly: update displayed values in this tab.
        await self.page.evaluate("([a,b]) => {$('#resInDttm').val(a); $('#resOutDttm').val(b)}",
                                 [(entry_at or self.inputs["entryAt"]) + ":00", (exit_at or self.inputs["exitAt"]) + ":00"])

    async def check(self):
        await self._load_search_form()
        return await self._check_loaded_search_form()

    async def _check_loaded_search_form(self):
        await self._pace("submit")
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
                await self.owner.sleep(jittered(self.inputs["intervalSeconds"], self.owner.random))
            await self._load_search_form(entry, end)
            if await self._check_loaded_search_form():
                return entry, end
        raise BrowserFault("예약신청 화면 진입용 평일 시간대를 찾지 못했습니다. 잠시 후 다시 시작해주세요.", retryable=True)

    async def _quote(self, entry_at, exit_at):
        # Obtain the requested period's price from the official calculator, not
        # from the short bootstrap stay rendered into the application page.
        quote = await self.page.evaluate("""async values => {
            const response = await fetch('/main/calculateAmt.json', {
                method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                body: new URLSearchParams(values)
            });
            // 본문이 JSON이 아닌 경우(오류 화면)에도 상태 코드와 파싱 여부를 반환해 오류를 분류한다.
            const text = await response.text();
            try { return {status: response.status, json: true, data: JSON.parse(text)}; }
            catch (error) { return {status: response.status, json: false, data: null}; }
        }""", {"sectnId": self.inputs["parkingId"], "inDttm": entry_at + ":00",
                "outDttm": exit_at + ":00", "discountCd": "DC001"})
        unknown = BrowserFault("실제 예약 기간의 공식 예상요금을 확인할 수 없습니다.")
        check_status(quote.get("status"), unknown)
        if not quote.get("json"):
            raise BrowserFault("정상 조회 대신 오류 화면을 받았습니다.", "SESSION_EXPIRED", retryable=True)
        data = quote.get("data")
        amount = data.get("calculateAmt") if isinstance(data, dict) else None
        if not re.fullmatch(r"[0-9]{1,10}", str(amount)):
            raise unknown
        return int(amount)

    async def _set_period(self, entry_at, exit_at, amount):
        old = self.form_period
        await self.page.evaluate(SET_PERIOD_SCRIPT, [old[0] + ":00", old[1] + ":00", entry_at + ":00", exit_at + ":00", int(amount)])
        self.form_period = (entry_at, exit_at)
        await self._sync_expected()

    async def _apply_discount(self, *, select):
        """공식 할인 처리를 통해 할인액을 조회·적용한다. select가 거짓이면 선택된 할인을 현재 기간으로 재계산한다."""
        async with self.page.expect_response("**/reservation/calculateDiscountAmt.json?*") as pending:
            if select:
                await self.page.select_option("#discountCd", self.inputs["discountSelection"])
            else:
                await self.page.locator("#discountCd").dispatch_event("change")
        response = await pending.value
        check_status(response.status, BrowserFault("공식 할인 요금을 조회할 수 없습니다."))
        try:
            amount = (await response.json())["discountAmt"]
            if not re.fullmatch(r"[0-9]{1,10}", str(amount)):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise BrowserFault("공식 할인 요금 응답을 확인할 수 없습니다.") from None
        await self.page.wait_for_function(
            "amount => document.getElementById('discountAmt').value === String(amount)", arg=amount)
        return int(amount)

    async def _switch_exit(self, exit_at):
        """예약신청 화면의 출차를 지정 후보로 전환한다. 후보별 요금·할인액은 최초 1회 조회 후 캐시를 사용한다."""
        if exit_at == self.exit_at:
            return
        await self._pace("field")
        entry_at = self.inputs["entryAt"]
        cached = self.quotes.get(exit_at)
        amount = cached["calculateAmt"] if cached else await self._quote(entry_at, exit_at)
        await self._set_period(entry_at, exit_at, amount)
        discount = 0
        if self.inputs["discountSelection"] != "DC001":
            if cached:
                discount = cached["discountAmt"]
                await self.page.evaluate(CACHED_DISCOUNT_SCRIPT, discount)
            else:
                discount = await self._apply_discount(select=False)
        self.quotes[exit_at] = {"calculateAmt": int(amount), "discountAmt": discount}

    async def prepare(self, *, bootstrap=False, exit_at=None):
        exit_at = exit_at or self.inputs["exitAt"]
        entry_window = await self._find_application_window() if bootstrap else None
        await self.page.locator("#requestBtn").wait_for(state="visible")
        await self._pace("transition")
        async with self.page.expect_navigation(wait_until="load") as pending:
            await self.page.click("#requestBtn")
        response = await pending.value
        if response is None or response.status != 200 or urlparse(self.page.url).path != "/reservation/resInsert.do":
            raise BrowserFault("공식 예약신청 화면에 진입하지 못했습니다.", "SESSION_EXPIRED", retryable=True)
        await self.page.locator("#carNo").wait_for(state="visible")
        await self._pace("page")
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
        # 공항이 렌더링한 신청 화면 기간: 부트스트랩 진입 시 진입용 단기 기간, 그 외에는 요청 기간.
        self.form_period = entry_window or (self.inputs["entryAt"], self.inputs["exitAt"])
        self.quotes = {}
        # Native confirm dialogs can activate the OS window even when immediately
        # accepted by Playwright. Handle only our validated automatic confirmation
        # in-page; manual confirmations and payment dialogs retain native behavior.
        await self.page.evaluate(CONFIRM_SCRIPT, [self.form_period[0] + ":00", self.form_period[1] + ":00"])
        await self._sync_expected()
        # Check the server-rendered identity before replacing the dates.
        OfficialContract.summary(await self._form(), {**self._period_inputs(), "discountSelection": "DC001"})
        if self.form_period != (self.inputs["entryAt"], exit_at):
            await self._set_period(self.inputs["entryAt"], exit_at, await self._quote(self.inputs["entryAt"], exit_at))
        OfficialContract.summary(await self._form(), {**self._period_inputs(), "discountSelection": "DC001"})
        discount = 0
        if self.inputs["discountSelection"] != "DC001":
            await self._pace("field")
            discount = await self._apply_discount(select=True)
        self.quotes[exit_at] = {"calculateAmt": int((await self._form())["calculateAmt"][0]), "discountAmt": discount}
        summary = OfficialContract.summary(await self._form(), self._period_inputs())
        for selector, key in (("#carNo", "carNumber"), ("#mobile", "phone"),
                              ("#password", "reservationPassword"), ("#passwordCk", "reservationPassword")):
            await self._pace("field")
            await self.page.fill(selector, self.inputs[key])
        for name in AGREEMENTS:
            await self._pace("field")
            await self.page.locator("#" + name).evaluate("element => { element.checked = true; element.dispatchEvent(new Event('change', {bubbles: true})); }")
        if self.inputs["mode"] == "watch":
            # Keep manual clicks from creating a second, untracked request chain.
            await self.page.evaluate("""() => {
                const button = document.querySelector('#reservationBtn');
                button.disabled = true;
                button.textContent = '자동 예약 진행 중';
            }""")
        return summary

    async def proceed(self, exit_at=None):
        if self.sealed_form is not None:
            raise BrowserFault("이미 진행한 결제는 다시 요청할 수 없습니다.")
        if not await self.alive() or urlparse(self.page.url).path != "/reservation/resInsert.do":
            raise BrowserFault("예약신청 화면이 닫혔거나 변경되었습니다.", "SESSION_EXPIRED", retryable=True)
        await self._switch_exit(exit_at or self.exit_at)
        summary = OfficialContract.summary(await self._form(), self._period_inputs())
        self.dialog_error = False
        self.allow_confirmation = True
        responses = {path: asyncio.Queue() for path in ("/reservation/duplicateReservation.json", "/reservation/reservationCheck.json")}
        async def collect(response):
            path = urlparse(response.url).path
            if path in responses and response.request.frame == self.page.main_frame:
                await responses[path].put(response)
        self.page.on("response", collect)
        try:
            await self.page.evaluate("""() => {
                window.__gimpoAutomaticConfirmation = true;
                window.__gimpoConfirmationMismatch = false;
            }""")
            notice = self.page.locator("#flashMessage")
            if await notice.is_visible():
                message = await notice.locator("#alertMassage").inner_text()
                if not re.fullmatch(r"예약가능\s*주차면수\s*:\s*0", message.strip()):
                    raise BrowserFault("공식 만차 안내 문구가 변경되었습니다.")
                await notice.locator("#flashMessageClose").evaluate("button => button.click()")
                await notice.wait_for(state="hidden")
            await self._pace("submit")
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
                return False, checked_at, summary
            await self.page.locator("#confirm").wait_for(state="visible")
            if self.dialog_error or "결제 하시겠습니까?" not in await self.page.locator("#confirmMassage").inner_text():
                raise BrowserFault("공식 예약내용 또는 결제 확인 문구가 일치하지 않습니다.")
            self.sealed_form = await self._form()
            summary = OfficialContract.summary(self.sealed_form, self._period_inputs())
            return True, checked_at, summary
        finally:
            self.allow_confirmation = False
            self.page.remove_listener("response", collect)
            if not self.page.is_closed():
                await self.page.evaluate("window.__gimpoAutomaticConfirmation = false")

    async def alive(self):
        return not self.closed and self.page is not None and not self.page.is_closed() and self.browser.is_connected()

    async def closed_by_user(self):
        # 예약창만 닫히고 브라우저는 연결되어 있으면 사용자가 창을 닫은 것으로 본다.
        return (not self.closed and self.page is not None and self.page.is_closed()
                and self.browser is not None and self.browser.is_connected())

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
