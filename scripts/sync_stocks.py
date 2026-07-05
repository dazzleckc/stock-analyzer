"""股票列表同步脚本

全量拉取 Tushare stock_basic，与本地 stocks.parquet 比对差异，
生成 changelog 记录到 stocks_changelog.parquet，最后 merge-upsert 写入。

数据来源：Tushare Pro stock_basic
筛选逻辑：L/P 全保留，D 仅保留 delist_date > CUTOFF_DATE

使用方式：
  python scripts/sync_stocks.py              # 增量（默认）
  python scripts/sync_stocks.py --full        # 全量初始化
  python scripts/sync_stocks.py --dry-run     # 仅 diff，不写入
"""

import argparse
import os
import sys
from datetime import datetime

import pandas as pd
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (                              # noqa: E402
    STOCKS_PATH, STOCKS_CHANGELOG_PATH, CUTOFF_DATE,
    STOCKS_COLUMNS, STOCKS_SCHEMA, CHANGELOG_COLUMNS, CHANGELOG_SCHEMA,
    get_pro, full_merge_upsert, atomic_write_parquet,
    validate_no_null, validate_unique,
)

FIELDS = "ts_code,symbol,name,list_status,list_date,delist_date"


# ═══════════════════════════════════════════════════════════════════
# 数据拉取
# ═══════════════════════════════════════════════════════════════════

def fetch_stock_basic_all(pro) -> pl.DataFrame:
    """L + D + P 三态全量拉取，返回含完整字段的 DataFrame。"""
    frames = []
    for status in ("L", "D", "P"):
        df = pro.stock_basic(list_status=status, fields=FIELDS)
        if df is not None and len(df) > 0:
            frames.append(df)
    return pl.DataFrame(
        pd.concat(frames, ignore_index=True).to_dict(orient="records")
    )


# ═══════════════════════════════════════════════════════════════════
# 数据转换
# ═══════════════════════════════════════════════════════════════════

def transform(raw: pl.DataFrame) -> pl.DataFrame:
    """ts_code→code，清理 delist_date .0 后缀，保留核心列。"""
    return (
        raw
        .with_columns(
            pl.col("symbol").cast(pl.Utf8).str.zfill(6).alias("code"),
            pl.col("delist_date").cast(pl.Utf8).str.replace(r"\.0$", ""),
        )
        .select(STOCKS_COLUMNS)
        .sort("code")
    )


# ═══════════════════════════════════════════════════════════════════
# 过滤 & 校验
# ═══════════════════════════════════════════════════════════════════

def apply_filter(df: pl.DataFrame) -> pl.DataFrame:
    """L/P 全保留，D 仅保留 delist_date > CUTOFF_DATE 且非空。"""
    return df.filter(
        pl.col("list_status").is_in(["L", "P"])
        |
        (
            (pl.col("list_status") == "D")
            & pl.col("delist_date").is_not_null()
            & (pl.col("delist_date") > CUTOFF_DATE)
        )
    )


def validate(df: pl.DataFrame) -> None:
    """确保 code 非空无重复、list_status 取值合法。"""
    validate_no_null(df, ["code"])
    validate_unique(df, ["code"])
    invalid = df.filter(~pl.col("list_status").is_in(["L", "D", "P"]))
    if invalid.height > 0:
        raise ValueError(
            f"list_status 含非法值: {invalid['list_status'].unique().to_list()}"
        )


# ═══════════════════════════════════════════════════════════════════
# Changelog
# ═══════════════════════════════════════════════════════════════════

