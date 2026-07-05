"""指数日线数据同步脚本

数据源：Tushare Pro pro_bar (asset='I')
策略：增量 truncate by trade_date，全量原子覆盖
模式：串行（6 只指数，无需并发）

使用方式：
  python scripts/sync_indices.py                    # 增量（默认，今天）
  python scripts/sync_indices.py --full             # 全量初始化
  python scripts/sync_indices.py --date 20260701    # 补拉指定日期
"""

import argparse
import os
import sys
from datetime import date

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (                              # noqa: E402
    get_pro, ymd_to_dashed,
    INDICES_PATH, INDICES_COLUMNS, INDICES_REQUIRED_NONNULL,
    INDEX_LIST, KLINE_START_DATE, INDICES_SCHEMA,
    truncate_and_insert, atomic_write_parquet,
    validate_no_null, validate_unique,
    retry_on_failure,
)

# ── 常量 ──────────────────────────────────────────

# 全市场汇总：需要聚合的指数代码（纯数字，不含交易所后缀）
_MARKET_AGG_CODES = ["000001", "399001", "899050"]
_MARKET_AGG_CODE = "999999"

# pro_bar(asset='I') 返回的必要列
_REQUIRED_RAW_COLUMNS = {
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "vol", "amount", "pct_chg",
}


# ═══════════════════════════════════════════════════════════════════
# 单只指数拉取
# ═══════════════════════════════════════════════════════════════════

def fetch_single_index(pro, ts_code: str, start_date: str, end_date: str) -> pl.DataFrame | None:
    """拉取单只指数日线。

    参数:
      pro: Tushare pro_api 实例
      ts_code: Tushare 格式指数代码（如 000001.SH）
      start_date: 起始日期 YYYYMMDD
      end_date: 结束日期 YYYYMMDD

    返回:
      Polars DataFrame（INDICES_COLUMNS 顺序），无数据返回 None。
      返回 None 不是错误——非交易日等正常情况。
    """

    @retry_on_failure(max_retries=3, base_delay=1.0)
    def _call_pro_bar():
        return pro.pro_bar(
            ts_code=ts_code, asset='I',
            start_date=start_date, end_date=end_date,
        )

    raw = _call_pro_bar()

    if raw is None or raw.empty:
        return None

    # 列结构校验（P1-4）
    if not _REQUIRED_RAW_COLUMNS.issubset(set(raw.columns)):
        missing_cols = _REQUIRED_RAW_COLUMNS - set(raw.columns)
        raise ValueError(f"Tushare 返回缺少必要列: {missing_cols}")

    df = pl.from_pandas(raw)

    # code: ts_code 前 6 位
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

    return df.select([
        pl.col("code"),
        pl.col("trade_date").str.to_date(format="%Y%m%d"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("vol").fill_null(0).cast(pl.Int64, strict=False).alias("volume"),
        pl.col("amount").cast(pl.Float64),
        pl.col("amplitude").cast(pl.Float64),
        pl.col("pct_chg").cast(pl.Float64).alias("pct_change"),
        pl.lit(0.0).alias("turnover_rate"),
    ])


# ═══════════════════════════════════════════════════════════════════
# 全市场汇总
# ═══════════════════════════════════════════════════════════════════

def build_market_aggregate(df: pl.DataFrame) -> pl.DataFrame:
    """从指数数据构建全市场汇总行（code=999999）。

    汇总逻辑：对 _MARKET_AGG_CODES 中存在的指数，
    按 trade_date 对 amount 和 volume 求和，其余字段填 0.0。

    不强制要求 3 只全部存在——polars group_by.sum 自动忽略缺失组。
    """
    return (
        df
        .filter(pl.col("code").is_in(_MARKET_AGG_CODES))
        .group_by("trade_date")
        .agg([
            pl.col("amount").sum().alias("amount"),
            pl.col("volume").sum().alias("volume"),
        ])
        .with_columns([
            pl.lit(_MARKET_AGG_CODE).alias("code"),
            pl.lit(0.0).alias("open"),
            pl.lit(0.0).alias("high"),
            pl.lit(0.0).alias("low"),
            pl.lit(0.0).alias("close"),
            pl.lit(0.0).alias("amplitude"),
            pl.lit(0.0).alias("pct_change"),
            pl.lit(0.0).alias("turnover_rate"),
        ])
        .select(INDICES_COLUMNS)
        .sort(["trade_date", "code"])
    )


# ═══════════════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════════════

def _validate_date_arg(s: str) -> str:
    """argparse type 校验函数：确保 --date 参数为 YYYYMMDD 格式。

    委托 ymd_to_dashed 做校验，确保未来校验逻辑增强后自动同步受益。
    """
    try:
        ymd_to_dashed(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))
    return s


