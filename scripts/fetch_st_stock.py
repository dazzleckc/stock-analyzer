"""ST 股票数据获取脚本

数据来源：Tushare Pro（stock_st 接口）
参考：https://tushare.pro/document/2?doc_id=397

功能：
  1. 全量拉取：python scripts/fetch_st_stock.py
     从 20160101 到当前日期，按交易日逐批拉取全量 ST 股票数据
  2. 增量更新：python scripts/fetch_st_stock.py --update
     基于已有 st_stock.parquet，只拉取最新交易日的数据并追加

输出：data/st_stock.parquet
  字段：code, name, exchange, trade_date, type, type_name
  code  格式为 6 位数字（如 '300313'），与项目 stocks.parquet 一致
  exchange 为交易所后缀（SH / SZ / BJ），便于溯源
"""

import os
import sys
import argparse
import time
from datetime import date, timedelta

import pandas as pd
import polars as pl
import tushare as ts

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.local import TUSHARE_TOKEN

# ── 路径 ──────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
ST_STOCK_PATH = os.path.join(DATA_DIR, "st_stock.parquet")

# ── 配置 ──────────────────────────────────────────────
START_DATE = "20260101"          # 接口数据起始日期
MAX_RETRY = 3                    # 单次请求最大重试次数
BATCH_DAYS = 5                   # 批量拉取天数（单批 < 1000 行限制）
BATCH_INTERVAL = 0.5             # 批间间隔（秒）

# 取消代理（Tushare 不走本地代理）
for _key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_key, None)


# ── 核心函数 ──────────────────────────────────────────

def get_pro() -> ts.pro_api:
    """获取 Tushare Pro 接口实例。"""
    return ts.pro_api(TUSHARE_TOKEN)


def fetch_batch(pro, start: str, end: str) -> pl.DataFrame | None:
    """拉取[start, end]日期区间内的ST股票数据，自动处理分页。

    接口限制：单次请求最大返回 1000 行。
    """
    all_pages = []
    offset = 0
    limit = 1000

    for attempt in range(MAX_RETRY):
        try:
            while True:
                df = pro.stock_st(start_date=start, end_date=end,
                                  limit=limit, offset=offset)
                if df is None or df.empty:
                    break
                all_pages.append(df)
                if len(df) < limit:
                    break
                offset += limit
                time.sleep(0.3)
            break
        except Exception as e:
            if attempt < MAX_RETRY - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"    [错误] {start}~{end}: {e}")
                return None

    if not all_pages:
        return None

    raw = pd.concat(all_pages, ignore_index=True)

    # 字段标准化：ts_code → code + exchange，trade_date → Date
    pg = (
        pl.DataFrame(raw.to_dict(orient="records"))
        .with_columns([
            pl.col("ts_code").str.split(".").list.first().alias("code"),
            pl.col("ts_code").str.split(".").list.last().alias("exchange"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d"),
        ])
        .select(["code", "name", "exchange", "trade_date", "type", "type_name"])
    )
    return pg


def fetch_all(start: str, end: str) -> pl.DataFrame:
    """全量拉取[start, end]日期区间内的ST数据（批量拉取）。"""
    pro = get_pro()

    start_dt = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    end_dt = date(int(end[:4]), int(end[4:6]), int(end[6:]))
    total_days = (end_dt - start_dt).days + 1

    print(f"拉取 ST 数据：{start} ~ {end}（共约 {total_days} 个自然日）")

    frames = []
    current = start_dt
    day_count = 0

    while current <= end_dt:
        batch_end = min(current + timedelta(days=BATCH_DAYS - 1), end_dt)
        s = current.strftime("%Y%m%d")
        e = batch_end.strftime("%Y%m%d")

        df = fetch_batch(pro, s, e)
        if df is not None and len(df) > 0:
            frames.append(df)

        day_count += (batch_end - current).days + 1
        if day_count % 100 == 0:
            print(f"  已处理约 {day_count}/{total_days} 天...")

        current = batch_end + timedelta(days=1)
        time.sleep(BATCH_INTERVAL)

    if not frames:
        print("未获取到任何数据。")
        return pl.DataFrame()

    result = pl.concat(frames, how="vertical")
    n_dates = result["trade_date"].n_unique()
    print(f"  → 共 {n_dates} 个交易日，{len(result)} 行数据")
    return result.sort(["trade_date", "code"])


# ── 主入口 ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ST 股票数据获取脚本（Tushare）")
    parser.add_argument("--update", action="store_true",
                        help="增量更新模式：基于已有 parquet 只拉取缺失日期")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    if args.update:
        # ── 增量更新 ──
        if not os.path.exists(ST_STOCK_PATH):
            print("未找到 st_stock.parquet，请先运行全量拉取。")
            sys.exit(1)

        existing = pl.read_parquet(ST_STOCK_PATH)
        max_date = existing["trade_date"].max()
        today = date.today()

        start = (max_date + timedelta(days=1)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")

        if start > end:
            print(f"数据已是最新（最新日期：{max_date}），无需更新。")
            return

        print(f"增量更新模式：{start} ~ {end}")
        new_data = fetch_all(start, end)

        if new_data.is_empty():
            print("没有新数据需要写入。")
            return

        updated = (
            pl.concat([existing, new_data], how="vertical")
            .unique(subset=["code", "trade_date"], keep="last")
            .sort(["trade_date", "code"])
        )
        updated.write_parquet(ST_STOCK_PATH)
        print(f"  → 原 {len(existing)} 行，新增 {len(new_data)} 行，共 {len(updated)} 行")
        print(f"  → 日期范围：{updated['trade_date'].min()} ~ {updated['trade_date'].max()}")
    else:
        # ── 全量拉取 ──
        today = date.today()
        end = today.strftime("%Y%m%d")

        df = fetch_all(START_DATE, end)

        if df.is_empty():
            print("没有获取到数据。")
            sys.exit(1)

        df.write_parquet(ST_STOCK_PATH)
        print(f"  → 已保存到 {ST_STOCK_PATH}")
        print(f"  → 日期范围：{df['trade_date'].min()} ~ {df['trade_date'].max()}")

    print("完成。")


if __name__ == "__main__":
    main()