def generate_changelog(old: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    """比较新旧 DataFrame，生成 changelog 记录。

    ┌──────────────┬─────────────────────┬───────────┬───────────┐
    │ field        │ 触发条件            │ old_value │ new_value │
    ├──────────────┼─────────────────────┼───────────┼───────────┤
    │ _new_        │ code 在新不在旧     │ None      │ name      │
    │ _removed_    │ code 在旧不在新     │ name      │ None      │
    │ name         │ code 同名不同值     │ 旧名称    │ 新名称    │
    │ list_status  │ code 同列不同值     │ 旧状态    │ 新状态    │
    │ delist_date  │ code 同列不同值     │ 旧日期    │ 新日期    │
    └──────────────┴─────────────────────┴───────────┴───────────┘
    """
    old_codes = set(old["code"].to_list())
    new_codes = set(new["code"].to_list())
    old_map = {r[0]: r for r in old.iter_rows()}
    new_map = {r[0]: r for r in new.iter_rows()}
    col_names = old.columns
    detected_at = datetime.now()
    rows = []

    # 新增
    for code in sorted(new_codes - old_codes):
        name = new_map[code][col_names.index("name")]
        rows.append((code, "_new_", None, name, detected_at))

    # 移除
    for code in sorted(old_codes - new_codes):
        name = old_map[code][col_names.index("name")]
        rows.append((code, "_removed_", name, None, detected_at))

    # 字段变更（共同 code，逐字段比较）
    field_indices = {
        f: col_names.index(f)
        for f in ("name", "list_status", "delist_date")
    }
    for code in sorted(old_codes & new_codes):
        old_row = old_map[code]
        new_row = new_map[code]
        for field, idx in field_indices.items():
            ov = old_row[idx]
            nv = new_row[idx]
            if ov != nv:
                rows.append((code, field, ov, nv, detected_at))

    if not rows:
        return pl.DataFrame(schema=CHANGELOG_SCHEMA)

    return pl.DataFrame(rows, schema=CHANGELOG_SCHEMA, orient="row")


def append_changelog(changelog_df: pl.DataFrame) -> None:
    """追加 changelog 到 stocks_changelog.parquet，空则跳过。"""
    if changelog_df.height == 0:
        return

    if os.path.exists(STOCKS_CHANGELOG_PATH):
        existing = pl.read_parquet(STOCKS_CHANGELOG_PATH)
        changelog_df = pl.concat([existing, changelog_df], how="vertical")

    atomic_write_parquet(changelog_df, STOCKS_CHANGELOG_PATH)


# ═══════════════════════════════════════════════════════════════════
# Diff 打印
# ═══════════════════════════════════════════════════════════════════

def print_diff(old: pl.DataFrame, new: pl.DataFrame, changelog_df: pl.DataFrame):
    """格式化输出差异，风格对齐 fetch_stocks.py。"""
    print(f"\n  本地 {old.height:,} 只 → 最新 {new.height:,} 只")

    added = changelog_df.filter(pl.col("field") == "_new_")
    removed = changelog_df.filter(pl.col("field") == "_removed_")
    renamed = changelog_df.filter(pl.col("field") == "name")
    status_changes = changelog_df.filter(pl.col("field") == "list_status")
    delist_changes = changelog_df.filter(pl.col("field") == "delist_date")

    if added.height > 0:
        print(f"\n  ── 新增 {added.height} 只 ──")
        for row in added.iter_rows():
            print(f"  + {row[0]:6s}  {row[3]}")

    if removed.height > 0:
        print(f"\n  ── 移除 {removed.height} 只 ──")
        for row in removed.iter_rows():
            print(f"  - {row[0]:6s}  {row[2]}")

    if renamed.height > 0:
        print(f"\n  ── 名称变更 {renamed.height} 只 ──")
        for row in renamed.iter_rows():
            print(f"  ~ {row[0]:6s}  {row[2]} → {row[3]}")

    if status_changes.height > 0:
        print(f"\n  ── 状态变更 {status_changes.height} 只 ──")
        for row in status_changes.iter_rows():
            print(f"  ~ {row[0]:6s}  {row[2] or '(无)'} → {row[3] or '(无)'}")

    if delist_changes.height > 0:
        print(f"\n  ── 退市日期变更 {delist_changes.height} 只 ──")
        for row in delist_changes.iter_rows():
            print(f"  ~ {row[0]:6s}  {row[2] or '(无)'} → {row[3] or '(无)'}")

    if changelog_df.height == 0:
        print("  无变更")


# ═══════════════════════════════════════════════════════════════════
# Schema 兼容
# ═══════════════════════════════════════════════════════════════════

def _check_schema_compat(existing: pl.DataFrame) -> bool:
    """检查已有 stocks.parquet schema 是否含全部必要列。"""
    missing = [c for c in STOCKS_COLUMNS if c not in existing.columns]
    if missing:
        print(f"  ⚠ 旧 stocks.parquet 缺少列 {missing}，触发全量重建")
        return False
    return True


# ═══════════════════════════════════════════════════════════════════
# 核心函数：sync_full / sync_incremental（两个独立函数）
# ═══════════════════════════════════════════════════════════════════

def sync_full() -> dict:
    """全量初始化：拉取 + 过滤 + 写入 + 生成完整 changelog。

    返回: {"new": int, "updated": int, "removed": int, "changelog_rows": int}
    """
    pro = get_pro()
    print("拉取 stock_basic (L + D + P) ...")
    raw = fetch_stock_basic_all(pro)
    print(f"  L + D + P 合计 {len(raw)} 只")

    print(f"\n转换 & 筛选（L/P 全保留，D 仅 delist_date > {CUTOFF_DATE}）...")
    df = transform(raw)
    df = apply_filter(df)
    validate(df)
    print(f"  过滤后 {df.height} 只")

    new_count = df.height
    updated = 0
    removed = 0
    changelog_rows = 0

    if os.path.exists(STOCKS_PATH):
        old = pl.read_parquet(STOCKS_PATH)
        if not _check_schema_compat(old):
            # Schema 不兼容 → 全量重建（旧数据无法比对，视为过时）
            removed = old.height
            print(f"\n  ── 全量重建（Schema 迁移）──")
            print(f"  新增 {new_count} 只（原 {removed} 条记录已过时）")
        else:
            changelog_df = generate_changelog(old, df)
            print_diff(old, df, changelog_df)
            append_changelog(changelog_df)
            changelog_rows = changelog_df.height
            new_count = changelog_df.filter(pl.col("field") == "_new_").height
            removed = changelog_df.filter(pl.col("field") == "_removed_").height
            updated = _count_updated(changelog_df)
    else:
        print(f"\n  首次运行，{df.height} 只全部按新增处理")
        for row in df.head(10).iter_rows():
            print(f"  + {row[0]:6s}  {row[1]}")
        if df.height > 10:
            print(f"  ... 共 {df.height} 只")

    full_merge_upsert(df, STOCKS_PATH, ["code"])
    print(f"\n  → 已保存到 {STOCKS_PATH}")

    return {"new": new_count, "updated": updated,
            "removed": removed, "changelog_rows": changelog_rows}


def sync_incremental() -> dict:
    """增量同步：必须有已有文件，同比 full 对比差异。

    返回: {"new": int, "updated": int, "removed": int, "changelog_rows": int}
    """
    if not os.path.exists(STOCKS_PATH):
        print("⚠ 无本地 stocks.parquet，切换为全量模式\n")
        return sync_full()

    pro = get_pro()
    print("拉取 stock_basic (L + D + P) ...")
    raw = fetch_stock_basic_all(pro)
    print(f"  L + D + P 合计 {len(raw)} 只")

    print(f"\n转换 & 筛选（L/P 全保留，D 仅 delist_date > {CUTOFF_DATE}）...")
    df = transform(raw)
    df = apply_filter(df)
    validate(df)
    print(f"  过滤后 {df.height} 只")

    old = pl.read_parquet(STOCKS_PATH)
    if not _check_schema_compat(old):
        print()
        return sync_full()

    changelog_df = generate_changelog(old, df)
    print_diff(old, df, changelog_df)

    new_count = changelog_df.filter(pl.col("field") == "_new_").height
    removed = changelog_df.filter(pl.col("field") == "_removed_").height
    updated = _count_updated(changelog_df)
    changelog_rows = changelog_df.height

    append_changelog(changelog_df)
    full_merge_upsert(df, STOCKS_PATH, ["code"])
    print(f"\n  → 已保存到 {STOCKS_PATH}")

    return {"new": new_count, "updated": updated,
            "removed": removed, "changelog_rows": changelog_rows}


def _count_updated(changelog_df: pl.DataFrame) -> int:
    """从 changelog 统计有多少个 code 发生了字段变更（不含新增/移除）。"""
    return (
        changelog_df
        .filter(~pl.col("field").is_in(["_new_", "_removed_"]))
        .select("code")
        .unique()
        .height
    )


# ═══════════════════════════════════════════════════════════════════
# Dry-Run
# ═══════════════════════════════════════════════════════════════════

def dry_run() -> dict:
    """仅拉取 + 对比 + 打印，不写任何文件。"""
    pro = get_pro()
    print("拉取 stock_basic (L + D + P) ...")
    raw = fetch_stock_basic_all(pro)
    print(f"  L + D + P 合计 {len(raw)} 只")

    print(f"\n转换 & 筛选（L/P 全保留，D 仅 delist_date > {CUTOFF_DATE}）...")
    df = transform(raw)
    df = apply_filter(df)
    validate(df)
    print(f"  过滤后 {df.height} 只")

    new_count = df.height
    updated = 0
    removed = 0
    changelog_rows = 0

    if os.path.exists(STOCKS_PATH):
        old = pl.read_parquet(STOCKS_PATH)
        if _check_schema_compat(old):
            changelog_df = generate_changelog(old, df)
            print_diff(old, df, changelog_df)
            new_count = changelog_df.filter(pl.col("field") == "_new_").height
            removed = changelog_df.filter(pl.col("field") == "_removed_").height
            updated = _count_updated(changelog_df)
            changelog_rows = changelog_df.height
        else:
            print("\n  ⚠ 旧 stocks.parquet schema 不兼容，无法 diff")
    else:
        print(f"\n  本地无 stocks.parquet，{df.height} 只全部按新增处理")

    print("\n  [dry-run] 未写入任何文件")
    return {"new": new_count, "updated": updated,
            "removed": removed, "changelog_rows": changelog_rows}


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="股票列表同步：拉取 stock_basic → 过滤 → diff → 写入 stocks + changelog"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="全量初始化模式"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅拉取和对比，不写入任何文件"
    )
    args = parser.parse_args()

    if args.dry_run:
        result = dry_run()
    elif args.full:
        result = sync_full()
    else:
        result = sync_incremental()

    print(
        f"\n完成。新增 {result['new']}，更新 {result['updated']}，"
        f"移除 {result['removed']}，changelog {result['changelog_rows']} 条"
    )


if __name__ == "__main__":
    main()
