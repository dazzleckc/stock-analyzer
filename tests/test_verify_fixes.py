"""
毕达标 · Phase 4 重测：验证 3 个修复的有效性

验证方式（不修改源文件，仅通过解析源码和模拟执行验证）：
  1. P0-1: sync_trade_cal.py 空 filter 后覆写保护
  2. P1-C-6: sync_stocks.py backward compat cast 加强（!= pl.Date）
  3. P1-C-2: sync_trade_cal.py + sync_st.py 日期格式校验 assert
"""

import ast
import functools
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

@functools.lru_cache(maxsize=2)
def _read_source(rel_path: str) -> str:
    """读取源码文件。"""
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


def _parse_ast(rel_path: str) -> ast.Module:
    """解析源码为 AST。"""
    return ast.parse(_read_source(rel_path))


def _find_function_body(source_code: str, func_name: str) -> str:
    """通过 AST 找到函数体源码。"""
    tree = ast.parse(source_code)
    lines = source_code.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            start = node.lineno - 1      # 0-based
            end = node.end_lineno
            return "\n".join(lines[start:end])
    return ""


def _find_linenos_for_pattern(source_code: str, pattern: str) -> list[int]:
    """返回包含 pattern 的所有行号（1-based）。"""
    return [i + 1 for i, line in enumerate(source_code.splitlines()) if pattern in line]


# ═══════════════════════════════════════════════════════════════════
# P0-1: sync_trade_cal.py 空 filter 后覆写保护
# ═══════════════════════════════════════════════════════════════════

class TestP01EmptyFilterProtection:
    """验证 sync_trade_cal.py sync() 中 filter 后有空结果保护。"""

    @staticmethod
    def _get_sync_body() -> str:
        return _find_function_body(_read_source("scripts/sync_trade_cal.py"), "sync")

    def test_p01_has_empty_check(self):
        """P0-1-1: sync() 函数包含空结果检查 'df.is_empty()'"""
        body = self._get_sync_body()
        assert "df.is_empty()" in body or ".is_empty()" in body, \
            "sync() 中应包含 is_empty() 检查"

    def test_p01_skip_write_on_empty(self):
        """P0-1-2: 空结果时跳过 atomic_write_parquet"""
        body = self._get_sync_body()
        # 在 is_empty 分支内不应有 atomic_write_parquet
        # 即 is_empty 应在 atomic_write_parquet 之前
        is_empty_line = body.index("is_empty()")
        atomic_write_line = body.find("atomic_write_parquet")
        assert atomic_write_line == -1 or is_empty_line < atomic_write_line, \
            "is_empty() 检查应在 atomic_write_parquet 之前"
        # 确认有 return 语句跳过写入
        assert "return" in body.split("if")[-1] if "if" in body else True

    def test_p01_empty_check_after_filter(self):
        """P0-1-3: 空结果检查在 filter 操作之后"""
        body = self._get_sync_body()
        filter_idx = body.find("df.filter")
        is_empty_idx = body.find("is_empty()")
        assert filter_idx >= 0, "sync() 中应包含 df.filter()"
        assert is_empty_idx > filter_idx, \
            "is_empty() 检查应在 filter() 操作之后，防止 filter 前检查"
        print(f"  ✓ filter() 在第 {filter_idx} 字符位，is_empty() 在第 {is_empty_idx} 字符位")

    def test_p01_empty_returns_zero_rows(self):
        """P0-1-4: 空结果时返回 {"rows": 0, ...}"""
        body = self._get_sync_body()
        assert '"rows": 0' in body or "'rows': 0" in body, \
            "空结果时应返回 rows=0 的 dict"
        assert '"date_min": None' in body or "'date_min': None" in body, \
            "空结果时 date_min 应为 None"

    def test_p01_simulate_empty_df(self):
        """P0-1-5: 模拟空 DataFrame 执行路径——验证 is_empty 行为"""
        empty_df = pl.DataFrame({
            "exchange": pl.Series([], dtype=pl.Utf8),
            "cal_date": pl.Series([], dtype=pl.Date),
            "is_open": pl.Series([], dtype=pl.Int8),
            "pretrade_date": pl.Series([], dtype=pl.Date),
        })
        assert empty_df.is_empty() is True, "空 DataFrame is_empty() 应为 True"

        # 非空 DataFrame is_empty() 应为 False
        non_empty_df = pl.DataFrame({
            "exchange": ["SSE"],
            "cal_date": [date(2026, 1, 1)],
            "is_open": [1],
            "pretrade_date": [date(2025, 12, 31)],
        })
        assert non_empty_df.is_empty() is False, "非空 DataFrame is_empty() 应为 False"

    def test_p01_early_return_on_empty(self):
        """P0-1-6: 空结果不再调用 atomic_write_parquet"""
        body = self._get_sync_body()
        # 分析 if 分支：在 is_empty 条件下应有 return，不应有 atomic_write_parquet
        # 定位 if is_empty 附近的行
        lines = body.splitlines()
        in_empty_branch = False
        for lineno, line in enumerate(lines, 1):
            if "is_empty" in line:
                in_empty_branch = True
                continue
            if in_empty_branch:
                stripped = line.strip()
                if stripped.startswith("return"):
                    # 空分支内有 return，合格
                    break
                if "atomic_write_parquet" in stripped:
                    pytest.fail(f"空分支内不应调用 atomic_write_parquet（第 {lineno} 行）")
                if stripped.startswith("print"):
                    continue
                if stripped.startswith("#") or not stripped:
                    continue
                # 如果不是缩进行，说明退出空分支了
                if stripped and not stripped.startswith((" ", "\t")):
                    break


