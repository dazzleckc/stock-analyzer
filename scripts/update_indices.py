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

    print(f"增量更新：{start} ~ {end}（最新数据：{last_date}）")

    new_frames = []
    for code, name in INDEX_LIST.items():
        df = fetch_index(code, name)
        if df is not None and len(df) > 0:
            new_frames.append(df)
            new_rows = len(df)
            print(f"  {name}({code}): +{new_rows} 行")
        else:
            print(f"  {name}({code}): 无新数据")

    if not new_frames:
        print("没有新数据需要写入。")
        return

    new_data = pl.concat(new_frames, how="vertical")
    updated = (
        pl.concat([existing, new_data], how="vertical")
        .unique(subset=["code", "trade_date"], keep="last")
        .sort(["trade_date", "code"])
    )

    updated.write_parquet(path)

    new_dates = new_data["trade_date"].unique().to_list()
    print(f"\n  → 新增 {len(new_dates)} 个交易日，共 {len(updated)} 行")
    print(f"  → 日期范围：{updated['trade_date'].min()} ~ {updated['trade_date'].max()}")
    print("完成。")


if __name__ == "__main__":
    main()
