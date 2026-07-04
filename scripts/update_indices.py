"""大盘指数日线数据增量更新脚本

基于已有的 indices.parquet，只拉取最新交易日的数据并追加。

使用方式：
  python scripts/update_indices.py
"""

import os
from datetime import date, timedelta

import polars as pl

from fetch_indices import fetch_index, INDEX_LIST, DATA_DIR


def main():
    path = os.path.join(DATA_DIR, "indices.parquet")

    if not os.path.exists(path):
        print("未找到 indices.parquet，请先运行 fetch_indices.py 做全量初始化。")
        return

    existing = pl.read_parquet(path)
    last_date = existing["trade_date"].max()
    today = date.today()
    tomorrow = today + timedelta(days=1)

    start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
    end = tomorrow.strftime("%Y%m%d")

    if start > end:
        print(f"数据已是最新（最新日期：{last_date}），无需更新。")
        return

    print(f"增量更新：{start} ~ {end}（现有最新：{last_date}）")

    new_frames = []
    for ts_code, name in INDEX_LIST.items():
        df = fetch_index(ts_code, name)
        if df is not None and len(df) > 0:
            new_frames.append(df)
            print(f"  {name}({ts_code}): +{len(df)} 行")
        else:
            print(f"  {name}({ts_code}): 无新数据")

    if not new_frames:
        print("没有新数据需要写入。")
        return

    new_data = pl.concat(new_frames, how="vertical")
    updated = (
        pl.concat([existing, new_data], how="vertical")
        .unique(subset=["code", "trade_date"], keep="last")
        .sort(["trade_date", "code"])
    )

    # 重新计算全市场汇总
    mk_codes = ["000001", "399106", "899050"]
    old_without_mk = updated.filter(~pl.col("code").is_in(["999999"]))
    total = (
        updated
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
        .select(updated.columns)
    )
    updated = pl.concat([old_without_mk, total], how="vertical").sort(["trade_date", "code"])

    updated.write_parquet(path)

    new_dates = new_data["trade_date"].unique().to_list()
    print(f"  → 新增 {len(new_dates)} 个交易日，共 {len(updated)} 行")
    print(f"  → 日期范围：{updated['trade_date'].min()} ~ {updated['trade_date'].max()}")
    print("完成。")


if __name__ == "__main__":
    main()
