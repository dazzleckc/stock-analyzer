"""退市整理期数据同步脚本

数据源：Tushare Pro st 接口
策略：
  - 增量：pub_date=今天 → 筛选退市记录 → truncate_and_insert by imp_date
  - 全量：逐只扫描 ST 股票列表 → 筛选退市记录 → atomic_write_parquet

parquet 列名统一用 imp_date（非 pub_date），与 config.DELIST_COLUMNS 一致。

使用方式：
  python scripts/sync_delist.py              # 增量（默认，pub_date=今天）
  python scripts/sync_delist.py --full        # 全量扫描
  python scripts/sync_delist.py --date 20260701  # 指定日期
"""

import argparse
import os
import sys
from datetime import date

import pandas as pd
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (                              # noqa: E402
    DELIST_PATH, ST_STOCK_PATH, DELIST_COLUMNS,
    get_pro, code_to_ts_code,
    truncate_and_insert, atomic_write_parquet,
    validate_no_null, validate_unique,
    RateLimiter,
)

# Tushare st 接口请求字段（pub_date 用于 API 过滤和返回，落盘时重命名为 imp_date）
ST_FIELDS = "ts_code,name,pub_date,st_tpye,st_reason"


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

def _normalize_columns(df: pl.DataFrame) -> pl.DataFrame:
    """去掉 Tushare 可能返回的列名前缀 '_'（如 _ts_code → ts_code）。"""
    renames = {}
    for col in df.columns:
        stripped = col.lstrip("_")
        if stripped != col:
            renames[col] = stripped
    if renames:
        df = df.rename(renames)
    return df


def _detect_date_col(df: pl.DataFrame) -> str:
    """探测日期列名：优先 pub_date，兜底 imp_date（兼容不同 Tushare 版本）。"""
    for col in ["pub_date", "imp_date"]:
        if col in df.columns:
            return col
    raise KeyError(f"API 返回缺少日期列（pub_date/imp_date），实际列: {list(df.columns)}")


def _ymd_to_date(ymd: str) -> date:
    """YYYYMMDD → date 对象（用于 truncate_and_insert key_values，需与 pl.Date 列类型匹配）。"""
    return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))


# ═══════════════════════════════════════════════════════════════════
# sync_incremental
# ═══════════════════════════════════════════════════════════════════

def sync_incremental(target_date: str = None) -> dict:
    """增量同步：pub_date=指定日期（默认今天）→ 筛选退市记录 → truncate by imp_date。

    参数:
      target_date: 目标日期 YYYYMMDD，默认 date.today()

    返回: {"records": int, "date": str}
    """
    if target_date is None:
        target_date = date.today().strftime("%Y%m%d")

    pro = get_pro()

    raw = pro.st(pub_date=target_date, fields=ST_FIELDS)

    if raw is None or raw.empty:
        print(f"  {target_date} 无退市公告")
        return {"records": 0, "date": target_date}

    df = pl.DataFrame(raw.to_dict(orient="records"))
    df = _normalize_columns(df)

    # 检查必要列是否存在
    if "st_tpye" not in df.columns:
        print(f"  ⚠ API 返回缺少 st_tpye 列，实际列: {list(df.columns)}")
        return {"records": 0, "date": target_date}

    # 筛选退市相关记录：st_tpye 或 st_reason（如有）包含"退市"
    has_reason = "st_reason" in df.columns
    mask = pl.col("st_tpye").cast(pl.Utf8).str.contains("退市")
    if has_reason:
        mask = mask | pl.col("st_reason").cast(pl.Utf8).str.contains("退市")
    df = df.filter(mask)

    if df.height == 0:
        print(f"  {target_date} 无退市公告（{len(raw)} 条 ST 公告均不含'退市'关键字）")
        return {"records": 0, "date": target_date}

    # 转换：ts_code→code，pub_date/imp_date→imp_date(pl.Date)
    date_col = _detect_date_col(df)

    df = df.select([
        pl.col("ts_code").cast(pl.Utf8).str.slice(0, 6).alias("code"),
        pl.col("name").cast(pl.Utf8),
        pl.col(date_col).str.to_date(format="%Y%m%d").alias("imp_date"),
    ])

    # 校验
    validate_no_null(df, ["code", "imp_date"])
    validate_unique(df, ["code", "imp_date"])

    # truncate & insert：按 imp_date 覆盖当天数据
    date_key = _ymd_to_date(target_date)
    truncate_and_insert(df, DELIST_PATH, key_column="imp_date",
                        key_values=[date_key])

    print(f"  → {df.height} 条退市整理期记录已写入 {target_date}")
    return {"records": df.height, "date": target_date}


