"""Single-process coordinator and command queue for the owned browser event loop."""
import asyncio
import atexit
import fcntl
import os
import threading
import uuid

from playwright.async_api import Error as PlaywrightError

from services.gimpo.client import BrowserFault, PlaywrightGimpoClient
from services.gimpo.store import Conflict, JobStore, PAYMENT_STATES, READY, RESTARTABLE
from services.gimpo.validation import InputError, validate
from services.notifications.outbox import NotificationOutbox
from services.notifications.telegram import TelegramNotifier
from services.notifications.config import CONFIG as NOTIFICATION_CONFIG, TelegramSettings

PREPAYMENT = frozenset({"DRAFT", "CHECKING", "WAITING_AVAILABLE", "AVAILABLE", "PREPARING", "PREPARED", "RECHECKING", READY})


def _is_target_closed(error):
    # playwright.async_api does not export TargetClosedError publicly (1.62); avoid importing
    # the private playwright._impl._errors module and instead match the public Error type plus
    # the message Playwright raises when the target (page/context/browser) closed mid-call.
    return isinstance(error, PlaywrightError) and "has been closed" in str(error)


class RuntimeUnavailable(Exception):
    pass


class ProcessLease:
    """OS-released single-process lease; no stale PID-file guessing."""
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = open(directory / "runtime.lock", "a+")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise RuntimeUnavailable("다른 프로세스가 김포 작업을 실행하고 있습니다. 단일 프로세스로 실행해주세요.") from None

    def close(self):
        fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


