"""
指数退避重试装饰器。

支持指定重试次数、退避策略、可重试异常类型。
网络波动/Tushare 服务端临时错误自动重试，结构性错误（如参数错误）不重试。
"""

import time
import functools
from typing import Callable, Tuple, Type


def retry_on_failure(
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
):
    """
    指数退避重试装饰器。

    参数:
      max_retries: 最大重试次数（含首次调用，共 max_retries+1 次尝试）
      base_delay: 首次退避延迟（秒）
      backoff_factor: 退避因子（延迟 = base_delay * backoff_factor^attempt）
      retryable_exceptions: 可重试的异常类型元组

    行为:
      - 首次调用失败 → 等 base_delay 秒 → 重试
      - 第二次失败 → 等 base_delay * backoff_factor 秒 → 重试
      - ...
      - 耗尽重试次数 → 抛出最后一个异常

    使用:
      @retry_on_failure(max_retries=3)
      def fetch_data(pro, code):
          return pro.pro_bar(ts_code=code, ...)
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt < max_retries:
                        delay = base_delay * (backoff_factor ** attempt)
                        time.sleep(delay)
                    else:
                        raise last_exc
            # unreachable
        return wrapper
    return decorator
