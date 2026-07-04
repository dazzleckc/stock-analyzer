"""股票列表更新脚本（Tushare stock_basic）

数据来源：Tushare Pro
接口：stock_basic（每次最多返回 6000 行，调取一次即可拉全）

筛选逻辑（与 kline_daily.parquet 的 2026-01-05 第一个交易日对齐）：
  - list_status='L'  正常上市          → 全部保留
  - list_status='D'  已退市            → 仅保留 delist_date > '20260105'
  - list_status='P'  暂停上市          → 全部保留（可能复牌）
  - list_status='G'  过会未交易        → 排除（尚无日K）

输出：data/stocks.parquet
  字段：code, name（与原 AKShare 生成的格式一致）
"""

import os

import pandas as pd
import polars as pl
import tushare as ts

from config.local import TUSHARE_TOKEN

# ── 配置 ──────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
STOCKS_PATH = os.path.join(DATA_DIR, "stocks.parquet")
CUTOFF_DATE = "20260105"          # 2026 年第一个交易日，与 kline_daily 对齐
FIELDS = "ts_code,symbol,name,list_status,list_date,delist_date"

# 取消代理（Tushare 不走本地代理）
for _key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_key, None)


def fetch_stock_basic(pro) -> pl.DataFrame:
    """拉取全市场股票基础信息（L + D + P 三种状态），返回 Polars DataFrame。"""
    frames = []
    for status in ("L", "D", "P"):
        df = pro.stock_basic(list_status=status, fields=FIELDS)
        if df is not None and len(df) > 0:
            frames.append(df)
            print(f"  list_status={status}: {len(df)} 只")

    raw = pd.concat(frames, ignore_index=True)
    return pl.DataFrame(raw.to_dict(orient="records"))


def filter_stocks(df: pl.DataFrame) -> pl.DataFrame:
    """按筛选逻辑过滤，返回仅含 code, name 的 DataFrame。"""
    total = len(df)

    result = (
        df
        .with_columns(pl.col("delist_date").cast(pl.Utf8).str.replace(r"\.0$", ""))
        .filter(
            # 正常上市 / 暂停上市 → 全保留
            pl.col("list_status").is_in(["L", "P"])
            |
            # 已退市但退市日在截止日之后 → 保留
            (
                (pl.col("list_status") == "D")
                & pl.col("delist_date").is_not_null()
                & (pl.col("delist_date") > CUTOFF_DATE)
            )
        )
        .select([
            pl.col("symbol").alias("code"),
            pl.col("name"),
        ])
        .with_columns(pl.col("code").cast(pl.Utf8).str.zfill(6))
        .sort("code")
    )

    excluded = total - len(result)
    print(f"  筛选结果：保留 {len(result)} 只，排除 {excluded} 只（{CUTOFF_DATE[:4]}-{CUTOFF_DATE[4:6]}-{CUTOFF_DATE[6:]} 前退市）")
    return result


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    pro = ts.pro_api(TUSHARE_TOKEN)

    print("拉取 stock_basic 数据...")
    df = fetch_stock_basic(pro)

    print(f"\n筛选（保留正常上市 + 退市日在 {CUTOFF_DATE} 之后）...")
    stocks = filter_stocks(df)

    stocks.write_parquet(STOCKS_PATH)
    print(f"\n  → 已保存到 {STOCKS_PATH}")
    print(f"  → 共 {len(stocks)} 只股票")
    print("完成。")


if __name__ == "__main__":
    main()