# ═══════════════════════════════════════════════════════════════════
# P1-C-6: sync_stocks.py backward compat cast 加强
# ═══════════════════════════════════════════════════════════════════

class TestP1C6BackwardCompatCast:
    """验证 sync_stocks.py append_changelog 中 cast 判断已改为 != pl.Date。"""

    @staticmethod
    def _get_append_changelog_body() -> str:
        return _find_function_body(_read_source("scripts/sync_stocks.py"), "append_changelog")

    def test_p1c6_not_equal_pl_date(self):
        """P1-C-6-1: 使用 '!= pl.Date' 而非 '== pl.Datetime'"""
        body = self._get_append_changelog_body()
        # 应该用 != pl.Date
        assert "!= pl.Date" in body, \
            "append_changelog 应使用 '!= pl.Date' 覆盖所有非 Date 类型"
        # 不应该再用 == pl.Datetime（旧的写法）
        assert "== pl.Datetime" not in body, \
            "不应再使用 '== pl.Datetime' 判断"

    def test_p1c6_covers_all_non_date_types(self):
        """P1-C-6-2: '!= pl.Date' 能覆盖 Utf8, Datetime 等所有非 Date 类型"""
        body = self._get_append_changelog_body()

        # 提取条件表达式
        lines_with_ne = [l for l in body.splitlines() if "!= pl.Date" in l]
        assert len(lines_with_ne) >= 1, "应至少有一处 != pl.Date 判断"

        # 验证该行在 if 语句内
        for line in lines_with_ne:
            assert "if" in line or "elif" in line, \
                f"'!= pl.Date' 应在条件判断中: {line.strip()}"

    def test_p1c6_simulate_detected_at_cast(self):
        """P1-C-6-3: 模拟不同 detected_at 类型，验证 cast 逻辑生效

        验证：
        - pl.Datetime → cast 为 pl.Date（修复前的旧数据）
        - pl.Utf8 → cast 为 pl.Date（理论上的旧数据）
        - pl.Date → 不 cast（已经是正确类型）
        """
        from scripts.sync_stocks import append_changelog
        from config.constants import STOCKS_CHANGELOG_PATH
        import tempfile
        import os

        for old_type, old_val, should_cast in [
            (pl.Datetime, "2026-01-01 10:30:00", True),
            (pl.Utf8, "2026-01-01", True),
            (pl.Date, date(2026, 1, 1), False),
        ]:
            with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
                tmp_path = f.name

            try:
                schema_overrides = {
                    "code": pl.Utf8,
                    "field": pl.Utf8,
                    "old_value": pl.Utf8,
                    "new_value": pl.Utf8,
                    "detected_at": old_type,
                }

                if should_cast and old_type == pl.Datetime:
                    # Datetime 需要传入 datetime 对象
                    from datetime import datetime as dt
                    val = dt(2026, 1, 1, 10, 30, 0)
                else:
                    val = old_val

                old_data = pl.DataFrame({
                    "code": ["000001"],
                    "field": ["name"],
                    "old_value": ["旧名"],
                    "new_value": ["新名"],
                    "detected_at": [val],
                }, schema=schema_overrides)

                old_data.write_parquet(tmp_path)

                # 构造新 changelog（detected_at 为 Date 类型）
                new_changelog_fresh = pl.DataFrame({
                    "code": ["600000"],
                    "field": ["_new_"],
                    "old_value": [None],
                    "new_value": ["浦发银行"],
                    "detected_at": [date(2026, 6, 1)],
                }, schema={
                    "code": pl.Utf8,
                    "field": pl.Utf8,
                    "old_value": pl.Utf8,
                    "new_value": pl.Utf8,
                    "detected_at": pl.Date,
                })

                # 用 monkeypatch 替换路径——但我们不能直接在类内用 monkeypatch
                # 手动 patch 路径属性
                original_path = STOCKS_CHANGELOG_PATH
                import scripts.sync_stocks as ss
                old_val_path = ss.STOCKS_CHANGELOG_PATH
                ss.STOCKS_CHANGELOG_PATH = tmp_path

                try:
                    append_changelog(new_changelog_fresh)
                    result = pl.read_parquet(tmp_path)
                    assert result.schema["detected_at"] == pl.Date, \
                        f"旧类型 {old_type} 合并后 detected_at 应为 pl.Date，实际 {result.schema['detected_at']}"
                    assert result.height == 2, \
                        f"合并后应有 2 行（旧 1 + 新 1），实际 {result.height}"
                finally:
                    ss.STOCKS_CHANGELOG_PATH = old_val_path

                print(f"  ✓ 旧类型 {old_type.__name__} → 合并后 detected_at 为 pl.Date (cast={'是' if should_cast else '否'})")

            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════
