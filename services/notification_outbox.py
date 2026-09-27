"""Serialized notification worker, independent of the browser event loop."""
import threading
from services.notification_messages import ReservationMessages
from services.telegram_notifier import DeliveryPolicy


class NotificationOutbox:
    def __init__(self, store, notifier, validate_ready, max_attempts=3):
        self.store, self.notifier = store, notifier
        self.validate_ready = validate_ready
        self.delivery_policy = DeliveryPolicy(max_attempts)
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(target=self._run, name="gimpo-notifications", daemon=True)
            self.thread.start()

    def status(self):
        events = self.store.events()
        return {"enabled": self.notifier.enabled, "configured": self.notifier.configured,
                "lastDelivery": events[-1] if events else None}

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.deliver_one()
            except Exception:
                # Raw transport exceptions can contain tokens; never log them.
                pass
            self.stop_event.wait(0.25)

    def deliver_one(self):
        for event in self.store.events():
            if event["status"] not in {"PENDING", "RETRYING"} or event["nextAt"] > self.store.clock():
                continue
            if event["kind"] == "READY" and not self.validate_ready(event):
                # Browser owner handles cancellation/expiry. No notification on timeout.
                continue
            claimed = self.store.claim_event(event["id"])
            if not claimed:
                continue
            job = self.store.get(claimed["jobId"])
            try:
                result = self.notifier.send(self.message(claimed, job), claimed["replyTo"])
            except Exception:
                self.store.finish_event(claimed["id"], "UNKNOWN", error="알림 전송 결과를 확인할 수 없습니다.")
                return
            status, delay = self.delivery_policy.decide(result, claimed["attempts"])
            self.store.finish_event(claimed["id"], status, message_id=result.message_id, error=result.error, delay=delay)
            return

    @staticmethod
    def message(event, job):
        return ReservationMessages.gimpo(event, job).render()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)