class GimpoRuntime:
    def __init__(self, config, client_factory=PlaywrightGimpoClient, notifier=None):
        self.config, self.client_factory = config, client_factory
        self.run_id = uuid.uuid4().hex
        self.command_lock = threading.RLock()
        self.lease = ProcessLease(config.directory)
        self.store = JobStore(config.directory / "jobs.sqlite3")
        os.chmod(config.directory / "jobs.sqlite3", 0o600)
        self.store.recover(self.run_id)
        self.clients, self.inputs, self.tasks = {}, {}, {}
        self.closing = False
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="gimpo-browser", daemon=True)
        self.thread.start()
        self.outbox = NotificationOutbox(self.store, notifier or TelegramNotifier(TelegramSettings.from_environment()),
                                          self.notification_valid, NOTIFICATION_CONFIG.max_attempts)
        self.outbox.start()
        atexit.register(self.close)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._monitor())
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    def _submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def _schedule(self, job_id, coroutine):
        async def tracked():
            self.tasks[job_id] = asyncio.current_task()
            try:
                await coroutine
            finally:
                if self.tasks.get(job_id) is asyncio.current_task():
                    self.tasks.pop(job_id, None)
        return self._submit(tracked())

    def _accepting(self):
        if self.closing:
            raise RuntimeUnavailable("프로그램이 종료 중입니다.")

    def create(self, inputs):
        with self.command_lock:
            self._accepting()
            job = self.store.create(inputs, self.run_id)
            self.inputs[job["id"]] = inputs
            self._schedule(job["id"], self._flow(job["id"]))
            return job

    def restart(self, job_id, version, inputs):
        with self.command_lock:
            self._accepting()
            job = self.store.restart(job_id, version, inputs, self.run_id)
            self.inputs[job_id] = inputs
            self._schedule(job_id, self._flow(job_id))
            return job

    def stop(self, job_id, version, replacement=None):
        with self.command_lock:
            self._accepting()
            if not self.store.get(job_id)["active"]:
                raise Conflict("이미 종료된 작업입니다.")
            job = self.store.command(job_id, version, PREPAYMENT | RESTARTABLE, "STOPPING", "예약 진행을 중지하고 예약창을 닫습니다.")
            self._submit(self._stop(job_id, replacement))
            return job

    async def _stop(self, job_id, replacement=None):
        task = self.tasks.get(job_id)
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._close_client(job_id)
        self.store.release(job_id, "STOPPED", "중지했습니다. 예약은 완료되지 않았습니다.", {"STOPPING"})
        if replacement is not None:
            old = self.store.get(job_id)
            try:
                job = self.store.restart(job_id, old, replacement, self.run_id)
            except Conflict:
                return
            self.inputs[job_id] = replacement
            self._schedule(job_id, self._flow(job["id"]))

    def prepare(self, job_id, version):
        with self.command_lock:
            self._accepting()
            if job_id not in self.inputs:
                raise Conflict("예약 정보를 다시 입력한 뒤 ‘빈자리 조회’ 또는 ‘자동 예약 시작’을 눌러주세요.")
            job = self.store.command(job_id, version, {"AVAILABLE"}, "PREPARING", "공항 사이트에 예약 정보를 입력합니다.")
            self._schedule(job_id, self._prepare(job_id, automatic=False))
            return job

    def proceed(self, job_id, version, consent):
        with self.command_lock:
            self._accepting()
            if consent is not True:
                raise Conflict("결제 대기 단계까지 자동 진행하는 데 동의해주세요.")
            job = self.store.command(job_id, version, {"PREPARED"}, "RECHECKING", "중복 예약과 잔여석을 다시 확인합니다.")
            self._schedule(job_id, self._proceed(job_id))
            return job

    def show(self, job_id, version):
        with self.command_lock:
            job = self.store.get(job_id)
            self.store.check_version(job, version)
            if not job["active"] or job_id not in self.clients:
                raise Conflict("열려 있는 공항 예약창이 없습니다.")
            self._submit(self._show(job_id))
            return job

    async def _show(self, job_id):
        job = self.store.get(job_id)
        if job["state"] == READY and not await self._valid_handoff(job_id):
            return
        client = self.clients.get(job_id)
        if client:
            try:
                await client.show()
            except Exception:
                await self._fail(job_id, BrowserFault("브라우저를 표시할 수 없습니다.", "SESSION_EXPIRED"))

    def resolve(self, job_id, version, outcome, acknowledged):
        if acknowledged is not True or outcome not in {"reserved", "not_reserved", "unknown"}:
            raise Conflict("예약 결과를 선택하고 예약창 닫기에 동의해주세요.")
        with self.command_lock:
            self._accepting()
            if not self.store.get(job_id)["active"]:
                raise Conflict("이미 종료된 작업입니다.")
            job = self.store.command(job_id, version, PAYMENT_STATES | {"CLOSED_BY_USER"}, "CLOSED_BY_USER", "사용자가 선택한 예약 결과를 기록하고 종료합니다.")
            self.store.transition(job_id, "CLOSED_BY_USER", job["reason"], expected={"CLOSED_BY_USER"}, userReportedOutcome=outcome)
            self._submit(self._resolve(job_id))
            return job

    async def _resolve(self, job_id):
        await self._close_client(job_id)
        self.store.release(job_id, "CLOSED_BY_USER", "예약 결과를 기록하고 종료했습니다.", {"CLOSED_BY_USER"})

    async def _new_client(self, job_id):
        client = self.client_factory(self, self.store.get(job_id), self.inputs[job_id])
        self.clients[job_id] = client
        return client

    async def _flow(self, job_id):
        try:
            job = self.store.get(job_id)
            if job["state"] not in {"CHECKING", "WAITING_AVAILABLE"}:
                return
            self._validate_current_input(job_id)
            client = self.clients.get(job_id) or await self._new_client(job_id)
            if self.inputs[job_id]["mode"] == "watch":
                if job["state"] == "WAITING_AVAILABLE" and job["summary"]:
                    self.store.transition(job_id, "RECHECKING", "예약신청 화면에서 다시 시도합니다.", expected={"WAITING_AVAILABLE"})
                    await self._proceed(job_id)
                else:
                    self.store.transition(job_id, "PREPARING", "예약신청 화면에 진입한 뒤 실제 예약 정보를 입력합니다.",
                                          expected={"CHECKING", "WAITING_AVAILABLE"})
                    await self._prepare(job_id, automatic=True, bootstrap=True)
                return
            available = await client.check()
            if available:
                self.store.transition(job_id, "AVAILABLE", "빈자리가 있습니다. 예약 정보를 입력해주세요.", expected={"CHECKING"},
                                      availabilityCheckedAt=self.store.clock(), commandStatus="DONE")
            else:
                self.store.transition(job_id, "WAITING_AVAILABLE", "선택한 기간은 만차입니다.", expected={"CHECKING"},
                                      availabilityCheckedAt=self.store.clock(), commandStatus="DONE")
                await self._finish_pre(job_id, "STOPPED", "1회 조회 결과: 만차입니다.", {"WAITING_AVAILABLE"})
        except Conflict:
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._fail(job_id, self._fault(error))

    def _validate_current_input(self, job_id):
        data = self.inputs[job_id]
        try:
            validate({**data, "passwordConfirmation": data["reservationPassword"]}, self.config.interval_sec)
        except InputError:
            raise BrowserFault("예약 가능한 기간을 벗어났습니다. 입출차 시간을 수정한 뒤 다시 조회해주세요.") from None

    async def _prepare(self, job_id, automatic, bootstrap=False):
        try:
            self._validate_current_input(job_id)
            summary = await self.clients[job_id].prepare(bootstrap=bootstrap)
            self.store.transition(job_id, "PREPARED", "입출차 시간과 주차장, 요금을 확인했습니다.", expected={"PREPARING"}, summary=summary, commandStatus="DONE")
            if automatic:
                self.store.transition(job_id, "RECHECKING", "중복 예약과 잔여석을 다시 확인합니다.", expected={"PREPARED"})
                await self._proceed(job_id)
        except Conflict:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._fail(job_id, self._fault(error))

    async def _proceed(self, job_id):
        try:
            while not self.closing:
                self._validate_current_input(job_id)
                job = self.store.get(job_id)
                if job["state"] != "RECHECKING":
                    return
                available, checked = await self.clients[job_id].proceed()
                if available:
                    self.store.ready(job_id, job["generation"], job["summary"], checked, self.config.handoff_max_age_sec)
                    return
                if self.inputs[job_id]["mode"] != "watch":
                    await self._finish_pre(job_id, "STOPPED", "최종 재조회 결과 만차입니다.", {"RECHECKING"})
                    return
                # Preserve the application document, its filled inputs, and session.
                self.store.transition(job_id, "WAITING_AVAILABLE", "만차입니다. 예약신청 화면에서 다시 시도합니다.",
                                      expected={"RECHECKING"}, availabilityCheckedAt=checked, commandStatus="DONE")
                await asyncio.sleep(self.inputs[job_id]["intervalSeconds"])
                self.store.transition(job_id, "RECHECKING", "예약신청 화면에서 다시 시도합니다.", expected={"WAITING_AVAILABLE"},
                                      commandStatus="RUNNING")
        except Conflict:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._fail(job_id, self._fault(error))

    @staticmethod
    def _fault(error):
        if isinstance(error, BrowserFault):
            return error
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or type(error).__name__ == "TimeoutError":
            return BrowserFault("공항 응답 대기 시간이 초과되었습니다.", "ERROR", retryable=True)
        return BrowserFault("브라우저 또는 공식 화면을 확인할 수 없습니다. Chromium 설치와 화면을 확인해주세요.", "ERROR")

    async def _close_client(self, job_id, clear_inputs=True):
        client = self.clients.get(job_id)
        if client:
            try:
                await client.close()
            except Exception:
                self.inputs.pop(job_id, None)
                job = self.store.get(job_id)
                if job["state"] == "CLOSED_BY_USER":
                    self.store.transition(job_id, "CLOSED_BY_USER", "예약창을 닫지 못했습니다. ‘결과 기록’을 다시 눌러주세요.", expected={"CLOSED_BY_USER"})
                else:
                    self.store.transition(job_id, "ERROR", "예약창을 닫지 못했습니다. ‘중지’를 다시 눌러주세요.",
                                          expected=PREPAYMENT | RESTARTABLE | {"STOPPING"})
                raise Conflict("브라우저 정리 확인이 필요합니다.") from None
            self.clients.pop(job_id, None)
        if clear_inputs:
            self.inputs.pop(job_id, None)

    async def _finish_pre(self, job_id, state, reason, expected):
        try:
            self.store.transition(job_id, state, reason, expected=expected)
        except Conflict:
            return
        try:
            await self._close_client(job_id)
            self.store.release(job_id, state, reason, {state})
        except Conflict:
            # A newer stop/cleanup command owns the final release.
            return

    async def _fail(self, job_id, fault):
        job = self.store.get(job_id)
        if job["paymentMayHaveBeenSent"]:
            self.payment_unknown(job_id)
            return
        await self._finish_pre(job_id, fault.state, str(fault), PREPAYMENT)

    def handoff_cancelled(self, job_id):
        self.loop.create_task(self._finish_pre(job_id, "HANDOFF_CANCELLED",
                                              "결제를 취소했습니다. 계속하려면 ‘빈자리 조회’ 또는 ‘자동 예약 시작’을 눌러주세요.", {READY, "RECHECKING"}))

    def browser_fault(self, job_id, reason):
        self.loop.create_task(self._fail(job_id, BrowserFault(reason)))

    def payment_unknown(self, job_id):
        try:
            self.store.transition(job_id, "PAYMENT_RESULT_UNKNOWN", "결제 결과를 확인할 수 없습니다. 공항 사이트에서 예약 내역을 확인한 뒤 결과를 기록해주세요.",
                                  expected={"PAYMENT_DISPATCHING", "PAYMENT_IN_PROGRESS"})
        except Conflict:
            pass

    async def _valid_handoff(self, job_id):
        job = self.store.get(job_id)
        if job["state"] != READY:
            return False
        if self.store.clock() >= job["handoffDeadline"]:
            await self._finish_pre(job_id, "HANDOFF_EXPIRED", "결제 대기 시간이 지났습니다. ‘빈자리 조회’ 또는 ‘자동 예약 시작’을 눌러주세요.", {READY})
            return False
        client = self.clients.get(job_id)
        try:
            if client is not None and not await client.alive():
                await self._finish_pre(job_id, "SESSION_EXPIRED", "공식 브라우저가 종료되었습니다. 다시 조회해주세요.", {READY})
                return False
            target_closed = False
            try:
                valid = client is not None and await client.inspect()
            except Exception as error:
                valid = False
                target_closed = client is not None and _is_target_closed(error)
            if not valid and client is not None and (target_closed or not await client.alive()):
                await self._finish_pre(job_id, "SESSION_EXPIRED", "공식 브라우저가 종료되었습니다. 다시 조회해주세요.", {READY})
                return False
        except Exception:
            valid = False
        if not valid:
            await self._finish_pre(job_id, "HANDOFF_CANCELLED", "결제창이 닫혔거나 화면이 변경되었습니다. ‘빈자리 조회’ 또는 ‘자동 예약 시작’을 눌러주세요.", {READY})
            return False
        return True

    def notification_valid(self, event):
        if self.closing or event["runId"] != self.run_id:
            return False
        future = self._submit(self._valid_handoff(event["jobId"]))
        try:
            return future.result(timeout=3)
        except Exception:
            return False

    async def _monitor(self):
        while not self.closing:
            try:
                job = self.store.active()
                if job and job["state"] == READY:
                    await self._valid_handoff(job["id"])
                elif job and job["state"] in {"AVAILABLE", "PREPARED", "WAITING_AVAILABLE"}:
                    client = self.clients.get(job["id"])
                    if client and not await client.alive():
                        await self._fail(job["id"], BrowserFault("공식 브라우저가 종료되었습니다.", "SESSION_EXPIRED"))
                elif job and job["state"] in {"PAYMENT_DISPATCHING", "PAYMENT_IN_PROGRESS"}:
                    client = self.clients.get(job["id"])
                    disconnected = client is None or not await client.alive()
                    if disconnected or (job["state"] == "PAYMENT_DISPATCHING" and self.store.clock() - job["paymentDispatchedAt"] > self.config.browser_timeout_sec):
                        self.payment_unknown(job["id"])
            except Exception:
                pass
            await asyncio.sleep(0.25)

    async def _shutdown(self):
        job = self.store.active()
        if job:
            if job["paymentMayHaveBeenSent"]:
                self.payment_unknown(job["id"])
            elif job["state"] in PREPAYMENT | RESTARTABLE | {"STOPPING"}:
                self.store.transition(job["id"], "STOPPING", "프로그램이 종료되고 있습니다.", expected=PREPAYMENT | RESTARTABLE | {"STOPPING"})
                await self._stop(job["id"])
        for task in list(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)

    def close(self):
        with self.command_lock:
            if self.closing:
                return
            self.closing = True
        atexit.unregister(self.close)
        try:
            self._submit(self._shutdown()).result(timeout=self.config.browser_timeout_sec + 5)
        except Exception:
            job = self.store.active()
            if job and job["paymentMayHaveBeenSent"]:
                self.payment_unknown(job["id"])
        self.outbox.close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        # Retain durable records if an operation could not be shut down cleanly.
        if not self.thread.is_alive() and not (self.outbox.thread and self.outbox.thread.is_alive()):
            self.store.close()
            self.lease.close()
