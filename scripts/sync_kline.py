"""A 股日K数据同步脚本

数据源：Tushare Pro pro_bar (asset='E', adj='qfq', factors=['tor'])
策略：
  全量 → 个股单文件 → 合并 → 原子覆盖（低内存 / 可续传 / 幂等）
  增量 → truncate by trade_date，全量原子覆盖
并发：ThreadPoolExecutor，共享 RateLimiter(500/min)

使用方式：
  python scripts/sync_kline.py                    # 增量（默认，今天）
  python scripts/sync_kline.py --full             # 全量初始化
  python scripts/sync_kline.py --date 20260701    # 补拉指定日期
  python scripts/sync_kline.py --workers 8        # 自定义并发数
"""

import argparse
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import polars as pl
import tushare as ts

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (                              # noqa: E402
    get_pro, code_to_ts_code, ymd_to_dashed,
    KLINE_PATH, STOCKS_PATH, KLINE_START_DATE,
    KLINE_COLUMNS, KLINE_REQUIRED_NONNULL,
    truncate_and_insert, atomic_write_parquet,
    validate_no_null, validate_unique,
    RateLimiter, retry_on_failure,
    TUSHARE_RATE_LIMIT, TUSHARE_RATE_WINDOW,
)


# ── 临时目录 ──────────────────────────────────────
_KLINE_TMP_DIR = os.path.join(
    os.path.dirname(KLINE_PATH), ".kline_tmp"
)


# ═══════════════════════════════════════════════════════════════════
# 单只拉取（pro_bar）
# ═══════════════════════════════════════════════════════════════════

@retry_on_failure(max_retries=3, base_delay=1.0)
def fetch_single_kline(pro, code: str, start_date: str, end_date: str) -> pl.DataFrame | None:
    """拉取单只股票日K（前复权 + 换手率）。

    参数:
      pro: Tushare pro_api 实例（通过 api=pro 传入 pro_bar）
      code: 6 位纯数字代码
      start_date: 起始日期 YYYYMMDD
      end_date: 结束日期 YYYYMMDD

    返回:
      Polars DataFrame（KLINE_COLUMNS 顺序），无数据返回 None。
    """
    ts_code = code_to_ts_code(code)
    raw = ts.pro_bar(
        ts_code=ts_code, api=pro, asset='E', adj='qfq',
        start_date=start_date, end_date=end_date,
        factors=['tor'],
    )

    if raw is None or raw.empty:
        return None

    df = pl.DataFrame(raw.to_dict(orient="records"))

    # ts_code → 纯 6 位 code
    df = df.with_columns(
        pl.col("ts_code").str.slice(0, 6).alias("code"),
    )

    # 振幅 = (high - low) / pre_close * 100（pre_close=0 时返回 None）
    df = df.with_columns(
        pl.when(pl.col("pre_close") > 0)
        .then((pl.col("high") - pl.col("low")) / pl.col("pre_close") * 100)
        .otherwise(None)
        .alias("amplitude")
    )

    # pro_bar(factors=['tor']) 返回的换手率列名通常是 turnover_rate
    tor_col = "turnover_rate" if "turnover_rate" in df.columns else "tor"

    selected = [
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
    ]

    if tor_col in df.columns:
        selected.append(pl.col(tor_col).cast(pl.Float64).alias("turnover_rate"))
    else:
        selected.append(pl.lit(None).cast(pl.Float64).alias("turnover_rate"))

    return df.select(selected)


# ═══════════════════════════════════════════════════════════════════
# Worker（全量：个股单文件）
# ═══════════════════════════════════════════════════════════════════

def _worker_full(
    codes: list[str],
    start_date: str,
    end_date: str,
    rate_limiter: RateLimiter,
    error_log: list,
    error_lock: threading.Lock,
) -> int:
    """全量 worker：独立 pro_api 实例，逐只拉取，每只写入独立 parquet。

    写入路径: {_KLINE_TMP_DIR}/{code}.parquet
    重试幂等：后写入覆盖前写入，天然去重。

    返回: 成功写入的个股数。
    """
    pro = get_pro()
    ok_count = 0
    for code in codes:
        rate_limiter.acquire(count=3)  # pro_bar 内部调用 daily + daily_basic + adj_factor
        try:
            df = fetch_single_kline(pro, code, start_date, end_date)
        except Exception as e:
            with error_lock:
                error_log.append((code, str(e)))
            continue

        if df is not None and len(df) > 0:
            path = os.path.join(_KLINE_TMP_DIR, f"{code}.parquet")
            df.write_parquet(path)
            ok_count += 1

    return ok_count


