import asyncio
import html
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from services.gimpo.client import PlaywrightGimpoClient
from services.notifications.telegram import Delivery

FIXTURES = Path(__file__).parent / "fixtures"


class FakeFeeClient:
    """UI 테스트용 가짜 공항 요금 조회. 실제 네트워크를 부르지 않는다."""
    def __init__(self, calculate_amt=8000, discount_amt=1600, error=None):
        self.calculate_amt, self.discount_amt, self.error = calculate_amt, discount_amt, error
        self.calls = []

    def quote(self, *, parkingId, entryAt, exitAt, discountSelection):
        self.calls.append({"parkingId": parkingId, "entryAt": entryAt, "exitAt": exitAt, "discountSelection": discountSelection})
        if self.error:
            raise self.error
        return {"calculateAmt": self.calculate_amt, "discountAmt": self.discount_amt,
                "estimatedAmt": self.calculate_amt - self.discount_amt}


class FakeNotifier:
    enabled = True
    configured = True
    credentials_configured = True
    def __init__(self, results=None):
        self.sent = []
        self.results = list(results or [])
    def send(self, text, reply_to=None):
        self.sent.append((text, reply_to))
        return self.results.pop(0) if self.results else Delivery("SENT", message_id=len(self.sent))


class FakeBrowser:
    available = True
    final_available = True
    live = True
    def __init__(self, owner, job, inputs):
        self.owner, self.job, self.inputs = owner, job, inputs
        self.closed = False
        self.checks = 0
        self.prepares = 0
        self.proceeds = 0
        self.exit_at = inputs.get("exitAt")
        self.prepared_exit = None
        self.attempted_exits = []
    def _summary(self):
        return {"parkingName": "국내선 제2주차장 주차타워 2, 3층", "entryAt": self.inputs["entryAt"], "exitAt": self.exit_at,
                "calculateAmt": 8000, "depositAmt": 10000, "paymentAmt": 10000, "receiptAmt": -2000, "discountAmt": 0}
    async def check(self):
        self.checks += 1
        return self.available
    async def prepare(self, *, bootstrap=False, exit_at=None):
        self.bootstrap = bootstrap
        self.prepares += 1
        self.exit_at = self.prepared_exit = exit_at or self.inputs["exitAt"]
        return self._summary()
    async def proceed(self, exit_at=None):
        self.proceeds += 1
        self.exit_at = exit_at or self.exit_at
        self.attempted_exits.append(self.exit_at)
        return self.final_available, self.owner.store.clock(), self._summary()
    async def alive(self):
        return not self.closed
    async def inspect(self):
        return self.live and not self.closed
    async def close(self):
        self.closed = True
    async def show(self):
        pass
    async def closed_by_user(self):
        return False


class FixtureBrowser(PlaywrightGimpoClient):
    """Every network request is fulfilled locally, AFTER the production payment guard."""
    codes = ("00", "00")
    duplicate = "00"
    payment_amount = "10000"
    error_html = False
    quote_amount = "8000"
    completion_overrides = {}
    def __init__(self, owner, job, inputs):
        super().__init__(owner, job, inputs, headless=True)
        self.forwarded = []
        self.check_count = 0
        self.requests = []
        self.quote_requests = []
        self.availability_forms = []
        self.bootstrap_form = None
        self.pacing_stages = []

    async def _pace(self, stage):
        self.pacing_stages.append(stage)

    cdp_endpoint = None  # shared_chromium 픽스처가 채운다. 비어 있으면 운영과 같이 새로 띄운다.

    async def _launch(self, playwright):
        if self.cdp_endpoint:
            return await playwright.chromium.connect_over_cdp(self.cdp_endpoint)
        return await super()._launch(playwright)

    async def close(self):
        # 운영처럼 context를 먼저 닫되, 공유 Chromium에서는 남겨 둔 대화상자나 진행 중인 요청 때문에 닫기가
        # 멈출 수 있어 잠깐만 기다린 뒤 드라이버를 멈춰 연결을 끊는다(연결이 만든 context는 함께 치워진다).
        if self.cdp_endpoint:
            if self.context:
                try:
                    await asyncio.wait_for(self.context.close(), 1)
                except Exception:
                    pass
            self.context = self.browser = None
        await super().close()

    async def _start(self):
        if self.context:
            return
        await super()._start()
        await self.context.unroute("**/*", self._guard)
        await self.context.route("**/*", self._fixture)
        await self.context.route("**/*", self._guard)
    async def _fixture(self, route):
        path = urlparse(route.request.url).path
        self.requests.append((path, route.request.frame.url))
        if path == '/reservation/recheck.do':
            await route.fulfill(content_type='text/html', body=(FIXTURES / 'step1.html').read_text())
        elif path == '/reservation/resInsert.do':
            data = {k: v[0] for k,v in parse_qs(route.request.post_data).items()}
            fields = {"parkingDivCd": "PLT-002", "sectnId": "2", "parkingName": "국내선 제2주차장 주차타워 2, 3층", "airportNm": "김포공항",
                      "resInDttm": data['resInDttm'], "resOutDttm": data['resOutDttm'],
                      **json.loads((FIXTURES / 'contract.json').read_text())["amounts"], "paymentAmt": self.payment_amount}
            self.bootstrap_form = data
            inputs = ''.join(f'<input type="hidden" id="{k}" name="{k}" value="{html.escape(v)}">' for k,v in fields.items())
            await route.fulfill(content_type='text/html', body=(FIXTURES / 'step2.html').read_text().replace('__FIELDS__', inputs).replace('__BOOTSTRAP_IN__', data['resInDttm']).replace('__BOOTSTRAP_OUT__', data['resOutDttm']))
        elif path == '/main/calculateAmt.json':
            self.quote_requests.append(parse_qs(route.request.post_data))
            await route.fulfill(json={'calculateAmt': self.quote_amount})
        elif path == '/reservation/reservationCheck.json':
            self.availability_forms.append(parse_qs(route.request.post_data))
            code = self.codes[min(self.check_count, len(self.codes)-1)]
            self.check_count += 1
            if self.error_html:
                await route.fulfill(content_type='text/html', body='<html>session expired</html>')
            else:
                await route.fulfill(json={"result": {"code": code}})
        elif path == '/reservation/calculateDiscountAmt.json':
            code = parse_qs(urlparse(route.request.url).query)['discountCd'][0]
            await route.fulfill(json={'discountAmt': 4000 if code == 'DC005' else 1600})
        elif path == '/reservation/duplicateReservation.json':
            await route.fulfill(json={"code": self.duplicate})
        elif path in {'/reservation/payment.json', '/reservation/insertAction.do'}:
            self.forwarded.append(path)
            await route.fulfill(json={"fixture": "no real payment response"})
        elif path == '/reservation/resComplete.do':
            values = {'__ENTRY_AT__': self.inputs['entryAt'], '__EXIT_AT__': self.exit_at,
                      '__CAR_NUMBER__': self.inputs['carNumber'], '__RESERVATION_NO__': '1234AB5678', **self.completion_overrides}
            body = (FIXTURES / 'step3_complete.html').read_text(encoding='utf-8')
            for key, value in values.items():
                body = body.replace(key, value)
            await route.fulfill(content_type='text/html; charset=utf-8', body=body)
        elif path == '/reservation/resView.do':
            await route.fulfill(content_type='text/html; charset=utf-8', body='<html><body>예약 조회</body></html>')
        else:
            await route.abort()  # No network, including third-party assets or redirects.