def _validate_schema(df: pl.DataFrame) -> None:
    """校验 df 列类型与 INDICES_SCHEMA 一致。（P1-3）"""
    for col, expected_dtype in INDICES_SCHEMA.items():
        actual = df[col].dtype
        if actual != expected_dtype:
            raise TypeError(
                f"列 {col} 类型不匹配: 期望 {expected_dtype}, 实际 {actual}"
            )


# ═══════════════════════════════════════════════════════════════════
# 共享拉取 & 校验
# ═══════════════════════════════════════════════════════════════════

def _fetch_and_validate(pro, start_date: str, end_date: str) -> tuple[pl.DataFrame | None, list[str]]:
    """拉取全部 6 只指数，concat，validate。
    
    返回: (df, missing)。
      df 为 None 表示无任何数据。
      missing 为拉取失败或无数据的 ts_code 列表。
    """
    frames: list[pl.DataFrame] = []
    missing: list[str] = []

    for ts_code, name in INDEX_LIST.items():
        try:
            df = fetch_single_index(pro, ts_code, start_date, end_date)
        except Exception as e:
            print(f"  {ts_code} {name}: 拉取失败 — {e}")
            missing.append(ts_code)
            continue

        if df is not None and len(df) > 0:
            frames.append(df)
            print(f"  {ts_code} {name}: {len(df)} 行")
        else:
            print(f"  {ts_code} {name}: 无数据")

    if not frames:
        return None, missing

    df = pl.concat(frames, how="vertical")
    validate_no_null(df, INDICES_REQUIRED_NONNULL)
    validate_unique(df, ["code", "trade_date"])
    return df, missing


# ═══════════════════════════════════════════════════════════════════
# 核心函数：sync_full / sync_incremental
# ═══════════════════════════════════════════════════════════════════

def sync_full() -> dict:
    """全量初始化：从 KLINE_START_DATE 到 today，拉取全部 6 只指数 + 汇总。

    返回: {"indices": int, "rows": int, "missing": list[str]}
    """
    today_str = date.today().strftime("%Y%m%d")
    pro = get_pro()

    print(f"全量初始化指数日线（{KLINE_START_DATE} ~ {today_str}）\n")

    df, missing = _fetch_and_validate(pro, KLINE_START_DATE, today_str)
    if df is None:
        print("\n未拉取到任何指数数据。")
        return {"indices": 0, "rows": 0, "missing": missing}

    real_count = df["code"].n_unique()
    agg = build_market_aggregate(df)
    df = pl.concat([df, agg], how="vertical").sort(["trade_date", "code"])

    _validate_schema(df)

    try:
        atomic_write_parquet(df, INDICES_PATH)
    except Exception as e:
        print(f"写入失败: {e}")
        raise

    print(
        f"\n  已保存 {real_count} 只指数 + 全市场汇总，"
        f"共 {len(df)} 行，{df['trade_date'].n_unique()} 个交易日"
    )
    return {"indices": real_count, "rows": len(df), "missing": missing}


def sync_incremental(target_date: str = None) -> dict:
    """增量同步：拉取指定日期的指数数据（默认今天），truncate by trade_date。

    返回: {"indices": int, "rows": int, "missing": list[str], "date": str}
    """
    if target_date is None:
        target_date = date.today().strftime("%Y%m%d")

    pro = get_pro()

    print(f"增量同步指数日线（{target_date}）\n")

    df, missing = _fetch_and_validate(pro, target_date, target_date)
    if df is None:
        print(f"\n  {target_date} 无交易数据")
        return {"indices": 0, "rows": 0, "missing": missing, "date": target_date}

    real_count = df["code"].n_unique()
    agg = build_market_aggregate(df)
    df = pl.concat([df, agg], how="vertical")

    _validate_schema(df)

    try:
        date_dashed = ymd_to_dashed(target_date)
        truncate_and_insert(df, INDICES_PATH, key_column="trade_date",
                            key_values=[date_dashed])
    except Exception as e:
        print(f"写入失败: {e}")
        raise

    print(
        f"\n  → {real_count} 只指数 + 全市场汇总，"
        f"共 {len(df)} 行，已写入 {target_date}"
    )

    return {
        "indices": real_count, "rows": len(df),
        "missing": missing, "date": target_date,
    }


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="指数日线数据同步：全量初始化 / 增量兜底"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="全量初始化模式"
    )
    parser.add_argument(
        "--date", type=_validate_date_arg, default=None,
        help="指定日期 YYYYMMDD（增量模式，默认今天）"
    )
    args = parser.parse_args()

    if args.full:
        result = sync_full()
    else:
        result = sync_incremental(target_date=args.date)

    print(
        f"\n完成。{result['indices']} 只指数，{result['rows']} 行，"
        f"缺失 {len(result['missing'])} 只"
    )
    if "date" in result:
        print(f"日期：{result['date']}")


if __name__ == "__main__":
    main()