# ═══════════════════════════════════════════════════════════════════
# Worker（增量：内存收集）
# ═══════════════════════════════════════════════════════════════════

def _worker_incremental(
    codes: list[str],
    start_date: str,
    end_date: str,
    rate_limiter: RateLimiter,
    error_log: list,
    error_lock: threading.Lock,
) -> list[pl.DataFrame]:
    """增量 worker：独立 pro_api 实例，原内存收集方案。"""
    pro = get_pro()
    frames: list[pl.DataFrame] = []
    for code in codes:
        rate_limiter.acquire(count=3)  # pro_bar 内部调用 daily + daily_basic + adj_factor
        try:
            df = fetch_single_kline(pro, code, start_date, end_date)
        except Exception as e:
            with error_lock:
                error_log.append((code, str(e)))
            continue

        if df is not None and len(df) > 0:
            frames.append(df)

    return frames


# ═══════════════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════════════

def _load_active_codes() -> list[str]:
    """读取 stocks.parquet，过滤 list_status != 'D'，返回 codes 列表。"""
    if not os.path.exists(STOCKS_PATH):
        raise FileNotFoundError(
            f"未找到 {STOCKS_PATH}，请先运行 sync_stocks.py --full"
        )
    stocks = pl.read_parquet(STOCKS_PATH)
    active = stocks.filter(pl.col("list_status") != "D")
    return sorted(active["code"].to_list())


def _chunk_list(lst: list, n: int) -> list[list]:
    """将列表均匀划分为 n 组。"""
    k, m = divmod(len(lst), n)
    return [
        lst[i * k + min(i, m): (i + 1) * k + min(i + 1, m)]
        for i in range(n)
    ]


def _ensure_tmp_dir():
    """确保临时目录存在。"""
    os.makedirs(_KLINE_TMP_DIR, exist_ok=True)


def _cleanup_tmp_dir():
    """删除临时目录及全部个股 parquet。"""
    if os.path.exists(_KLINE_TMP_DIR):
        shutil.rmtree(_KLINE_TMP_DIR)


def _merge_tmp_files() -> pl.DataFrame:
    """读取临时目录下所有个股 parquet，合并为一个 DataFrame。"""
    files = sorted(Path(_KLINE_TMP_DIR).glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"临时目录 {_KLINE_TMP_DIR} 为空，无数据可合并")

    frames = [pl.read_parquet(str(f)) for f in files]
    return pl.concat(frames, how="vertical")


# ═══════════════════════════════════════════════════════════════════
# 核心函数：sync_full
# ═══════════════════════════════════════════════════════════════════

def sync_full(max_workers: int = 5) -> dict:
    """全量初始化：个股单文件 → 合并 → 原子覆盖。

    返回: {"stocks": int, "rows": int, "failed": list[str]}
    """
    today_str = date.today().strftime("%Y%m%d")
    codes = _load_active_codes()
    total = len(codes)

    print(f"全量初始化日K（{KLINE_START_DATE} ~ {today_str}）")
    print(f"  活跃股票 {total} 只，{max_workers} 线程并发")
    print(f"  临时目录: {_KLINE_TMP_DIR}")

    _cleanup_tmp_dir()
    _ensure_tmp_dir()

    rate_limiter = RateLimiter(
        max_calls=TUSHARE_RATE_LIMIT, window_seconds=TUSHARE_RATE_WINDOW,
    )
    error_log: list[tuple[str, str]] = []
    error_lock = threading.Lock()

    chunks = _chunk_list(codes, max_workers)

    ok_total = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                _worker_full, chunk, KLINE_START_DATE, today_str,
                rate_limiter, error_log, error_lock,
            )
            for chunk in chunks
        ]

        for future in as_completed(futures):
            ok_total += future.result()

    if error_log:
        print(f"  ⚠ {len(error_log)} 只股票拉取失败")
        for code, err in error_log[:5]:
            print(f"    {code}: {err}")
        if len(error_log) > 5:
            print(f"    ... 共 {len(error_log)} 只")

    if ok_total == 0:
        print("  未拉取到任何日K数据")
        _cleanup_tmp_dir()
        return {
            "stocks": total, "rows": 0,
            "failed": [e[0] for e in error_log],
        }

    print(f"  合并 {ok_total} 只个股文件...")
    df = _merge_tmp_files()
    validate_no_null(df, KLINE_REQUIRED_NONNULL)
    validate_unique(df, ["code", "trade_date"])

    atomic_write_parquet(df, KLINE_PATH)
    print(f"  → {len(df)} 行（{df['code'].n_unique()} 只股票，"
          f"{df['trade_date'].n_unique()} 个交易日）已保存到 {KLINE_PATH}")

    _cleanup_tmp_dir()

    return {
        "stocks": total, "rows": len(df),
        "failed": [e[0] for e in error_log],
    }


