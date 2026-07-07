"""同步 ST 数据脚本测试——TRADE_CAL_START_DATE 引用验证

验证 sync_st.py 使用了 TRADE_CAL_START_DATE 而非硬编码日期。
"""

import polars as pl
import pytest

from config.constants import TRADE_CAL_START_DATE


class TestSyncSTUsesConstant:
    """TC2-5: sync_st.py 使用 TRADE_CAL_START_DATE 而非硬编码"""

    def test_tc2_5_imports_trade_cal_start_date(self):
        """验证 sync_st.py 中 import 了 TRADE_CAL_START_DATE"""
        import scripts.sync_st as sync_st_module

        # 检查模块源码中是否包含 TRADE_CAL_START_DATE 的引用
        source = open(sync_st_module.__file__).read()

        # 从 config import 中应包含 TRADE_CAL_START_DATE
        assert "TRADE_CAL_START_DATE" in source, \
            "sync_st.py 应 import TRADE_CAL_START_DATE"

        # 不应包含硬编码的 "20260101" 作为日期字面量（TRADE_CAL_START_DATE 的值除外）
        # import 语句中的 "20260101" 不算硬编码（那是常量定义）
        # 但 sync_st.py 的代码中不应出现硬编码日期
        lines_with_hardcode = []
        for i, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            # 跳过注释、import 行、空行
            if stripped.startswith("#") or "import" in stripped or not stripped:
                continue
            # 检查是否包含类似 "2026-01-01" 或 "20260101" 的日期字面量
            if '"2026' in stripped or "'2026" in stripped:
                lines_with_hardcode.append((i, stripped))

        assert len(lines_with_hardcode) == 0, \
            f"sync_st.py 不应包含硬编码日期，发现：{lines_with_hardcode}"

    def test_tc2_5_sync_full_uses_constant(self):
        """验证 sync_full 函数使用 TRADE_CAL_START_DATE 构建起始日期"""
        import scripts.sync_st as sync_st_module

        source = open(sync_st_module.__file__).read()

        # 验证 sync_full 中使用 TRADE_CAL_START_DATE 解析起始日期
        # 应包含类似 TRADE_CAL_START_DATE[:4] 的字符串切片操作
        assert "TRADE_CAL_START_DATE" in source

        # 启动日期的计算方式应等同于 date(int(TRADE_CAL_START_DATE[:4]), ...)
        expected_patterns = [
            "TRADE_CAL_START_DATE[:4]",
            "TRADE_CAL_START_DATE[4:6]",
            "TRADE_CAL_START_DATE[6:8]",
        ]
        for pattern in expected_patterns:
            assert pattern in source, \
                f"sync_st.py 应包含 '{pattern}' 以从 TRADE_CAL_START_DATE 解析日期"

    def test_tc2_5_no_hardcoded_date_in_source(self):
        """验证源代码硬编码日期清单"""
        import scripts.sync_st as sync_st_module

        source = open(sync_st_module.__file__).read()

        # 专门检查是否有遗留的硬编码 "2026-01-01" 或其他硬编码起始日
        # 允许的 import 中的 "20260101" 实际上是 constants.py 的 re-export
        hardcoded_dates = ["\"2026-01-01\"", "'2026-01-01'", "\"20260101\"", "'20260101'"]
        for hd in hardcoded_dates:
            # 检查非 import 行中是否包含
            for i, line in enumerate(source.splitlines(), 1):
                if hd in line and "import" not in line and not line.strip().startswith("#"):
                    pytest.fail(f"第 {i} 行含硬编码日期 {hd}：{line.strip()}")
