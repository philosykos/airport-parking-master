"""Transactional job repository: never stores browser sessions or reservation secrets."""
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from services.gimpo.validation import PUBLIC_INPUT

PAYMENT_STATES = frozenset({"PAYMENT_DISPATCHING", "PAYMENT_IN_PROGRESS", "PAYMENT_RESULT_UNKNOWN", "RESERVED", "PAYMENT_FAILED"})
RESTARTABLE = frozenset({"STOPPED", "HANDOFF_CANCELLED", "HANDOFF_EXPIRED", "SESSION_EXPIRED", "INTERRUPTED", "REVIEW_REQUIRED", "ERROR"})
READY = "PAYMENT_CONFIRM_READY"


class Conflict(Exception):
    """A stale command or a command forbidden by the current state."""


class JobStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, active INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(active) WHERE active=1;
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS defaults (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        ''')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _get(self, job_id):
        row = self.db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._decode_job(row[0])

    def get(self, job_id):
        with self.lock:
            return self._get(job_id)

    def _save(self, job):
        self.db.execute("INSERT INTO jobs VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET active=excluded.active, data=excluded.data",
                        (job["id"], int(job["active"]), json.dumps(job, ensure_ascii=False)))

    def active(self):
        with self.lock:
            row = self.db.execute("SELECT data FROM jobs WHERE active=1").fetchone()
            return self._decode_job(row[0]) if row else None

    def recent(self):
        with self.lock:
            row = self.db.execute("SELECT data FROM jobs ORDER BY rowid DESC LIMIT 1").fetchone()
            return self._decode_job(row[0]) if row else None

    def create(self, inputs, run_id):
        with self.transaction():
            if self.active():
                raise Conflict("진행 중인 예약을 중지하거나 예약 결과를 확인해주세요.")
            job = {"id": "GMP-" + uuid.uuid4().hex[:12], "runId": run_id,
                   "state": "CHECKING", "active": True, "inputVersion": 1, "generation": 1,
                   "handoffEpoch": 0, "stateVersion": 1, "paymentMayHaveBeenSent": False,
                   "returnedFromPayment": False,
                   "paymentAttemptId": None, "handoffDeadline": None, "availabilityCheckedAt": None,
                   "inputs": {k: inputs[k] for k in PUBLIC_INPUT}, "summary": None,
                   "reason": "빈자리를 조회합니다.", "updatedAt": self.clock(), "logs": [],
                   "commandId": uuid.uuid4().hex, "commandStatus": "RUNNING"}
            self._log(job)
            self._save(job)
            return job

    @staticmethod
    def _compact_logs(logs):
        """Update one row through a request; each polling attempt starts a new row."""
        rows = []
        for index, original in enumerate(logs):
            entry = dict(original)
            new_command = (rows and entry.get("requestId") and rows[-1].get("requestId")
                           and entry["requestId"] != rows[-1]["requestId"])
            if entry.get("logId"):
                new_row = not rows or entry["logId"] != rows[-1]["logId"]
            else:
                new_row = not rows or entry["state"] == "CHECKING" or new_command
                entry["logId"] = f"legacy:{index}:{entry['time']}" if new_row else rows[-1]["logId"]
            if new_row:
                rows.append(entry)
            else:
                rows[-1] = entry
        return rows[-100:]

    @classmethod
    def _decode_job(cls, raw):
        job = json.loads(raw)
        # Also collapse status-by-status logs saved by earlier versions.
        job["logs"] = cls._compact_logs(job.get("logs", []))
        return job

    def _log(self, job):
        previous = job["logs"][-1] if job["logs"] else None
        new_row = (not previous or job["state"] == "CHECKING"
                   or (job["state"] == "RECHECKING" and previous["state"] == "WAITING_AVAILABLE")
                   or previous.get("requestId") != job["commandId"])
        entry = {"time": self.clock(), "state": job["state"], "message": job["reason"],
                 "requestId": job["commandId"],
                 "logId": uuid.uuid4().hex if new_row else previous["logId"]}
        job["logs"] = self._compact_logs(job["logs"] + [entry])

    @staticmethod
    def check_version(job, version):
        if not isinstance(version, dict) or any(type(version.get(k)) is not int or version[k] != job[k]
                                               for k in ("inputVersion", "generation")):
            raise Conflict("예약 정보가 변경되었습니다. 새로고침 후 다시 시도해주세요.")

    def transition(self, job_id, state, reason, *, expected=None, **updates):
        with self.transaction():
            job = self._get(job_id)
            if expected is not None and job["state"] not in expected:
                raise Conflict("작업 상태가 변경되었습니다.")
            return self._transition(job, state, reason, updates)

    def _transition(self, job, state, reason, updates=None):
        old = job["state"]
        job.update(updates or {})
        job.update(state=state, reason=reason, stateVersion=job["stateVersion"] + 1, updatedAt=self.clock())
        self._log(job)
        self._save(job)
        if old == READY and state != READY:
            self._invalidate(job)
        elif state != READY:
            self._refresh_corrections(job)
        if state == READY:
            self._event(job, "READY")
        return job

    def command(self, job_id, version, allowed, next_state, reason):
        with self.transaction():
            job = self._get(job_id)
            self.check_version(job, version)
            if job["state"] not in allowed:
                raise Conflict("현재 상태에서는 이 작업을 실행할 수 없습니다.")
            return self._transition(job, next_state, reason,
                                    {"commandId": uuid.uuid4().hex, "commandStatus": "RUNNING"})

    def restart(self, job_id, version, inputs, run_id):
        with self.transaction():
            job = self._get(job_id)
            self.check_version(job, version)
            active = self.active()
            if job["active"] or job["state"] not in RESTARTABLE or job["paymentMayHaveBeenSent"] or active:
                raise Conflict("결과 확인 또는 다른 작업의 종료가 필요합니다.")
            return self._transition(job, "CHECKING", "예약창을 새로 열어 빈자리를 조회합니다.", {
                "active": True, "runId": run_id, "generation": job["generation"] + 1,
                "inputVersion": job["inputVersion"] + 1, "summary": None, "handoffDeadline": None,
                "inputs": {k: inputs[k] for k in PUBLIC_INPUT}, "commandId": uuid.uuid4().hex,
                "returnedFromPayment": False, "commandStatus": "RUNNING"})

    def ready(self, job_id, generation, summary, checked_at, max_age):
        with self.transaction():
            job = self._get(job_id)
            if job["state"] != "RECHECKING" or job["generation"] != generation:
                raise Conflict("이미 취소된 준비입니다.")
            return self._transition(job, READY, "결제 대기 — 예약 미완료", {
                "handoffEpoch": job["handoffEpoch"] + 1, "summary": summary,
                "availabilityCheckedAt": checked_at, "handoffDeadline": checked_at + max_age,
                "commandStatus": "DONE"})

    def dispatch_payment(self, job_id, version):
        """The durable record is committed BEFORE allowing the original network request."""
        with self.transaction():
            job = self._get(job_id)
            self.check_version(job, version)
            if job["state"] != READY or job["paymentMayHaveBeenSent"] or self.clock() >= job["handoffDeadline"]:
                raise Conflict("유효하지 않거나 이미 전송된 결제 준비 요청입니다.")
            return self._transition(job, "PAYMENT_DISPATCHING", "사용자가 결제 진행을 시작했습니다.", {
                "paymentMayHaveBeenSent": True, "paymentAttemptId": uuid.uuid4().hex,
                "paymentDispatchedAt": self.clock()})

    def mark_returned(self, job_id):
        """PG 창에서 공항 사이트로 돌아온 것을 한 번만 기록한다. 완료 판정이 아니라 확인 요청 신호다."""
        with self.transaction():
            job = self._get(job_id)
            if job["state"] != "PAYMENT_IN_PROGRESS" or job.get("returnedFromPayment"):
                return None
            # 새 commandId로 새 로그 행을 만들어 '결제를 마친 뒤…' 행이 덮이지 않게 한다.
            return self._transition(job, job["state"],
                                    "결제창에서 공항 사이트로 돌아왔습니다. 예약 내역을 확인한 뒤 결과를 기록해주세요.",
                                    {"returnedFromPayment": True, "commandId": uuid.uuid4().hex})

    def release(self, job_id, state, reason, expected):
        # Called only after the owned browser context is closed.
        return self.transition(job_id, state, reason, expected=expected, active=False, commandStatus="DONE")

    def recover(self, run_id):
        with self.transaction():
            for event in self.events():
                if event["status"] == "SENDING":
                    event["status"] = "UNKNOWN"
                    event["error"] = "앱이 종료되어 알림 전송 결과를 확인할 수 없습니다."
                    self._save_event(event)
            rows = self.db.execute("SELECT data FROM jobs WHERE active=1").fetchall()
            for row in rows:
                job = json.loads(row[0])
                if job["runId"] == run_id:
                    continue
                if job["state"] == "PAYMENT_RESULT_UNKNOWN" and job.get("recoveredWithoutBrowser"):
                    self._invalidate(job)
                    continue
                state = "PAYMENT_RESULT_UNKNOWN" if job["paymentMayHaveBeenSent"] else "INTERRUPTED"
                self._transition(job, state, "이전 실행이 종료되었습니다. 공식 사이트에서 진행 여부를 확인해주세요.",
                                 {"active": job["paymentMayHaveBeenSent"], "commandStatus": "DONE", "recoveredWithoutBrowser": True})
                self._invalidate(job)

    def events(self, job_id=None):
        with self.lock:
            rows = self.db.execute("SELECT data FROM events" + (" WHERE job_id=?" if job_id else ""),
                                   (job_id,) if job_id else ()).fetchall()
            return [json.loads(r[0]) for r in rows]

    def _save_event(self, event):
        self.db.execute("INSERT INTO events VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                        (event["id"], event["jobId"], json.dumps(event, ensure_ascii=False)))

    def _event(self, job, kind, parent=None):
        key = f'{job["id"]}:{job["inputVersion"]}:{job["generation"]}:{job["handoffEpoch"]}:{kind}'
        if parent:
            key += f':{parent["id"]}:{job["stateVersion"]}'
        if self.db.execute("SELECT 1 FROM events WHERE id=?", (key,)).fetchone():
            return
        event = {"id": key, "jobId": job["id"], "kind": kind, "runId": job["runId"],
                 "inputVersion": job["inputVersion"], "generation": job["generation"],
                 "handoffEpoch": job["handoffEpoch"], "stateVersion": job["stateVersion"],
                 "status": "PENDING", "attempts": 0, "round": 1, "nextAt": self.clock(),
                 "messageId": None, "mayHaveBeenSent": False, "replyTo": parent["messageId"] if parent else None,
                 "parentId": parent["id"] if parent else None, "superseded": False, "error": None}
        self._save_event(event)

    @staticmethod
    def _may_have_been_sent(event):
        # Older persisted events do not yet carry the delivery-history flag.
        return event.get("mayHaveBeenSent", False) or event["status"] in {"SENT", "UNKNOWN"} or event["messageId"] is not None

    def _invalidate(self, job):
        for event in self.events(job["id"]):
            if event["kind"] != "READY":
                continue
            if self._may_have_been_sent(event):
                self._event(job, "CORRECTION", event)
            if event["status"] in {"PENDING", "RETRYING"}:
                event["status"] = "CANCELLED"
            elif event["status"] == "SENDING":
                event["superseded"] = True
            self._save_event(event)
        self._refresh_corrections(job)

    def _refresh_corrections(self, job):
        for event in self.events(job["id"]):
            if event["kind"] == "CORRECTION" and event["stateVersion"] != job["stateVersion"]:
                if event["status"] in {"PENDING", "RETRYING"}:
                    event["status"] = "CANCELLED"
                    self._save_event(event)
                    parent = next(e for e in self.events(job["id"]) if e["id"] == event["parentId"])
                    self._event(job, "CORRECTION", parent)
                elif event["status"] == "SENDING":
                    event["superseded"] = True
                    self._save_event(event)

    def event_valid(self, event, job):
        if event["kind"] == "CORRECTION":
            return event["stateVersion"] == job["stateVersion"]
        return (job["state"] == READY and job["active"] and self.clock() < job["handoffDeadline"]
                and all(event[k] == job[k] for k in ("runId", "inputVersion", "generation", "handoffEpoch")))

    def claim_event(self, event_id):
        with self.transaction():
            event = next(e for e in self.events() if e["id"] == event_id)
            if event["status"] not in {"PENDING", "RETRYING"} or event["nextAt"] > self.clock():
                return None
            job = self._get(event["jobId"])
            if not self.event_valid(event, job):
                event["status"] = "CANCELLED"
                self._save_event(event)
                return None
            event.update(status="SENDING", attempts=event["attempts"] + 1)
            self._save_event(event)
            return event

    def finish_event(self, event_id, status, *, message_id=None, error=None, delay=0):
        with self.transaction():
            event = next(e for e in self.events() if e["id"] == event_id)
            job = self._get(event["jobId"])
            event.update(mayHaveBeenSent=self._may_have_been_sent(event) or status in {"SENT", "UNKNOWN"},
                         status=status, messageId=message_id if message_id is not None else event["messageId"],
                         error=error, nextAt=self.clock() + delay)
            if event["superseded"] and status == "RETRYING":
                event["status"] = "CANCELLED"
            self._save_event(event)
            if event["superseded"] and status in {"SENT", "UNKNOWN"}:
                parent = event if event["kind"] == "READY" else next(e for e in self.events() if e["id"] == event["parentId"])
                self._event(job, "CORRECTION", parent)

    def resend(self, job_id, version, event_id, round_number):
        with self.transaction():
            job = self._get(job_id)
            self.check_version(job, version)
            event = next((e for e in self.events(job_id) if e["id"] == event_id), None)
            if not event or not self.event_valid(event, job):
                raise Conflict("현재 유효한 안내만 재발송할 수 있습니다.")
            if type(round_number) is not int or round_number != event["round"]:
                raise Conflict("이미 재발송했거나 오래된 안내입니다.")
            if event["status"] not in {"FAILED", "UNKNOWN", "SENT"}:
                return event
            event.update(mayHaveBeenSent=self._may_have_been_sent(event), status="PENDING", attempts=0,
                         round=event["round"] + 1, nextAt=self.clock(), superseded=False, error=None)
            self._save_event(event)
            return event

    def get_defaults(self):
        with self.lock:
            row = self.db.execute("SELECT data FROM defaults WHERE id=1").fetchone()
            return json.loads(row[0]) if row else {}

    def save_defaults(self, data):
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO defaults VALUES (1, ?)", (json.dumps(data, ensure_ascii=False),))

    def close(self):
        with self.lock:
            self.db.close()
