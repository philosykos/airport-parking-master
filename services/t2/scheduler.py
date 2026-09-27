"""반복 호출 워커 하나의 수명(시작·중지·실행 여부)을 관리한다."""
import threading


class Scheduler:
    """job을 interval_sec 간격으로 백그라운드 스레드에서 되풀이한다.

    실행마다 중지 신호(Event)를 새로 만들어 그 실행의 스레드에만 넘긴다. 신호 하나를 clear()해
    재사용하면, 중지 직후 다시 시작할 때 HTTP 호출 중이던 옛 스레드가 되살아나 둘이 함께 돈다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stop.set()
        self.thread = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive() and not self._stop.is_set()

    def start(self, job, interval_sec, on_start=None):
        """실행을 시작한다. 이미 실행 중이면 아무것도 하지 않고 False를 돌려준다.

        job()이 True를 돌려주면 그 실행을 끝낸다. on_start는 스레드를 띄우기 전에 잠금 안에서 부른다.
        """
        with self._lock:
            if self.running:
                return False
            if on_start is not None:
                on_start()
            self._stop = threading.Event()
            self.thread = threading.Thread(target=self._loop, args=(job, interval_sec, self._stop), daemon=True)
            self.thread.start()
            return True

    def stop(self):
        """현재 실행에 중지 신호를 보낸다. 실행 중이 아니면 False. 진행 중인 job 호출은 끝까지 간다."""
        with self._lock:
            if not self.running:
                return False
            self._stop.set()
            return True

    @staticmethod
    def _loop(job, interval_sec, stop):
        while not stop.is_set():
            if job():
                stop.set()
                break
            stop.wait(interval_sec)
