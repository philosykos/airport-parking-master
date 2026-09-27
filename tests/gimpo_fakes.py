import asyncio
import html
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from services.gimpo_client import PlaywrightGimpoClient
from services.telegram_notifier import Delivery

FIXTURES = Path(__file__).parent / "fixtures" / "gimpo"


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
    async def check(self):
        self.checks += 1
        return self.available
    async def prepare(self):
        self.prepares += 1
        return {"parkingName": "국내선 제2주차장 주차타워 2, 3층", "entryAt": self.inputs["entryAt"], "exitAt": self.inputs["exitAt"],
                "calculateAmt": 8000, "depositAmt": 10000, "paymentAmt": 10000, "receiptAmt": -2000, "discountAmt": 0}
    async def proceed(self):
        self.proceeds += 1
        return self.final_available, self.owner.store.clock()
    async def alive(self):
        return not self.closed
    async def inspect(self):
        return self.live and not self.closed
    async def close(self):
        self.closed = True
    async def show(self):
        pass


class FixtureBrowser(PlaywrightGimpoClient):
    """Every network request is fulfilled locally, AFTER the production payment guard."""
    codes = ("00", "00")
    duplicate = "00"
    payment_amount = "10000"
    error_html = False
    def __init__(self, owner, job, inputs):
        super().__init__(owner, job, inputs, headless=True)
        self.forwarded = []
        self.check_count = 0
    async def _start(self):
        if self.context:
            return
        await super()._start()
        await self.context.unroute("**/*", self._guard)
        await self.context.route("**/*", self._fixture)
        await self.context.route("**/*", self._guard)
    async def _fixture(self, route):
        path = urlparse(route.request.url).path
        if path == '/reservation/recheck.do':
            await route.fulfill(content_type='text/html', body=(FIXTURES / 'step1.html').read_text())
        elif path == '/reservation/resInsert.do':
            data = {k: v[0] for k,v in parse_qs(route.request.post_data).items()}
            fields = {"parkingDivCd": "PLT-002", "sectnId": "2", "parkingName": "국내선 제2주차장 주차타워 2, 3층", "airportNm": "김포공항",
                      "resInDttm": data['resInDttm'], "resOutDttm": data['resOutDttm'],
                      **json.loads((FIXTURES / 'contract.json').read_text())["amounts"], "paymentAmt": self.payment_amount}
            inputs = ''.join(f'<input type="hidden" id="{k}" name="{k}" value="{html.escape(v)}">' for k,v in fields.items())
            await route.fulfill(content_type='text/html', body=(FIXTURES / 'step2.html').read_text().replace('__FIELDS__', inputs))
        elif path == '/reservation/reservationCheck.json':
            code = self.codes[min(self.check_count, len(self.codes)-1)]
            self.check_count += 1
            if self.error_html:
                await route.fulfill(content_type='text/html', body='<html>session expired</html>')
            else:
                await route.fulfill(json={"result": {"code": code}})
        elif path == '/reservation/duplicateReservation.json':
            await route.fulfill(json={"code": self.duplicate})
        elif path in {'/reservation/payment.json', '/reservation/insertAction.do'}:
            self.forwarded.append(path)
            await route.fulfill(json={"fixture": "no real payment response"})
        else:
            await route.abort()  # No network, including third-party assets or redirects.