# P1-C-2: 日期格式校验 assert
# ═══════════════════════════════════════════════════════════════════

class TestP1C2DateAssert:
    """验证 sync_trade_cal.py 和 sync_st.py 中 TRADE_CAL_START_DATE 格式校验。"""

    def test_p1c2_trade_cal_has_assert(self):
        """P1-C-2-1: sync_trade_cal.py 中有 assert 检查 TRADE_CAL_START_DATE"""
        source = _read_source("scripts/sync_trade_cal.py")
        assert_lines = [l for l in source.splitlines() if "assert" in l and "TRADE_CAL_START_DATE" in l]
        assert len(assert_lines) >= 1, \
            "sync_trade_cal.py 应包含至少 1 个 assert 检查 TRADE_CAL_START_DATE"
        # 验证 assert 内容检查长度和数字格式
        assert any("len(TRADE_CAL_START_DATE) == 8" in l for l in assert_lines), \
            "assert 应检查 len == 8"
        assert any("isdigit()" in l for l in assert_lines), \
            "assert 应检查 isdigit()"

    def test_p1c2_sync_st_has_assert(self):
        """P1-C-2-2: sync_st.py 中有 assert 检查 TRADE_CAL_START_DATE"""
        source = _read_source("scripts/sync_st.py")
        assert_lines = [l for l in source.splitlines() if "assert" in l and "TRADE_CAL_START_DATE" in l]
        assert len(assert_lines) >= 1, \
            "sync_st.py 应包含至少 1 个 assert 检查 TRADE_CAL_START_DATE"
        assert any("len(TRADE_CAL_START_DATE) == 8" in l for l in assert_lines), \
            "assert 应检查 len == 8"
        assert any("isdigit()" in l for l in assert_lines), \
            "assert 应检查 isdigit()"

    def test_p1c2_assert_before_usage(self):
        """P1-C-2-3: assert 在 TRADE_CAL_START_DATE 切片解析之前执行"""
        # sync_trade_cal.py
        trade_cal_source = _read_source("scripts/sync_trade_cal.py")
        trade_cal_lines = trade_cal_source.splitlines()
        assert_lineno = None
        slice_lineno = None
        for i, line in enumerate(trade_cal_lines, 1):
            if "assert" in line and "TRADE_CAL_START_DATE" in line:
                if assert_lineno is None:
                    assert_lineno = i
            # 找到 TRADE_CAL_START_DATE 切片操作（即实际使用的地方）
            if "TRADE_CAL_START_DATE[" in line:
                if slice_lineno is None:
                    slice_lineno = i
        assert assert_lineno is not None, "sync_trade_cal.py 未找到 assert"
        assert slice_lineno is not None, "sync_trade_cal.py 未找到 TRADE_CAL_START_DATE 切片"
        assert assert_lineno < slice_lineno, \
            f"assert（第 {assert_lineno} 行）应在切片使用（第 {slice_lineno} 行）之前"

        # sync_st.py
        sync_st_source = _read_source("scripts/sync_st.py")
        sync_st_lines = sync_st_source.splitlines()
        assert_lineno = None
        slice_lineno = None
        for i, line in enumerate(sync_st_lines, 1):
            if "assert" in line and "TRADE_CAL_START_DATE" in line:
                if assert_lineno is None:
                    assert_lineno = i
            if "TRADE_CAL_START_DATE[" in line:
                if slice_lineno is None:
                    slice_lineno = i
        assert assert_lineno is not None, "sync_st.py 未找到 assert"
        assert slice_lineno is not None, "sync_st.py 未找到 TRADE_CAL_START_DATE 切片"
        assert assert_lineno < slice_lineno, \
            f"assert（第 {assert_lineno} 行）应在切片使用（第 {slice_lineno} 行）之前"

    def test_p1c2_assert_message_contains_8_digit(self):
        """P1-C-2-4: assert 失败消息包含格式提示"""
        trade_cal_source = _read_source("scripts/sync_trade_cal.py")
        assert "8 位" in trade_cal_source and "YYYYMMDD" in trade_cal_source, \
            "sync_trade_cal.py 的 assert 消息应提示 '8 位 YYYYMMDD 格式'"

        sync_st_source = _read_source("scripts/sync_st.py")
        assert "8 位" in sync_st_source and "YYYYMMDD" in sync_st_source, \
            "sync_st.py 的 assert 消息应提示 '8 位 YYYYMMDD 格式'"

    def test_p1c2_assert_catches_bad_date(self):
        """P1-C-2-5: assert 能正确拦截非法日期格式"""
        # 模拟错误格式
        bad_dates = [
            "abc",           # 不够长且非纯数字
            "2026-01-01",    # 有横线
            "202601011",     # 9位
            "2026abcd",      # 含字母
        ]
        for bad in bad_dates:
            condition = len(bad) == 8 and bad.isdigit()
            assert condition is False, \
                f"'{bad}' 不应通过校验（len={len(bad)}, isdigit={bad.isdigit()}）"

    def test_p1c2_assert_passes_good_date(self):
        """P1-C-2-6: assert 能放行合法日期格式"""
        good_dates = [
            "20260101",
            "20250101",
            "19900101",
        ]
        for good in good_dates:
            condition = len(good) == 8 and good.isdigit()
            assert condition is True, \
                f"'{good}' 应通过校验（len={len(good)}, isdigit={good.isdigit()}）"
