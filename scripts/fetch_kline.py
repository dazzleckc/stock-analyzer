"""A 股日线数据采集脚本

数据来源：AKShare（东方财富）
接口：
  - ak.stock_info_a_code_name() → 全量 A 股代码与名称
  - ak.stock_zh_a_hist()        → 单只股票历史日线（后复权）

输出：
  data/stocks.parquet       → 股票列表（code, name）
  data/kline_daily.parquet  → 日线数据（11 个字段）

使用方式：
  python scripts/fetch_kline.py
"""

import os
import random
import sys
import time
from datetime import date

import akshare as ak
import polars as pl
from tqdm import tqdm

# 配置
START_DATE = "20260101"
END_DATE = date.today().strftime("%Y%m%d")
ADJUST = "qfq"  # 前复权
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")

# 目标字段映射：AKShare 中文列名 → 英文列名
COLUMN_MAP = {
    "日期": "trade_date",
    "股票代码": "code",
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


def ensure_dirs():
    """确保数据目录存在。"""
    for d in (DATA_DIR, RAW_DIR):
        os.makedirs(d, exist_ok=True)


def get_stock_list() -> pl.DataFrame:
    """获取全量 A 股列表，写入 data/stocks.parquet。"""
    print("[1/3] 获取股票列表...")

    raw = ak.stock_info_a_code_name()
    df = pl.DataFrame(raw.to_dict(orient="records"))
    df = df.rename({"code": "code", "name": "name"})

    # 确保 code 为纯数字 6 位字符串
    df = df.with_columns(
        pl.col("code").cast(pl.Utf8).str.strip_chars().str.zfill(6)
    )

    path = os.path.join(DATA_DIR, "stocks.parquet")
    df.write_parquet(path)
    print(f"  → 共 {len(df)} 只股票，已保存到 {path}")
    return df


def transform(raw) -> pl.DataFrame | None:
    """将 AKShare 原始数据转为标准 Polars DataFrame。"""
    if raw is None or raw.empty:
        return None

    # to_dict 绕过 pandas nullable 类型（无需 pyarrow）
    df = (
        pl.DataFrame(raw.to_dict(orient="records"))
        .rename(COLUMN_MAP)
        .select(list(COLUMN_MAP.values()))
    )

    return df.with_columns([
        pl.col("code").cast(pl.Utf8).str.strip_chars().str.zfill(6),
        pl.col("trade_date").cast(pl.Date),
        pl.col("volume").cast(pl.Int64),
        pl.col("open", "high", "low", "close", "amount", "amplitude",
               "pct_change", "turnover_rate").cast(pl.Float64),
    ])


def fetch_single_stock(code: str, start_date: str = START_DATE,
                       end_date: str = END_DATE) -> pl.DataFrame | None:
    """拉取单只股票的日线数据，返回 Polars DataFrame。"""
    try:
        raw = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=ADJUST,
        )
    except Exception:
        return None

    return transform(raw)


def fetch_all(stock_df: pl.DataFrame):
    """批量下载日线数据，逐只存为 data/raw/{code}.parquet，支持断点续传。"""
    print(f"\n[2/3] 下载日线数据（{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]} ~ {END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:]}）...")

    codes = stock_df["code"].to_list()
    success = 0
    skipped = 0
    failed = 0

    for code in tqdm(codes, desc="下载进度"):
        file_path = os.path.join(RAW_DIR, f"{code}.parquet")

        if os.path.exists(file_path):
            skipped += 1
            continue

        df = fetch_single_stock(code)
        if df is not None and len(df) > 0:
            df.write_parquet(file_path)
            success += 1
        else:
            failed += 1

        time.sleep(0.3 + random.uniform(-0.2, 0.2))  # 0.1~0.5s 随机抖动，避免反爬

    print(f"  → 成功 {success}，跳过 {skipped}，失败 {failed}")


def merge():
    """合并所有单只股票文件为 kline_daily.parquet。"""
    print("\n[3/3] 合并数据...")

    raw_files = sorted(os.listdir(RAW_DIR))
    frames = []

    for fname in tqdm(raw_files, desc="合并进度"):
        path = os.path.join(RAW_DIR, fname)
        frames.append(pl.read_parquet(path))

    if not frames:
        print("  → 没有可合并的数据")
        return

    df = pl.concat(frames, how="vertical").sort(["trade_date", "code"])
    path = os.path.join(DATA_DIR, "kline_daily.parquet")
    df.write_parquet(path)

    print(f"  → {len(df)} 行（{df['code'].n_unique()} 只股票，"
          f"{df['trade_date'].n_unique()} 个交易日）已保存到 {path}")


def main():
    ensure_dirs()
    stock_df = get_stock_list()
    fetch_all(stock_df)
    merge()
    print("\n完成。")


if __name__ == "__main__":
    main()
