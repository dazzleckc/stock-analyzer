"""大盘指数日线数据全量拉取脚本

数据来源：AKShare（东方财富）
接口：ak.index_zh_a_hist()

输出：
  data/indices.parquet  → 指数日线数据

使用方式：
  python scripts/fetch_indices.py
"""

import os
from datetime import date

import akshare as ak
import polars as pl

START_DATE = "20260101"
END_DATE = date.today().strftime("%Y%m%d")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

INDEX_LIST = {
    "000001": "上证指数",
    "399106": "深证综指",
    "899050": "北证50",
    "399001": "深证成指",
    "399006": "创业板指",
    "000688": "科创50",
    "000300": "沪深300",
    "000016": "上证50",
}

COLUMN_MAP = {
    "日期": "trade_date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct_change",
    "换手率": "turnover_rate",
}


def fetch_index(code: str, name: str) -> pl.DataFrame | None:
    """拉取单只指数的日线数据。"""
    try:
        raw = ak.index_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=START_DATE,
            end_date=END_DATE,
        )
    except Exception as e:
        print(f"  {name}({code}) 拉取失败: {e}")
        return None

    if raw is None or raw.empty:
        return None

    df = (
        pl.DataFrame(raw.to_dict(orient="records"))
        .rename(COLUMN_MAP)
        .select(list(COLUMN_MAP.values()))
        .with_columns([
            pl.lit(code).alias("code"),
            pl.lit(name).alias("name"),
            pl.col("trade_date").cast(pl.Date),
            pl.col("volume").cast(pl.Int64),
            pl.col("open", "high", "low", "close", "amount",
                   "amplitude", "pct_change", "turnover_rate").cast(pl.Float64),
        ])
    )

    return df


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    print(f"拉取大盘指数数据（{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]} ~ {END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:]}）...\n")

    frames = []
    for code, name in INDEX_LIST.items():
        df = fetch_index(code, name)
        if df is not None and len(df) > 0:
            frames.append(df)
            print(f"  {name}({code}): {len(df)} 行, {df['trade_date'].min()} ~ {df['trade_date'].max()}")
        else:
            print(f"  {name}({code}): 无数据")

    if not frames:
        print("没有拉取到任何数据。")
        return

    df = pl.concat(frames, how="vertical").sort(["trade_date", "code"])

    # 计算全市场总成交额（沪+深+北）
    total = (
        df
        .filter(pl.col("code").is_in(["000001", "399106", "899050"]))
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