# ═══════════════════════════════════════════════════════════════════
# sync_full
# ═══════════════════════════════════════════════════════════════════

def sync_full() -> dict:
    """全量初始化：读取 st_stock.parquet → 逐只调用 pro.st(ts_code=...) → 筛选退市记录。

    依赖 st_stock.parquet 已存在（需先运行 sync_st.py）。

    返回: {"stocks_scanned": int, "delist_records": int}
    """
    if not os.path.exists(ST_STOCK_PATH):
        raise FileNotFoundError(
            f"未找到 {ST_STOCK_PATH}，请先运行 sync_st.py --full"
        )

    st_stocks = pl.read_parquet(ST_STOCK_PATH)
    codes = sorted(st_stocks["code"].unique().to_list())
    print(f"全量扫描退市整理期（{len(codes)} 只 ST 股票）")

    rate_limiter = RateLimiter(max_calls=500, window_seconds=60.0)
    pro = get_pro()
    records: list[dict] = []
    failed = 0

    for i, code in enumerate(codes):
        ts_code = code_to_ts_code(code)
        rate_limiter.acquire()

        try:
            raw = pro.st(ts_code=ts_code, fields=ST_FIELDS)
        except Exception as e:
            failed += 1
            if failed <= 3:
                print(f"  ⚠ {code} 查询失败: {e}")
            continue

        if raw is None or raw.empty:
            continue

        df = pl.DataFrame(raw.to_dict(orient="records"))
        df = _normalize_columns(df)

        if "st_tpye" not in df.columns:
            continue

        # 筛选退市：st_tpye 或 st_reason 包含"退市"
        has_reason = "st_reason" in df.columns
        mask = pl.col("st_tpye").cast(pl.Utf8).str.contains("退市")
        if has_reason:
            mask = mask | pl.col("st_reason").cast(pl.Utf8).str.contains("退市")
        delist_df = df.filter(mask)

        if delist_df.height == 0:
            continue

        date_col = _detect_date_col(delist_df)

        for row in delist_df.iter_rows(named=True):
            records.append({
                "code": code,
                "name": row["name"],
                "imp_date": row[date_col],
            })

        if (i + 1) % 100 == 0:
            print(f"  进度: {i + 1}/{len(codes)}，已发现 {len(records)} 条退市记录")

    if failed:
        print(f"  ⚠ {failed} 只股票查询失败")

    if not records:
        print("  未发现退市整理期记录")
        empty_df = pl.DataFrame(
            schema={"code": pl.Utf8, "name": pl.Utf8, "imp_date": pl.Date}
        )
        atomic_write_parquet(empty_df, DELIST_PATH)
        return {"stocks_scanned": len(codes), "delist_records": 0}

    df = (
        pl.DataFrame(records)
        .with_columns(pl.col("imp_date").str.to_date(format="%Y%m%d"))
        .select(DELIST_COLUMNS)
        .sort(["imp_date", "code"])
    )

    validate_no_null(df, ["code", "imp_date"])
    validate_unique(df, ["code", "imp_date"])

    atomic_write_parquet(df, DELIST_PATH)

    print(f"\n  → {df.height} 条退市整理期记录（{df['code'].n_unique()} 只股票）")
    print(f"  → 日期范围：{df['imp_date'].min()} ~ {df['imp_date'].max()}")
    print(f"  → 已保存到 {DELIST_PATH}")

    return {"stocks_scanned": len(codes), "delist_records": df.height}


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="退市整理期数据同步：增量 truncate / 全量扫描"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="全量扫描模式（逐只扫描 st_stock.parquet 中所有 ST 股票）"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="指定日期 YYYYMMDD（增量模式，默认今天）"
    )
    args = parser.parse_args()

    if args.full:
        result = sync_full()
    else:
        result = sync_incremental(target_date=args.date)

    print(f"\n完成。")
    if "stocks_scanned" in result:
        print(f"  扫描 {result['stocks_scanned']} 只，退市记录 {result['delist_records']} 条")
    else:
        print(f"  日期 {result['date']}，{result['records']} 条记录")


if __name__ == "__main__":
    main()
