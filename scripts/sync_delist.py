"""退市整理期数据同步脚本

数据源：Tushare Pro st 接口
策略：
  - 增量：pub_date=今天 → 筛选退市记录 → truncate_and_insert by imp_date
  - 全量：时间窗口扫描（Phase A）+ 逐只扫描（Phase B）→ 合并去重写入

parquet 列名统一用 imp_date（非 pub_date），与 config.DELIST_COLUMNS 一致。

使用方式：
  python scripts/sync_delist.py              # 增量（默认，pub_date=今天）
  python scripts/sync_delist.py --full        # 全量扫描
  python scripts/sync_delist.py --date 20260701  # 指定日期
"""

import argparse
import os
import sys
import time
from datetime import date

import pandas as pd
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (                              # noqa: E402
    DELIST_PATH, ST_STOCK_PATH, DELIST_COLUMNS,
    get_pro, code_to_ts_code,
    truncate_and_insert, atomic_write_parquet,
    validate_no_null, validate_unique,
    RateLimiter, KLINE_START_DATE,
)

# Tushare st 接口请求字段（pub_date 用于 API 过滤和返回，落盘时重命名为 imp_date）
ST_FIELDS = "ts_code,name,pub_date,st_tpye"


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


def _filter_delist(df: pl.DataFrame) -> pl.DataFrame:
    """筛选退市相关记录：st_type == '退市整理期'."""
    return df.filter(pl.col("st_tpye") == "退市整理期")


def _extract_code(ts_code) -> str | None:
    """从 ts_code 提取 6 位股票代码（split + zfill），无效返回 None."""
    if not ts_code or not isinstance(ts_code, str):
        return None
    raw_code = ts_code.split(".")[0]
    if not raw_code:
        return None
    return raw_code.zfill(6)


def _call_pro_st_with_retry(pro, **kwargs):
    """带重试的 pro.st() 调用，3 次指数退避."""
    last_exc = None
    for attempt in range(3):
        try:
            return pro.st(**kwargs)
        except Exception as e:
            last_exc = e
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))
    raise last_exc


# ═══════════════════════════════════════════════════════════════════
# _scan_pub_date_window
# ═══════════════════════════════════════════════════════════════════

