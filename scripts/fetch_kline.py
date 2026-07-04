"""A 股日线数据采集脚本

数据来源：Tushare Pro
接口：ts.pro_bar(asset='E', adj='qfq', factors=['tor','vr'])
  - 股票前复权日线，含换手率、量比
  - 未复权、后复权可切换 adj 参数

输出：
  data/raw/{code}.parquet    → 中间缓存（支持断点续传）
  data/kline_daily.parquet   → 汇总日线数据

使用方式：
  python scripts/fetch_kline.py
"""

import os
import sys
import time
from datetime import date, timedelta

import pandas as pd
import polars as pl
import tushare as ts
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.local import TUSHARE_TOKEN

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
START_DATE = "20260105"  # 2026 年第一个交易日
END_DATE = date.today().strftime("%Y%m%d")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
STOCKS_PATH = os.path.join(DATA_DIR, "stocks.parquet")
OUTPUT_PATH = os.path.join(DATA_DIR, "kline_daily.parquet")

# 取消代理（Tushare 不走本地代理）
for _key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_key, None)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def code_to_ts_code(code: str) -> str:
    """6 位代码 → Tushare 格式（带交易所后缀）。"""
    c = code.zfill(6)
    if c[0] == "6":
        return c + ".SH"
    if c[0] in ("0", "3"):
        return c + ".SZ"
    # 北交所
    if c[0] == "9":
        return c + ".BJ"
    return c + ".SZ"  # 兜底


def ensure_dirs():
    for d in (DATA_DIR, RAW_DIR):
        os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------------------
# 数据获取
# ---------------------------------------------------------------------------

def transform(raw: pd.DataFrame) -> pl.DataFrame | None:
    """pro_bar 原始数据 → 标准 Polars DataFrame。"""
    if raw is None or raw.empty:
        return None

    df = pl.DataFrame(raw.to_dict(orient="records"))

    # pro_bar 输出字段：
    #   ts_code, trade_date, open, high, low, close,
    #   pre_close, change, pct_chg, vol, amount,
    #   turnover_rate (factors=['tor']), volume_ratio (factors=['vr'])

    # 提取纯 6 位代码
    df = df.with_columns(
        pl.col("ts_code").str.slice(0, 6).alias("code"),
    )

    # 振幅 = (high - low) / pre_close * 100
    df = df.with_columns(
        ((pl.col("high") - pl.col("low")) / pl.col("pre_close") * 100)
        .alias("amplitude")
    )

    return df.select([
        pl.col("code"),
        pl.col("trade_date").str.to_date(format="%Y%m%d"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("vol").cast(pl.Int64).alias("volume"),
        pl.col("amount").cast(pl.Float64),
        pl.col("amplitude").cast(pl.Float64),
        pl.col("pct_chg").cast(pl.Float64).alias("pct_change"),
        pl.col("turnover_rate").cast(pl.Float64),
    ])


def fetch_single_stock(code: str, start_date: str = START_DATE,
                       end_date: str = END_DATE) -> pl.DataFrame | None:
    """拉取单只股票前复权日线，含换手率。"""
    ts_code = code_to_ts_code(code)
    try:
        raw = ts.pro_bar(
            ts_code=ts_code,
            adj="qfq",
            start_date=start_date,
            end_date=end_date,
            factors=["tor", "vr"],
        )
    except Exception:
        return None

    return transform(raw)


# ---------------------------------------------------------------------------
# 批量下载 & 合并
# ---------------------------------------------------------------------------

def fetch_all(codes: list[str]):
    """逐只下载，存为 data/raw/{code}.parquet，跳过已存在的。"""
    print(f"[1/2] 下载日线数据（{START_DATE} ~ {END_DATE}）...")

    success = skipped = failed = 0
    t0 = time.time()

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

        # 6000 积分 → 500 次/min，无需限速

    elapsed = time.time() - t0
    print(f"  → 成功 {success}，跳过 {skipped}，失败 {failed}，耗时 {elapsed:.0f}s")


def merge():
    """合并 raw/ 下的 parquet 为 kline_daily.parquet。"""
    print("\n[2/2] 合并数据...")

    if not os.path.isdir(RAW_DIR):
        print("  → raw/ 目录不存在")
        return

    raw_files = sorted(f for f in os.listdir(RAW_DIR) if f.endswith(".parquet"))
    if not raw_files:
        print("  → 没有可合并的数据")
        return

    frames = []
    for fname in tqdm(raw_files, desc="合并进度"):
        frames.append(pl.read_parquet(os.path.join(RAW_DIR, fname)))

    df = pl.concat(frames, how="vertical").sort(["trade_date", "code"])
    df.write_parquet(OUTPUT_PATH)

    print(f"  → {len(df)} 行（{df['code'].n_unique()} 只股票，"
          f"{df['trade_date'].n_unique()} 个交易日）已保存到 {OUTPUT_PATH}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    ensure_dirs()

    # 从 stocks.parquet 读取股票列表
    if not os.path.exists(STOCKS_PATH):
        print(f"未找到 {STOCKS_PATH}，请先运行 scripts/fetch_stocks.py 生成股票列表。")
        return

    stocks = pl.read_parquet(STOCKS_PATH)
    codes = sorted(stocks["code"].to_list())
    print(f"股票列表：{len(codes)} 只（来自 {STOCKS_PATH}）")

    fetch_all(codes)
    merge()
    print("\n完成。")


if __name__ == "__main__":
    main()
