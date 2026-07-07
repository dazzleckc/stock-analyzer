"""常量定义验证测试"""

import polars as pl

from config.constants import (
    CHANGELOG_SCHEMA,
    TRADE_CAL_START_DATE,
)


class TestChangelogSchema:
    def test_changelog_schema_detected_at_is_date(self):
        """TC-SCHEMA-01: CHANGELOG_SCHEMA detected_at 类型验证"""
        assert CHANGELOG_SCHEMA["detected_at"] == pl.Date, \
            f"期望 pl.Date，实际 {CHANGELOG_SCHEMA['detected_at']}"


class TestTradeCalConstants:
    def test_trade_cal_start_date_exists(self):
        """TC-SCHEMA-02: TRADE_CAL_START_DATE 常量存在且格式正确"""
        assert isinstance(TRADE_CAL_START_DATE, str), "应为字符串"
        assert len(TRADE_CAL_START_DATE) == 8, "长度应为 8"
        assert TRADE_CAL_START_DATE.isdigit(), "应全为数字"

    def test_trade_cal_start_date_parses_correctly(self):
        """TC-SYNC-03: TRADE_CAL_START_DATE 可正确解析为日期"""
        from datetime import date
        d = date(
            int(TRADE_CAL_START_DATE[:4]),
            int(TRADE_CAL_START_DATE[4:6]),
            int(TRADE_CAL_START_DATE[6:8]),
        )
        assert d == date(2026, 1, 1), f"期望 2026-01-01，实际 {d}"

    def test_trade_cal_importable_from_config(self):
        """验证 TRADE_CAL_START_DATE 可通过 from config import 引入"""
        from config import TRADE_CAL_START_DATE as tcs
        assert tcs == "20260101"