# ═══════════════════════════════════════════════════════════════════
# 核心函数：sync_incremental（保持内存方案）
# ═══════════════════════════════════════════════════════════════════

def sync_incremental(target_date: str = None, max_workers: int = 5) -> dict:
    """增量同步：拉取指定日期（默认今天），truncate by trade_date。

    参数:
      target_date: YYYYMMDD，默认 date.today()
      max_workers: 并发线程数，默认 5

    返回: {"stocks": int, "rows": int, "failed": list[str], "date": str}
    """
    if target_date is None:
        target_date = date.today().strftime("%Y%m%d")

    codes = _load_active_codes()

    print(f"增量同步日K（{target_date}）")
    print(f"  活跃股票 {len(codes)} 只，{max_workers} 线程并发")

    rate_limiter = RateLimiter(
        max_calls=TUSHARE_RATE_LIMIT, window_seconds=TUSHARE_RATE_WINDOW,
    )
    error_log: list[tuple[str, str]] = []
    error_lock = threading.Lock()

    chunks = _chunk_list(codes, max_workers)

    all_frames: list[pl.DataFrame] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                _worker_incremental, chunk, target_date, target_date,
                rate_limiter, error_log, error_lock,
            )
            for chunk in chunks
        ]

        for future in as_completed(futures):
            all_frames.extend(future.result())

    if error_log:
        print(f"  ⚠ {len(error_log)} 只股票拉取失败")
        for code, err in error_log[:5]:
            print(f"    {code}: {err}")
        if len(error_log) > 5:
            print(f"    ... 共 {len(error_log)} 只")

    if not all_frames:
        print(f"  {target_date} 无交易数据")
        return {
            "stocks": len(codes), "rows": 0,
            "failed": [e[0] for e in error_log],
            "date": target_date,
        }

    df = pl.concat(all_frames, how="vertical")
    validate_no_null(df, KLINE_REQUIRED_NONNULL)
    validate_unique(df, ["code", "trade_date"])

    date_dashed = ymd_to_dashed(target_date)
    truncate_and_insert(
        df, KLINE_PATH, key_column="trade_date",
        key_values=[date_dashed],
    )

    print(f"  → {len(df)} 行（{df['code'].n_unique()} 只股票）已写入 {target_date}")

    return {
        "stocks": len(codes), "rows": len(df),
        "failed": [e[0] for e in error_log],
        "date": target_date,
    }


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def _validate_date_arg(s: str) -> str:
    if len(s) != 8 or not s.isdigit():
        raise argparse.ArgumentTypeError(
            f"日期格式必须为 YYYYMMDD，收到: {s!r}"
        )
    return s


def main():
    parser = argparse.ArgumentParser(
        description="A 股日K数据同步：全量初始化 / 增量兜底"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="全量初始化模式（从 KLINE_START_DATE 到 today）"
    )
    parser.add_argument(
        "--date", type=_validate_date_arg, default=None,
        help="指定日期 YYYYMMDD（增量模式，默认今天）"
    )
    parser.add_argument(
        "--workers", type=int, default=5,
        help="并发线程数（默认 5）"
    )
    args = parser.parse_args()

    if args.full:
        result = sync_full(max_workers=args.workers)
    else:
        result = sync_incremental(target_date=args.date, max_workers=args.workers)

    print(
        f"\n完成。{result['stocks']} 只股票，{result['rows']} 行，"
        f"失败 {len(result['failed'])} 只"
    )
    if "date" in result:
        print(f"日期：{result['date']}")


if __name__ == "__main__":
    main()
