"""交易日历同步脚本。

数据源：Tushare trade_cal 接口（SSE 交易所）
策略：全量拉取 → 原子覆盖写入（幂等，~1,800 行 / ~18KB）

使用方式：
  python scripts/sync_trade_cal.py        # 全量拉取（幂等）
  python scripts/sync_trade_cal.py --full # 同上

注册在 sync_runner 拓扑第 0 位（独立无依赖）。
"""

import argparse
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (                              # noqa: E402
    TRADE_CAL_PATH, TRADE_CAL_COLUMNS,
    get_pro, atomic_write_parquet,
)


def sync() -> dict:
    """全量拉取交易日历 → 类型转换 → 原子覆盖写入。

    返回: {"rows": int, "date_min": str, "date_max": str, "trading_days": int}
    """
    pro = get_pro()
    print("拉取 trade_cal（SSE）...")

    # 带重试的 API 调用
    raw = None
    last_error = None
    for attempt in range(3):
        try:
            raw = pro.trade_cal(exchange="SSE")
            if raw is not None and not raw.empty:
                break
        except Exception as e:
            last_error = e
            if attempt < 2:
                import time
                delay = 1.0 * (2 ** attempt)
                print(f"  第 {attempt + 1} 次失败（{e}），{delay:.0f}s 后重试...")
                time.sleep(delay)

    if raw is None or raw.empty:
        raise RuntimeError(
            f"trade_cal 接口返回空数据（3 次重试均失败）"
            + (f"：{last_error}" if last_error else "")
        )

    # Tushare 返回 pandas DataFrame → Polars
    df = pl.from_pandas(raw)
    print(f"  API 返回 {len(df)} 行")

    # 类型转换
    df = df.with_columns(
        pl.col("cal_date").cast(pl.Utf8).str.to_date(format="%Y%m%d"),
        pl.col("is_open").cast(pl.Int8),
        pl.col("pretrade_date").cast(pl.Utf8).str.to_date(format="%Y%m%d"),
    )

    # 选择列 & 排序
    df = df.select(TRADE_CAL_COLUMNS).sort("cal_date")

    # 原子写入
    atomic_write_parquet(df, TRADE_CAL_PATH)

    trading_count = df.filter(pl.col("is_open") == 1).height
    date_min = df["cal_date"].min()
    date_max = df["cal_date"].max()

    print(f"  → {len(df)} 行（{trading_count} 个交易日）")
    print(f"  → 日期范围：{date_min} ~ {date_max}")
    print(f"  → 已保存到 {TRADE_CAL_PATH}")

    return {
        "rows": len(df),
        "date_min": str(date_min),
        "date_max": str(date_max),
        "trading_days": trading_count,
    }


def main():
    parser = argparse.ArgumentParser(
        description="交易日历同步：全量拉取 Tushare trade_cal → 原子覆盖写入"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="全量拉取（默认行为，幂等）"
    )
    args = parser.parse_args()
    _ = args  # --full 仅为了和其他 sync_* 习惯一致，实际行为相同

    result = sync()

    print(f"\n完成。{result['rows']} 行，{result['trading_days']} 个交易日，"
          f"{result['date_min']} ~ {result['date_max']}")


if __name__ == "__main__":
    main()