def _scan_pub_date_window(
    start_date: str,       # "20251121" (YYYYMMDD)
    end_date: str,         # "20260105" (YYYYMMDD)
    rate_limiter: RateLimiter,
    pro,
) -> list[dict]:
    """逐日扫描 pub_date 窗口，拉取退市公告。

    对窗口内每一天调用 pro.st(pub_date=date)，筛选 st_tpye ==
    "退市整理期"的记录，返回标准化的 dict 列表。

    返回: [{"code": str, "name": str, "imp_date": str}, ...]

    单日失败不中断整体流程，仅记录失败天数。
    """
    from datetime import timedelta

    s = _ymd_to_date(start_date)
    e = _ymd_to_date(end_date)

    records: list[dict] = []
    failed_dates = 0
    total_dates = (e - s).days + 1
    date_col_cache = None

    print(f"\nPhase A: 时间窗口扫描 {start_date} ~ {end_date}（{total_dates} 天）")

    current = s
    while current <= e:
        date_str = current.strftime("%Y%m%d")
        rate_limiter.acquire()

        try:
            raw = _call_pro_st_with_retry(pro, pub_date=date_str, fields=ST_FIELDS)
        except Exception as ex:
            failed_dates += 1
            if failed_dates <= 3:
                print(f"  ⚠ {date_str} 查询失败: {ex}")
            current += timedelta(days=1)
            continue

        if raw is None or raw.empty:
            current += timedelta(days=1)
            continue

        df = pl.DataFrame(raw.to_dict(orient="records"))
        df = _normalize_columns(df)

        if "st_tpye" not in df.columns:
            current += timedelta(days=1)
            continue

        # 筛选退市记录
        delist_df = _filter_delist(df)

        if delist_df.height > 0:
            # 探测并缓存日期列名（P1-3/P1-4）
            if date_col_cache is None:
                try:
                    date_col_cache = _detect_date_col(delist_df)
                except KeyError as ke:
                    print(f"  ⚠ {date_str} 探测日期列失败: {ke}")
                    current += timedelta(days=1)
                    continue
            date_col = date_col_cache
            if date_col not in delist_df.columns:
                try:
                    date_col_cache = _detect_date_col(delist_df)
                except KeyError:
                    current += timedelta(days=1)
                    continue
                date_col = date_col_cache

            for row in delist_df.iter_rows(named=True):
                code = _extract_code(row.get("ts_code"))
                if code is None:
                    continue
                records.append({
                    "code": code,
                    "name": row["name"],
                    "imp_date": row[date_col],
                })

        current += timedelta(days=1)

    if failed_dates > 3:
        print(f"  ⚠ 另有 {failed_dates - 3} 天失败未逐一列出")
    print(f"  Phase A 完成: {len(records)} 条退市记录, "
          f"{failed_dates}/{total_dates} 天失败")
    return records


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

    raw = _call_pro_st_with_retry(pro, pub_date=target_date, fields=ST_FIELDS)

    if raw is None or raw.empty:
        print(f"  {target_date} 无退市公告")
        return {"records": 0, "date": target_date}

    df = pl.DataFrame(raw.to_dict(orient="records"))
    df = _normalize_columns(df)

    # 检查必要列是否存在
    if "st_tpye" not in df.columns:
        print(f"  ⚠ API 返回缺少 st_tpye 列，实际列: {list(df.columns)}")
        return {"records": 0, "date": target_date}

    # 筛选退市相关记录
    df = _filter_delist(df)

    if df.height == 0:
        print(f"  {target_date} 无退市公告（{len(raw)} 条 ST 公告均不含'退市'关键字）")
        return {"records": 0, "date": target_date}

    # 转换：ts_code→code，pub_date/imp_date→imp_date(pl.Date)
    date_col = _detect_date_col(df)

    df = df.select([
        pl.col("ts_code").cast(pl.Utf8).str.split(".").list.first().str.zfill(6).alias("code"),
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
    """全量初始化：Phase A 冗余时间窗口扫描 + Phase B 逐只扫描 → 合并去重写入。

    依赖 st_stock.parquet 已存在（需先运行 sync_st.py）。

    返回: {"stocks_scanned": int, "delist_records": int}
    """
    from datetime import timedelta

    if not os.path.exists(ST_STOCK_PATH):
        raise FileNotFoundError(
            f"未找到 {ST_STOCK_PATH}，请先运行 sync_st.py --full"
        )

    # ── Phase A: 冗余时间窗口扫描 ──
    kline_start = _ymd_to_date(KLINE_START_DATE)
    window_start = kline_start - timedelta(days=45)
    window_start_str = window_start.strftime("%Y%m%d")

    rate_limiter = RateLimiter(max_calls=500, window_seconds=60.0)
    pro = get_pro()

    try:
        records_a = _scan_pub_date_window(
            window_start_str, KLINE_START_DATE, rate_limiter, pro
        )
    except Exception as e:
        print(f"  ⚠ Phase A 执行异常: {e}")
        import traceback
        traceback.print_exc()
        print("  降级为纯 Phase B 逐只扫描")
        records_a = []

    # ── Phase B: 逐只扫描 ──

    st_stocks = pl.read_parquet(ST_STOCK_PATH)
    codes = sorted(st_stocks["code"].unique().to_list())
    print(f"\nPhase B: 逐只扫描退市整理期（{len(codes)} 只 ST 股票）")

    records_b: list[dict] = []
    failed = 0

    for i, code in enumerate(codes):
        if (i + 1) % 100 == 0:
            print(f"  进度: {i + 1}/{len(codes)}，已发现 {len(records_b)} 条退市记录")
        ts_code = code_to_ts_code(code)
        rate_limiter.acquire()

        try:
            raw = _call_pro_st_with_retry(pro, ts_code=ts_code, fields=ST_FIELDS)

            if raw is None or raw.empty:
                continue

            df = pl.DataFrame(raw.to_dict(orient="records"))
            df = _normalize_columns(df)

            if "st_tpye" not in df.columns:
                continue

            # 筛选退市记录
            delist_df = _filter_delist(df)

            if delist_df.height == 0:
                continue

            date_col = _detect_date_col(delist_df)

            for row in delist_df.iter_rows(named=True):
                records_b.append({
                    "code": code,
                    "name": row["name"],
                    "imp_date": row[date_col],
                })
        except Exception as e:
            failed += 1
            if failed <= 3:
                print(f"  ⚠ {code} 查询失败: {e}")
            continue

    if failed > 3:
        print(f"  ⚠ 另有 {failed - 3} 只股票查询失败未逐一列出")
    elif failed:
        print(f"  ⚠ {failed} 只股票查询失败")

    # ── Phase C: 合并去重写入 ──
    all_records = records_a + records_b
    print(f"\nPhase C: 合并 {len(records_a)}(A) + {len(records_b)}(B) "
          f"= {len(all_records)} 条原始记录")

    if not all_records:
        print("  未发现退市整理期记录")
        empty_df = pl.DataFrame(
            schema={"code": pl.Utf8, "name": pl.Utf8, "imp_date": pl.Date}
        )
        atomic_write_parquet(empty_df, DELIST_PATH)
        return {"stocks_scanned": len(codes), "delist_records": 0}

    df = (
        pl.DataFrame(all_records)
        .with_columns(pl.col("imp_date").cast(pl.Utf8).str.to_date(format="%Y%m%d"))
        .unique(subset=["code", "imp_date"], keep="first")
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
        help="全量扫描模式（时间窗口扫描 + 逐只扫描 → 合并去重写入）"
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
