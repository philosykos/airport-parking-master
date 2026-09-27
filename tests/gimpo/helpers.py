"""Reusable Gimpo inputs and state transitions; contains no collected tests."""
from datetime import datetime, timedelta

from services.gimpo.validation import AGREEMENTS, SEOUL, validate
from tests.support.waiting import eventually


NOW = datetime(2026, 9, 27, 10, 0, tzinfo=SEOUL)


def valid_input(now=NOW, mode="once"):
    start = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return {"airportCode": "PLT-002", "parkingId": "2", "entryAt": start.strftime("%Y-%m-%d %H:%M"),
            "exitAt": (start + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M"), "carNumber": "123가4567", "phone": "01012345678",
            "reservationPassword": "PrivatePass44", "passwordConfirmation": "PrivatePass44", "discountSelection": "DC001",
            "agreements": {k: True for k in AGREEMENTS}, "intervalSeconds": 30, "mode": mode, "autoProceedConsent": True}


def inputs(mode='watch'):
    return validate(valid_input(datetime.now(SEOUL), mode))


def wait_state(runtime, job_id, state):
    return eventually(lambda: job if (job := runtime.store.get(job_id))['state'] == state else None)


def ready_job(store):
    job = store.create(validate(valid_input(), now=NOW), 'run1')
    store.transition(job['id'], 'RECHECKING', 'test')
    return store.ready(job['id'], 1, {'parkingName':'fixture', 'entryAt':'date', 'exitAt':'date', 'calculateAmt':8000, 'depositAmt':10000}, 1000, 120)
