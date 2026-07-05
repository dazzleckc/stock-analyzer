"""
数据文件 I/O 工具：原子写入、truncate-and-insert、upsert、数据校验。

所有写入操作都遵循"先写临时文件，成功再 rename"原则，
防止写入过程中崩溃导致文件损坏。
"""

import datetime
import os
import tempfile
from typing import List, Union

import polars as pl


# ── 原子写入 ──────────────────────────────────────

def atomic_write_parquet(df: pl.DataFrame, target_path: str):
    """
    原子写入 Parquet 文件。

    流程:
      1. 在同目录创建 .tmp 临时文件
      2. 写入数据到临时文件
      3. os.replace() 原子替换目标文件（POSIX 保证原子性）

    即使写入过程中进程崩溃，也只留下可清理的 .tmp 文件，
    不会损坏已有的 parquet。
    """
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    # 使用 mkstemp 在同目录创建（保证 rename 在同文件系统）
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", prefix=".", dir=target_dir
    )
    try:
        os.close(fd)
        df.write_parquet(tmp_path)
        os.replace(tmp_path, target_path)  # POSIX: 原子替换
    except Exception:
        # 清理残留临时文件
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ── 幂等策略 ──────────────────────────────────────

def truncate_and_insert(
    df: pl.DataFrame,
    target_path: str,
    key_column: str,
    key_values: List,
):
    """
    截断 + 插入：删除本地数据中匹配 key_values 的行，追加新行。

    适用于 kline（按 trade_date 覆盖）、st_stock（按 trade_date 覆盖）、
    delist_period（按 imp_date 覆盖）。

    参数:
      df: 新数据
      target_path: 目标 parquet 路径
      key_column: 用于筛选的列名（如 'trade_date', 'imp_date'）
      key_values: 要删除/替换的键值列表

    示例:
      # 覆盖 kline 中 2026-07-04 和 2026-07-05 的数据
      truncate_and_insert(new_kline, KLINE_PATH,
                         "trade_date", ["2026-07-04", "2026-07-05"])
    """
    if not os.path.exists(target_path):
        # 文件不存在 → 直接写入
        atomic_write_parquet(df, target_path)
        return

    existing = pl.read_parquet(target_path)

    # 类型转换：确保 key_values 与目标列类型匹配
    col_dtype = existing[key_column].dtype
    if col_dtype == pl.Date:
        key_values = [
            datetime.date(int(v[:4]), int(v[5:7]), int(v[8:10]))
            if isinstance(v, str) else v
            for v in key_values
        ]

    # 删除匹配的旧行
    remaining = existing.filter(~pl.col(key_column).is_in(key_values))

    # 追加新行
    merged = pl.concat([remaining, df], how="vertical")

    # 排序（如有 trade_date / imp_date 列）
    sort_cols = []
    for col in ["trade_date", "imp_date"]:
        if col in merged.columns:
            sort_cols.append(col)
    if "code" in merged.columns:
        sort_cols.append("code")
    if sort_cols:
        merged = merged.sort(sort_cols)

    atomic_write_parquet(merged, target_path)


def full_merge_upsert(
    df: pl.DataFrame,
    target_path: str,
    primary_keys: List[str],
):
    """
    全量合并（upsert）：读入已有数据，按主键去重合并，原子写入。

    适用于 stocks.parquet（全量拉取覆盖，主键 code）。

    去重策略: keep='last' —— 新数据覆盖旧数据。
    """
    if not os.path.exists(target_path):
        atomic_write_parquet(df, target_path)
        return

    existing = pl.read_parquet(target_path)
    merged = (
        pl.concat([existing, df], how="vertical")
        .unique(subset=primary_keys, keep="last")
        .sort(primary_keys)
    )
    atomic_write_parquet(merged, target_path)


# ── 数据校验 ──────────────────────────────────────

def validate_no_null(df: pl.DataFrame, columns: List[str]) -> None:
    """确保指定列不含 null。"""
    for col in columns:
        if col not in df.columns:
            raise ValueError(f"缺少必要列: {col}")
        null_count = df[col].null_count()
        if null_count > 0:
            raise ValueError(f"列 {col} 包含 {null_count} 个 null")


def validate_unique(df: pl.DataFrame, subset: List[str]) -> None:
    """确保指定列组合唯一。"""
    if df.height != df.unique(subset=subset).height:
        raise ValueError(f"主键 {subset} 存在重复行")
