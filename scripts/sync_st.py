"""ST 状态数据同步脚本

数据源：Tushare stock_st 接口
策略：增量 truncate by trade_date，全量原子覆盖

使用方式：
  python scripts/sync_st.py            # 增量（默认，今天）
  python scripts/sync_st.py --full      # 全量初始化
"""

import argparse
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (                              # noqa: E402
    ST_STOCK_PATH, ST_COLUMNS,
    get_pro, truncate_and_insert, atomic_write_parquet,
    validate_no_null, validate_unique,
)


# ═══════════════════════════════════════════════════════════════════
# 数据拉取
# ═══════════════════════════════════════════════════════════════════

def _fetch_batch(pro, start_date: str, end_date: str) -> pl.DataFrame | None:
    """拉取 [start_date, end_date] 区间 ST 数据，自动处理分页。

    stock_st 接口单次最多返回 1000 行。
    对 5 天批次通常不会触发分页，但这里做了防御处理。
    """
    all_pages = []
    offset = 0
    limit = 1000

    while True:
        df = pro.stock_st(
            start_date=start_date, end_date=end_date,
            limit=limit, offset=offset,
        )
        if df is None or df.empty:
            break
        all_pages.append(df)
        if len(df) < limit:
            break
        offset += limit
        time.sleep(0.3)

    if not all_pages:
        return None

    raw = pd.concat(all_pages, ignore_index=True)
    return pl.DataFrame(raw.to_dict(orient="records"))


# ═══════════════════════════════════════════════════════════════════
# 数据转换
# ═══════════════════════════════════════════════════════════════════

def _transform(raw: pl.DataFrame) -> pl.DataFrame:
    """ts_code → code + exchange，trade_date → pl.Date。"""
    return (
        raw
        .with_columns(
            pl.col("ts_code").str.slice(0, 6).alias("code"),
            pl.col("ts_code").str.split(".").list.last().alias("exchange"),
            pl.col("trade_date").str.to_date(format="%Y%m%d"),
        )
        .select(ST_COLUMNS)
        .sort(["trade_date", "code"])
    )


# ═══════════════════════════════════════════════════════════════════
# 日期工具
# ═══════════════════════════════════════════════════════════════════

def _ymd_to_dashed(ymd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（truncate_and_insert 需要此格式）。"""
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


# ═══════════════════════════════════════════════════════════════════
# 核心函数：sync_full / sync_incremental（两个独立函数）
# ═══════════════════════════════════════════════════════════════════

def sync_full() -> dict:
    """全量初始化：从 2026-01-01 到 today 分批拉取。

    返回: {"days": int, "rows": int}
    """
    pro = get_pro()
    start_dt = date(2026, 1, 1)
    end_dt = date.today()

    print(f"全量初始化 ST 数据（{start_dt} ~ {end_dt}）")

    frames: list[pl.DataFrame] = []
    current = start_dt

    while current <= end_dt:
        batch_end = min(current + timedelta(days=4), end_dt)  # 5 天一批
        s = current.strftime("%Y%m%d")
        e = batch_end.strftime("%Y%m%d")

        raw = _fetch_batch(pro, s, e)
        if raw is not None and len(raw) > 0:
            df = _transform(raw)
            frames.append(df)

        current = batch_end + timedelta(days=1)
        time.sleep(0.5)

    if not frames:
        print("  未获取到任何 ST 数据")
        return {"days": 0, "rows": 0}

    df = pl.concat(frames, how="vertical").sort(["trade_date", "code"])

    validate_no_null(df, ["code", "trade_date"])
    validate_unique(df, ["code", "trade_date"])

    atomic_write_parquet(df, ST_STOCK_PATH)

    n_dates = df["trade_date"].n_unique()
    print(f"  → {n_dates} 个交易日，{len(df)} 行已保存到 {ST_STOCK_PATH}")

    return {"days": n_dates, "rows": len(df)}


def sync_incremental(target_date: str = None) -> dict:
    """增量同步：拉取指定日期的 ST 数据（默认今天），truncate by trade_date。

    返回: {"rows": int, "date": str}
    """
    pro = get_pro()

    if target_date is None:
        target_date = date.today().strftime("%Y%m%d")

    print(f"增量同步 ST 数据（{target_date}）")

    raw = _fetch_batch(pro, target_date, target_date)

    if raw is None or raw.is_empty():
        print(f"  {target_date} 无 ST 变动")
        return {"rows": 0, "date": target_date}

    df = _transform(raw)

    validate_no_null(df, ["code", "trade_date"])
    validate_unique(df, ["code", "trade_date"])

    date_dashed = _ymd_to_dashed(target_date)
    truncate_and_insert(
        df, ST_STOCK_PATH,
        key_column="trade_date", key_values=[date_dashed],
    )

    print(f"  → {len(df)} 行已写入 {target_date}")

    return {"rows": len(df), "date": target_date}


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="ST 状态数据同步：增量 truncate / 全量原子覆盖"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="全量初始化模式"
    )
    args = parser.parse_args()

    if args.full:
        result = sync_full()
        print(
            f"\n完成。{result['days']} 个交易日，{result['rows']} 行"
        )
    else:
        result = sync_incremental()
        print(
            f"\n完成。{result['rows']} 行，日期 {result['date']}"
        )


if __name__ == "__main__":
    main()
