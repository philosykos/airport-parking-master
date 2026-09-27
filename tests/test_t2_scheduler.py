import threading

from services.t2_scheduler import Scheduler


def gated_job():
    """부를 때마다 이름을 기록하고, release가 설정될 때까지 '호출 중'으로 멈추는 작업을 만든다."""
    calls, entered, release = [], threading.Event(), threading.Event()

    def make(name):
        def run():
            calls.append(name)
            entered.set()
            release.wait(5)
            return False
        return run
    return make, calls, entered, release


def test_start_runs_job_until_stop():
    s = Scheduler()
    make, calls, entered, release = gated_job()
    release.set()
    assert s.start(make("a"), 60) is True
    assert entered.wait(2)
    assert s.running is True
    assert s.stop() is True
    s.thread.join(2)
    assert not s.thread.is_alive()
    assert s.running is False
    assert calls == ["a"]


def test_job_returning_true_ends_run():
    s = Scheduler()
    s.start(lambda: True, 60)
    s.thread.join(2)
    assert not s.thread.is_alive()
    assert s.running is False


def test_on_start_runs_before_first_job():
    order = []
    s = Scheduler()
    s.start(lambda: order.append("job") or True, 60, on_start=lambda: order.append("start"))
    s.thread.join(2)
    assert order == ["start", "job"]


def test_start_while_running_is_refused():
    s = Scheduler()
    make, calls, entered, release = gated_job()
    s.start(make("a"), 60)
    assert entered.wait(2)
    assert s.start(make("b"), 60) is False
    release.set()
    s.stop()
    s.thread.join(2)
    assert calls == ["a"]


def test_stop_when_idle_is_refused():
    assert Scheduler().stop() is False


def test_restart_during_inflight_job_does_not_revive_old_run():
    s = Scheduler()
    make, calls, entered, release = gated_job()
    try:
        s.start(make("old"), 0.01)  # 주기가 짧아 되살아나면 곧바로 다시 부른다
        assert entered.wait(2)
        old = s.thread
        assert s.stop() is True
        entered.clear()
        assert s.start(make("new"), 60) is True
        assert entered.wait(2)
        release.set()
        old.join(2)
        assert not old.is_alive()
        assert calls.count("old") == 1
        assert s.running is True
    finally:
        release.set()  # 중간에 실패해도 멈춰 있는 작업을 풀어 준다
        s.stop()
        s.thread.join(2)
