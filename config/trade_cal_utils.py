"""
交易日历工具函数：基于 trade_cal.parquet 提供日期计算。
"""

import datetime
import os
from typing import Optional
import polars as pl

from config.constants import TRADE_CAL_PATH

# 模块加载时即缓存，避免首次调用的重试开销和线程安全问题
_TRADE_CAL_CACHE: Optional[pl.DataFrame] = (
    pl.read_parquet(TRADE_CAL_PATH) if os.path.exists(TRADE_CAL_PATH) else None
)


def _load_trade_cal(trade_cal: Optional[pl.DataFrame] = None) -> pl.DataFrame:
    """加载交易日历（使用模块级缓存，避免重复读盘）。"""
    if trade_cal is not None:
        return trade_cal
    if _TRADE_CAL_CACHE is None:
        raise FileNotFoundError(
            f"未找到 {TRADE_CAL_PATH}，请先运行 scripts/sync_trade_cal.py"
        )
    return _TRADE_CAL_CACHE


def get_nth_trading_day(
    start_date: str | datetime.date,
    n: int,
    trade_cal: Optional[pl.DataFrame] = None,
) -> datetime.date:
    """从 start_date（含）起第 n 个交易日。n>0 往后，n<0 往前，n=0 返回自身。"""
    if n == 0:
        if isinstance(start_date, str):
            start_date = datetime.date.fromisoformat(start_date)
        return start_date

    cal = _load_trade_cal(trade_cal)
    if isinstance(start_date, str):
        start_date = datetime.date.fromisoformat(start_date)

    trading_days = (
        cal.filter(pl.col("is_open") == 1)
        .sort("cal_date")["cal_date"]
        .to_list()
    )

    if n > 0:
        subset = [d for d in trading_days if d >= start_date]
        if len(subset) < n:
            raise ValueError(f"从 {start_date} 起仅有 {len(subset)} 个交易日，不足 {n} 个")
        return subset[n - 1]
    else:
        n_abs = abs(n)
        subset = [d for d in trading_days if d <= start_date]
        if len(subset) < n_abs:
            raise ValueError(f"从 {start_date} 往前仅有 {len(subset)} 个交易日，不足 {n_abs} 个")
        return subset[-n_abs]


def is_trading_day(date_str: str | datetime.date, trade_cal: Optional[pl.DataFrame] = None) -> bool:
    """判断某日期是否为交易日。"""
    cal = _load_trade_cal(trade_cal)
    if isinstance(date_str, str):
        date_str = datetime.date.fromisoformat(date_str)
    row = cal.filter(pl.col("cal_date") == date_str)
    if row.is_empty():
        return False
    return row[0, "is_open"] == 1


def get_prev_trading_day(date_str: str | datetime.date, trade_cal: Optional[pl.DataFrame] = None) -> datetime.date:
    """获取上一个交易日（若本身是交易日，返回自身）。"""
    return get_nth_trading_day(date_str, -1, trade_cal)
