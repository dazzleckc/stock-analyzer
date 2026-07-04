"""股票列表更新脚本

每次全量拉取 Tushare stock_basic，与本地 stocks.parquet 比对差异，
格式化输出 新增 / 移除 / 名称变更，最后覆盖写入。

数据来源：Tushare Pro stock_basic
筛选逻辑：L/P 全保留，D 仅保留 delist_date > CUTOFF_DATE

使用方式：
  python scripts/fetch_stocks.py
"""

import os
import sys

import pandas as pd
import polars as pl
import tushare as ts

# 确保可以从脚本目录外导入 config 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.local import TUSHARE_TOKEN

# ── 配置 ──────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
STOCKS_PATH = os.path.join(DATA_DIR, "stocks.parquet")
CUTOFF_DATE = "20260105"          # 与 kline_daily 起始交易日对齐
FIELDS = "ts_code,symbol,name,list_status,list_date,delist_date"

for _key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_key, None)


def fetch_raw(pro) -> pl.DataFrame:
    """L + D + P 三态全量拉取，返回含完整字段的 DataFrame。"""
    frames = []
    for status in ("L", "D", "P"):
        df = pro.stock_basic(list_status=status, fields=FIELDS)
        if df is not None and len(df) > 0:
            frames.append(df)
    return pl.DataFrame(pd.concat(frames, ignore_index=True).to_dict(orient="records"))


def apply_filter(df: pl.DataFrame) -> pl.DataFrame:
    """按 CUTOFF_DATE 过滤 D 状态，输出 (code, name)。"""
    return (
        df
        .with_columns(pl.col("delist_date").cast(pl.Utf8).str.replace(r"\.0$", ""))
        .filter(
            pl.col("list_status").is_in(["L", "P"])
            |
            (
                (pl.col("list_status") == "D")
                & pl.col("delist_date").is_not_null()
                & (pl.col("delist_date") > CUTOFF_DATE)
            )
        )
        .select(pl.col("symbol").alias("code"), pl.col("name"))
        .with_columns(pl.col("code").cast(pl.Utf8).str.zfill(6))
        .sort("code")
    )


def diff(old: pl.DataFrame, new: pl.DataFrame):
    """对比新旧股票列表，格式化输出差异。"""
    old_map = dict(old.iter_rows())      # code → name
    new_map = dict(new.iter_rows())

    old_codes = set(old_map)
    new_codes = set(new_map)

    added = sorted(new_codes - old_codes)
    removed = sorted(old_codes - new_codes)
    renamed = [
        (c, old_map[c], new_map[c])
        for c in sorted(old_codes & new_codes)
        if old_map[c] != new_map[c]
    ]

    print(f"\n  本地 {len(old_codes):,} 只 → 最新 {len(new_codes):,} 只")

    if added:
        print(f"\n  ── 新增 {len(added)} 只 ──")
        for c in added:
            print(f"  + {c:6s}  {new_map[c]}")

    if removed:
        print(f"\n  ── 移除 {len(removed)} 只 ──")
        for c in removed:
            print(f"  - {c:6s}  {old_map[c]}")

    if renamed:
        print(f"\n  ── 名称变更 {len(renamed)} 只 ──")
        for c, old_name, new_name in renamed:
            print(f"  ~ {c:6s}  {old_name} → {new_name}")

    if not added and not removed and not renamed:
        print("  无变更")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # 1. 拉取全量
    pro = ts.pro_api(TUSHARE_TOKEN)
    print("拉取 stock_basic (L + D + P) ...")
    raw = fetch_raw(pro)
    print(f"  L + D + P 合计 {len(raw)} 只")

    # 2. 过滤
    print(f"\n筛选（L/P 全保留，D 仅 delist_date > {CUTOFF_DATE}）...")
    new = apply_filter(raw)
    print(f"  过滤后 {len(new)} 只")

    # 3. 加载本地数据、对比
    if os.path.exists(STOCKS_PATH):
        old = pl.read_parquet(STOCKS_PATH)
        diff(old, new)
    else:
        print(f"\n  首次运行，{len(new)} 只全部按新增处理")
        for c in sorted(new["code"].to_list()[:10]):
            print(f"  + {c:6s}  {new.filter(pl.col('code')==c)['name'][0]}")
        if len(new) > 10:
            print(f"  ... 共 {len(new)} 只")

    # 4. 覆盖写入
    new.write_parquet(STOCKS_PATH)
    print(f"\n  → 已保存到 {STOCKS_PATH}")
    print("完成。")


if __name__ == "__main__":
    main()
