"""Reusable, bounded background queue for notifications without browser handoff state."""
import atexit
import queue
import threading
import uuid
from collections import OrderedDict

from services.notifications.telegram import Delivery, DeliveryPolicy, Notifier


class BackgroundNotifications:
    def __init__(self, notifier: Notifier, max_attempts=3, on_result=None):
        self.notifier = notifier
        self.policy = DeliveryPolicy(max_attempts)
        self.on_result = on_result
        self.pending = queue.Queue(maxsize=64)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.results = OrderedDict()

    def publish(self, event_id, message):
        with self.lock:
            if event_id in self.results:
                return dict(self.results[event_id])
            record = {"eventId": event_id, "status": "PENDING", "error": None}
            self.results[event_id] = record
            self._trim()
            if not self.notifier.enabled:
                record["status"] = "DISABLED"
                return dict(record)
            if not self.notifier.configured:
                record.update(status="FAILED", error="봇 토큰과 수신자 ID를 설정해주세요.")
                return dict(record)
            if self.stop_event.is_set():
                record.update(status="FAILED", error="앱이 종료 중이라 알림을 보낼 수 없습니다.")
                return dict(record)
            try:
                self.pending.put_nowait((event_id, message))
            except queue.Full:
                record.update(status="FAILED", error="대기 중인 알림이 많아 전송하지 못했습니다.")
                return dict(record)
            if not self.thread:
                self.thread = threading.Thread(target=self._run, name="reservation-notifications", daemon=True)
                self.thread.start()
                atexit.register(self.close)
            return dict(record)

    def publish_test(self, message):
        with self.lock:
            for key, record in self.results.items():
                if key.startswith("TEST-") and record["status"] == "PENDING":
                    return dict(record)
            return self.publish("TEST-" + uuid.uuid4().hex[:12], message)

    def status(self):
        with self.lock:
            tests = [record for key, record in self.results.items() if key.startswith("TEST-")]
            deliveries = [record for key, record in self.results.items() if not key.startswith("TEST-")]
            return {"enabled": self.notifier.enabled, "configured": self.notifier.configured,
                    "credentialsConfigured": self.notifier.credentials_configured,
                    "testPending": any(record["status"] == "PENDING" for record in tests),
                    "lastTest": dict(tests[-1]) if tests else None,
                    "lastDelivery": dict(deliveries[-1]) if deliveries else None}

    def _run(self):
        while not self.stop_event.is_set():
            try:
                event_id, message = self.pending.get(timeout=.25)
            except queue.Empty:
                continue
            result = Delivery("FAILED", error="프로그램 종료로 알림 발송을 중단했습니다.")
            for attempt in range(1, self.policy.max_attempts + 1):
                try:
                    result = self.notifier.send(message.render())
                except Exception:
                    result = Delivery("UNKNOWN", error="알림 전송 결과를 확인할 수 없습니다.")
                status, delay = self.policy.decide(result, attempt)
                result = Delivery(status, result.message_id, result.error, result.retry_after)
                if status != "RETRYING" or self.stop_event.wait(delay):
                    break
            if result.status == "RETRYING":
                result = Delivery("FAILED", error="프로그램 종료로 알림 재시도를 중단했습니다.")
            with self.lock:
                record = self.results[event_id]
                record.update(status=result.status, error=result.error)
                self._trim()
            if self.on_result:
                try:
                    self.on_result(dict(record))
                except Exception:
                    pass  # Notification logging cannot restart a reservation.
            self.pending.task_done()

    def _trim(self):
        # Caller holds the lock; never evict queued or in-flight events.
        for key in list(self.results):
            if len(self.results) <= 100:
                break
            if self.results[key]["status"] != "PENDING":
                self.results.pop(key)

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)
            atexit.unregister(self.close)
