"""大盘指数日线数据全量拉取脚本

数据来源：Tushare Pro
接口：ts.pro_bar(asset='I')
  - 指数无复权概念，输出 price/volume/amount

输出：
  data/indices.parquet  → 指数日线数据（含全市场汇总）

使用方式：
  python scripts/fetch_indices.py
"""

import os
from datetime import date

import pandas as pd
import polars as pl
import tushare as ts

START_DATE = "20260105"  # 2026 年第一个交易日
END_DATE = date.today().strftime("%Y%m%d")
TUSHARE_TOKEN = "b9c84e9a50444ef4c497adf0681acfa59646a6ba89b03fd393fbd53a"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# Tushare 格式的指数代码
INDEX_LIST = {
    "000001.SH": "上证指数",
    "399106.SZ": "深证综指",
    "899050.BJ": "北证50",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000300.SH": "沪深300",
    "000016.SH": "上证50",
}

# 取消代理
for _key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_key, None)


def fetch_index(ts_code: str, name: str) -> pl.DataFrame | None:
    """拉取单只指数的日线数据。"""
    try:
        raw = ts.pro_bar(
            ts_code=ts_code,
            asset="I",
            start_date=START_DATE,
            end_date=END_DATE,
        )
    except Exception as e:
        print(f"  {name}({ts_code}) 拉取失败: {e}")
        return None

    if raw is None or raw.empty:
        return None

    # pro_bar 指数输出：ts_code, trade_date, close, open, high, low,
    #                    pre_close, change, pct_chg, vol, amount
    df = pl.DataFrame(raw.to_dict(orient="records"))

    code_6 = ts_code[:6]
    return df.select([
        pl.lit(code_6).alias("code"),
        pl.lit(name).alias("name"),
        pl.col("trade_date").str.to_date(format="%Y%m%d"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("vol").cast(pl.Int64).alias("volume"),
        pl.col("amount").cast(pl.Float64),
        pl.lit(0.0).alias("amplitude"),
        pl.col("pct_chg").cast(pl.Float64).alias("pct_change"),
        pl.lit(0.0).alias("turnover_rate"),
    ])


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    print(f"拉取大盘指数数据（{START_DATE} ~ {END_DATE}）...\n")

    frames = []
    for ts_code, name in INDEX_LIST.items():
        df = fetch_index(ts_code, name)
        if df is not None and len(df) > 0:
            frames.append(df)
            print(f"  {name}({ts_code}): {len(df)} 行, {df['trade_date'].min()} ~ {df['trade_date'].max()}")
        else:
            print(f"  {name}({ts_code}): 无数据")

    if not frames:
        print("没有拉取到任何数据。")
        return

    df = pl.concat(frames, how="vertical").sort(["trade_date", "code"])

    # 全市场汇总（沪 + 深 + 北 成交额之和）
    mk_codes = ["000001", "399106", "899050"]
    total = (
        df
        .filter(pl.col("code").is_in(mk_codes))
        .group_by("trade_date")
        .agg([
            pl.col("amount").sum().alias("amount"),
            pl.col("volume").sum().alias("volume"),
        ])
        .with_columns([
            pl.lit("999999").alias("code"),
            pl.lit("全市场").alias("name"),
            pl.lit(0.0).alias("open"),
            pl.lit(0.0).alias("high"),
            pl.lit(0.0).alias("low"),
            pl.lit(0.0).alias("close"),
            pl.lit(0.0).alias("amplitude"),
            pl.lit(0.0).alias("pct_change"),
            pl.lit(0.0).alias("turnover_rate"),
        ])
        .select(df.columns)
    )
    df = pl.concat([df, total], how="vertical").sort(["trade_date", "code"])

    path = os.path.join(DATA_DIR, "indices.parquet")
    df.write_parquet(path)

    print(f"\n  → {len(df)} 行，{df['code'].n_unique()} 只指数，已保存到 {path}")
    print("完成。")


if __name__ == "__main__":
    main()
