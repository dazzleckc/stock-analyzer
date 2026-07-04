"""A 股日线数据增量更新脚本

基于已有的 kline_daily.parquet，只拉取最新交易日的数据并追加。

使用方式：
  python scripts/update_kline.py
"""

import os
import random
import time
from datetime import date, timedelta

import polars as pl
from fetch_kline import (  # noqa: E402 - 延迟 import 确保 tushare 初始化在先
    fetch_single_stock,
    DATA_DIR,
    STOCKS_PATH,
    ensure_dirs,
)

# Tushare 代理清理（fetch_kline 已做，这里冗余保证）
for _key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_key, None)


def main():
    kline_path = os.path.join(DATA_DIR, "kline_daily.parquet")

    if not os.path.exists(kline_path):
        print("未找到 kline_daily.parquet，请先运行 fetch_kline.py 做全量初始化。")
        return

    ensure_dirs()

    # 加载已有数据和股票列表
    existing = pl.read_parquet(kline_path)
    stocks = pl.read_parquet(STOCKS_PATH)
    last_date = existing["trade_date"].max()
    today = date.today()
    tomorrow = today + timedelta(days=1)

    start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
    end = tomorrow.strftime("%Y%m%d")

    if start > end:
        print(f"数据已是最新（最新日期：{last_date}），无需更新。")
        return

    print(f"增量更新：{start} ~ {end}（现有最新：{last_date}）")

    # 逐只拉取增量
    codes = sorted(stocks["code"].to_list())
    new_frames = []
    success = failed = 0

    for code in codes:
        df = fetch_single_stock(code, start_date=start, end_date=end)
        if df is not None and len(df) > 0:
            new_frames.append(df)
            success += 1
        else:
            failed += 1
        time.sleep(0.3 + random.uniform(0.0, 0.2))

    print(f"  → 有新增数据 {success} 只，无数据 {failed} 只")

    if not new_frames:
        print("没有新数据需要写入。")
        return

    # 合并新旧，按主键去重
    new_data = pl.concat(new_frames, how="vertical")
    updated = (
        pl.concat([existing, new_data], how="vertical")
        .unique(subset=["code", "trade_date"], keep="last")
        .sort(["trade_date", "code"])
    )

    updated.write_parquet(kline_path)

    new_dates = new_data["trade_date"].unique().to_list()
    print(f"  → 新增 {len(new_dates)} 个交易日，共 {len(updated)} 行")
    print(f"  → 日期范围：{updated['trade_date'].min()} ~ {updated['trade_date'].max()}")
    print("完成。")


if __name__ == "__main__":
    main()
