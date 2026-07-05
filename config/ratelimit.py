"""
线程安全的滑动窗口速率限制器。

用于 kline 并发拉取场景：多线程共享一个 RateLimiter 实例，
确保总体 QPS 不超过 Tushare 的 500 次/分钟限制。
"""

import time
import threading
from collections import deque


class RateLimiter:
    """
    滑动窗口速率限制器。

    使用:
      limiter = RateLimiter(max_calls=500, window_seconds=60.0)

      def worker():
          for code in my_codes:
              limiter.acquire()      # 阻塞直到有可用配额
              fetch_kline(code)      # 安全调用 API
    """

    def __init__(self, max_calls: int = 500, window_seconds: float = 60.0):
        self._max_calls = max_calls
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self):
        """阻塞直到有可用配额。"""
        while True:
            with self._lock:
                now = time.time()
                # 清理窗口外的旧时间戳
                cutoff = now - self._window
                while self._timestamps and self._timestamps[0] < cutoff:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max_calls:
                    self._timestamps.append(now)
                    return  # 获权，立即返回

                # 在锁内计算 sleep_time
                sleep_time = self._timestamps[0] - cutoff + 0.01

            # 锁外 sleep
            if sleep_time > 0:
                time.sleep(sleep_time)

    @property
    def available(self) -> int:
        """当前窗口剩余配额数（非阻塞，用于监控）。"""
        with self._lock:
            now = time.time()
            cutoff = now - self._window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return self._max_calls - len(self._timestamps)
