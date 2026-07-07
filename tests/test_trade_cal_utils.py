"""交易日历工具函数测试——基于 TRADE_CAL_START_DATE 过滤后的数据

验证 get_nth_trading_day 等函数在使用 TRADE_CAL_START_DATE 过滤后的
交易日历数据时能正常工作。
"""

import datetime
import os

import polars as pl
import pytest

from config.constants import TRADE_CAL_START_DATE, TRADE_CAL_PATH
from config.constants import TRADE_CAL_COLUMNS, TRADE_CAL_SCHEMA


def _parse_start_date() -> datetime.date:
    return datetime.date(
        int(TRADE_CAL_START_DATE[:4]),
        int(TRADE_CAL_START_DATE[4:6]),
        int(TRADE_CAL_START_DATE[6:8]),
    )


@pytest.fixture
def filtered_cal() -> pl.DataFrame:
    """构造仅含 2026-01-01 起数据的模拟交易日历"""
    return pl.DataFrame({
        "exchange": ["SSE", "SSE", "SSE", "SSE", "SSE", "SSE"],
        "cal_date": [
            datetime.date(2026, 1, 1),   # 周四，非交易日
            datetime.date(2026, 1, 2),   # 周五，交易日
            datetime.date(2026, 1, 5),   # 周一，交易日
            datetime.date(2026, 1, 6),   # 周二，交易日
            datetime.date(2026, 1, 7),   # 周三，交易日
            datetime.date(2026, 1, 8),   # 周四，交易日
        ],
        "is_open": [0, 1, 1, 1, 1, 1],
        "pretrade_date": [
            datetime.date(2025, 12, 31),
            datetime.date(2026, 1, 1),
            datetime.date(2026, 1, 2),
            datetime.date(2026, 1, 5),
            datetime.date(2026, 1, 6),
            datetime.date(2026, 1, 7),
        ],
    })


class TestGetNthTradingDay:

    def test_tc2_6_get_nth_trading_day_forward(self, filtered_cal):
        """从 2026-01-01 起第 1 个交易日应为 2026-01-02"""
        from config.trade_cal_utils import get_nth_trading_day

        result = get_nth_trading_day("2026-01-01", 1, trade_cal=filtered_cal)
        assert result == datetime.date(2026, 1, 2), \
            f"第 1 个交易日应为 2026-01-02，实际 {result}"

    def test_tc2_6_get_nth_trading_day_multiple(self, filtered_cal):
        """从 2026-01-01 起第 3 个交易日应为 2026-01-06"""
        from config.trade_cal_utils import get_nth_trading_day

        result = get_nth_trading_day("2026-01-01", 3, trade_cal=filtered_cal)
        assert result == datetime.date(2026, 1, 6), \
            f"第 3 个交易日应为 2026-01-06，实际 {result}"

    def test_tc2_6_get_nth_trading_day_backward(self, filtered_cal):
        """从 2026-01-08 起往前第 1 个交易日应为 2026-01-07（get_prev_trading_day 若本身是交易日返回自身）"""
        from config.trade_cal_utils import get_nth_trading_day

        # n=-1 从交易日往前的逻辑：subset[-1] = 最后一个 <= start_date 的交易日
        # 由于 2026-01-07 是交易日，结果 = 自身
        result = get_nth_trading_day("2026-01-07", -1, trade_cal=filtered_cal)
        assert result == datetime.date(2026, 1, 7), \
            f"从交易日 2026-01-07 往前第 1 个应返回自身，实际 {result}"

        # 从非交易日往前：2026-01-01 不是交易日，往前第 1 个也没有更早的交易日
        # 测试从交易日 2026-01-07 往前第 2 个
        result2 = get_nth_trading_day("2026-01-07", -2, trade_cal=filtered_cal)
        assert result2 == datetime.date(2026, 1, 6), \
            f"往前第 2 个交易日应为 2026-01-06，实际 {result2}"

    def test_tc2_6_get_nth_trading_day_zero(self, filtered_cal):
        """n=0 时返回自身"""
        from config.trade_cal_utils import get_nth_trading_day

        result = get_nth_trading_day("2026-01-05", 0, trade_cal=filtered_cal)
        assert result == datetime.date(2026, 1, 5), \
            f"n=0 应返回自身 2026-01-05，实际 {result}"

    def test_tc2_6_get_nth_trading_day_on_trading_day(self, filtered_cal):
        """从交易日 2026-01-05 起第 1 个交易日（含自身）应为 2026-01-05"""
        from config.trade_cal_utils import get_nth_trading_day

        result = get_nth_trading_day("2026-01-05", 1, trade_cal=filtered_cal)
        assert result == datetime.date(2026, 1, 5), \
            f"交易日 2026-01-05 起第 1 天应为自身，实际 {result}"

    def test_tc2_6_with_real_data(self):
        """在真实 trade_cal.parquet 上验证 get_nth_trading_day"""
        if not os.path.exists(TRADE_CAL_PATH):
            pytest.skip(f"skip: {TRADE_CAL_PATH} 不存在")

        from config.trade_cal_utils import get_nth_trading_day

        start = _parse_start_date()

        # 从 start_date 起第 1 个交易日
        result = get_nth_trading_day(start, 1)
        assert result >= start, f"第 1 个交易日 {result} 应 >= {start}"

        # 从今天往前第 1 个交易日
        today = datetime.date.today()
        prev = get_nth_trading_day(today, -1)
        assert prev <= today, f"往前第 1 个交易日 {prev} 应 <= {today}"


class TestIsTradingDay:
    """is_trading_day 辅助验证"""

    def test_is_trading_day_known(self, filtered_cal):
        """验证已知的交易日和非交易日"""
        from config.trade_cal_utils import is_trading_day

        assert is_trading_day("2026-01-02", trade_cal=filtered_cal) is True, "2026-01-02 应为交易日"
        assert is_trading_day("2026-01-01", trade_cal=filtered_cal) is False, "2026-01-01 应为非交易日"

    def test_is_trading_day_out_of_range(self, filtered_cal):
        """范围外的日期应返回 False"""
        from config.trade_cal_utils import is_trading_day

        assert is_trading_day("2025-12-31", trade_cal=filtered_cal) is False, \
            "2025-12-31 不在日历中，应返回 False"
