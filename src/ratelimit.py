import threading
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = max(0.0, float(rate_per_sec))
        self.capacity = max(1, int(burst))
        self.tokens = float(self.capacity)
        self.last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.rate <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last
            self.last = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens < 1.0:
                # need to wait for enough tokens
                needed = 1.0 - self.tokens
                sleep_s = needed / self.rate
                sleep_s = max(0.0, sleep_s)
                # Observe wait in metrics (best-effort, no hard dep)
                try:
                    from .metrics import observe_ads_rate_limit_wait  # type: ignore

                    observe_ads_rate_limit_wait(sleep_s)
                except Exception:
                    pass
                time.sleep(sleep_s)
                # after sleep, add tokens and proceed
                now2 = time.monotonic()
                elapsed2 = now2 - self.last
                self.last = now2
                self.tokens = min(self.capacity, self.tokens + elapsed2 * self.rate)
            # spend one token
            self.tokens -= 1.0
