"""同步 stocks 相关测试——changelog detected_at 类型"""

import os
import tempfile
from datetime import date, datetime

import polars as pl
import pytest

from scripts.sync_stocks import generate_changelog, append_changelog
from config.constants import STOCKS_CHANGELOG_PATH


class TestGenerateChangelog:
    def test_generate_changelog_detected_at_is_date(self, sample_stocks_old, sample_stocks_new):
        """TC-SYNC-01: generate_changelog 产生 Date 类型的 detected_at"""
        changelog = generate_changelog(sample_stocks_old, sample_stocks_new)

        # 应有 changelog 行（新增 000002 移除、000001 改名）
        assert changelog.height > 0, "应有变更记录"

        # detected_at 类型应为 pl.Date
        assert changelog.schema["detected_at"] == pl.Date, \
            f"期望 pl.Date，实际 {changelog.schema['detected_at']}"

        # 值应为 date 类型（不是 datetime）
        for row in changelog.iter_rows():
            val = row[4]  # detected_at 是第 5 列（index 4）
            assert isinstance(val, date), f"期望 date 类型，实际 {type(val)}: {val}"
            assert not isinstance(val, datetime), "不应是 datetime 类型"

    def test_generate_changelog_no_change(self, sample_stocks_old):
        """TC1-4: 无变更时返回空 DataFrame"""
        changelog = generate_changelog(sample_stocks_old, sample_stocks_old)
        assert changelog.height == 0

    def test_append_changelog_backward_compat(self, sample_stocks_old, sample_stocks_new, monkeypatch):
        """TC-SYNC-02: append_changelog 兼容旧 pl.Datetime 数据

        模拟旧文件 detected_at 为 pl.Datetime 类型，验证 concat 成功。
        """
        from scripts.sync_stocks import append_changelog
        from config.constants import STOCKS_CHANGELOG_PATH

        # 用临时文件路径替换真实路径
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            tmp_path = f.name

        try:
            # 构造一个 detected_at 为 Datetime 类型的旧 DataFrame
            old_data = pl.DataFrame({
                "code": ["000001"],
                "field": ["name"],
                "old_value": ["旧名"],
                "new_value": ["新名"],
                "detected_at": [datetime(2026, 1, 1, 10, 30, 0)],  # Datetime 类型
            }, schema={
                "code": pl.Utf8,
                "field": pl.Utf8,
                "old_value": pl.Utf8,
                "new_value": pl.Utf8,
                "detected_at": pl.Datetime,
            })
            old_data.write_parquet(tmp_path)

            # 生成新 changelog（detected_at 为 Date 类型）
            new_changelog = generate_changelog(sample_stocks_old, sample_stocks_new)

            # 用 monkeypatch 替换路径
            monkeypatch.setattr("scripts.sync_stocks.STOCKS_CHANGELOG_PATH", tmp_path)

            # append 不应抛异常
            append_changelog(new_changelog)

            # 验证结果
            result = pl.read_parquet(tmp_path)
            assert result.height == old_data.height + new_changelog.height, \
                f"期望 {old_data.height + new_changelog.height} 行，实际 {result.height}"
            assert result.schema["detected_at"] == pl.Date, \
                f"合并后 detected_at 应为 pl.Date，实际 {result.schema['detected_at']}"

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_append_changelog_first_run(self, sample_stocks_old, sample_stocks_new, monkeypatch):
        """TC1-1: 首次运行（无旧文件）——append_changelog 写入成功，detected_at 为 pl.Date"""
        from scripts.sync_stocks import append_changelog

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            tmp_path = f.name

        try:
            # 确保临时文件不存在（首次运行）
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

            # 生成 changelog
            changelog_df = generate_changelog(sample_stocks_old, sample_stocks_new)
            assert changelog_df.height > 0

            monkeypatch.setattr("scripts.sync_stocks.STOCKS_CHANGELOG_PATH", tmp_path)

            # 首次 append 不应抛异常
            append_changelog(changelog_df)

            # 验证文件已创建
            assert os.path.exists(tmp_path), "首次运行应创建 changelog 文件"

            result = pl.read_parquet(tmp_path)
            assert result.schema["detected_at"] == pl.Date, \
                f"首次运行 detected_at 应为 pl.Date，实际 {result.schema['detected_at']}"
            assert result.height == changelog_df.height, "行数应与 changelog 一致"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_append_changelog_incremental_date(self, sample_stocks_old, sample_stocks_new, monkeypatch):
        """TC1-2: 增量追加（旧文件为 Date 类型）——连续运行两次不报错"""
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            tmp_path = f.name

        try:
            # NamedTemporaryFile 会创建空文件，需要先删除以便 append_changelog 首次写入
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

            monkeypatch.setattr("scripts.sync_stocks.STOCKS_CHANGELOG_PATH", tmp_path)

            # 第一次运行：写入初始 changelog
            changelog1 = generate_changelog(sample_stocks_old, sample_stocks_new)
            assert changelog1.height > 0
            append_changelog(changelog1)

            # 第二次运行：追加新 changelog（使用相同数据，会生成相同记录）
            changelog2 = generate_changelog(sample_stocks_old, sample_stocks_new)
            assert changelog2.height > 0

            # 第二次 append 不应抛异常
            append_changelog(changelog2)

            # 验证结果：两批数据合并，detected_at 仍为 pl.Date
            result = pl.read_parquet(tmp_path)
            assert result.schema["detected_at"] == pl.Date, \
                f"追加后 detected_at 应为 pl.Date，实际 {result.schema['detected_at']}"
            assert result.height == changelog1.height + changelog2.height, \
                f"两批数据应合并：{changelog1.height} + {changelog2.height} = {result.height}"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_append_changelog_idempotent_same_day(self, sample_stocks_old, sample_stocks_new, monkeypatch):
        """TC1-6: 同一天多次运行（幂等）——每次正常写入，无 schema 冲突"""
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            tmp_path = f.name

        try:
            # NamedTemporaryFile 会创建空文件，需要先删除以便首次写入
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

            monkeypatch.setattr("scripts.sync_stocks.STOCKS_CHANGELOG_PATH", tmp_path)

            # 运行 3 次，每次追加相同 changelog
            for i in range(3):
                changelog = generate_changelog(sample_stocks_old, sample_stocks_new)
                append_changelog(changelog)

                # 每次写入后检查文件可正常读取
                result = pl.read_parquet(tmp_path)
                assert result.schema["detected_at"] == pl.Date, \
                    f"第 {i+1} 次运行后 detected_at 应为 pl.Date"
                assert result.height == (i + 1) * changelog.height, \
                    f"第 {i+1} 次运行后应有 {(i+1)*changelog.height} 行"

            # 最终验证：schema 正确
            final = pl.read_parquet(tmp_path)
            assert final.schema["detected_at"] == pl.Date
            assert final.height == 3 * generate_changelog(sample_stocks_old, sample_stocks_new).height
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
