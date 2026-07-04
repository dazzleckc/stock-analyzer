"""退市整理期数据采集脚本

从 st 接口中筛选 stock_st 中 ST 个股的退市整理期记录。

数据来源：Tushare Pro st 接口
筛选条件：st_tpye 包含"退市"

输出：
  data/delist_period.parquet  → 退市整理期数据（code, name, imp_date）

使用方式：
  python scripts/fetch_delist_period.py           # 全量拉取
  python scripts/fetch_delist_period.py --update  # 增量更新（幂等）
"""

import os
import sys
import argparse
import time

import pandas as pd
import polars as pl
import tushare as ts
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.local import TUSHARE_TOKEN

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
ST_STOCK_PATH = os.path.join(DATA_DIR, "st_stock.parquet")
OUTPUT_PATH = os.path.join(DATA_DIR, "delist_period.parquet")

# 取消代理
for _key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_key, None)


def code_to_ts_code(code: str) -> str:
    """6 位代码 → Tushare 格式。"""
    c = code.zfill(6)
    if c[0] == "6":
        return c + ".SH"
    if c[0] in ("0", "3"):
        return c + ".SZ"
    if c[0] == "9":
        return c + ".BJ"
    return c + ".SZ"


def fetch_one(code: str) -> list[dict] | None:
    """查询单只股票的 st 记录，返回退市整理期事件列表。"""
    ts_code = code_to_ts_code(code)
    try:
        raw = ts.pro_api().st(
            ts_code=ts_code,
            fields="ts_code,name,imp_date,st_tpye,st_reason",
        )
    except Exception:
        return None

    if raw is None or raw.empty:
        return None

    mask = raw["st_tpye"].astype(str).str.contains("退市")
    delist = raw[mask]

    if len(delist) == 0:
        return None

    return delist[["ts_code", "name", "imp_date"]].to_dict(orient="records")


def scan_all(codes: list[str]) -> pl.DataFrame:
    """逐只查询退市整理期，返回 Polars DataFrame。"""
    records = []
    found = failed = 0
    for code in tqdm(codes, desc="查询退市整理期"):
        result = fetch_one(code)
        if result is not None:
            for r in result:
                records.append({
                    "code": code,
                    "name": r["name"],
                    "imp_date": r["imp_date"],
                })
            found += len(result)
        else:
            failed += 1
        time.sleep(0.2)

    print(f"\n  退市整理期事件：{found} 条（{len(set(r['code'] for r in records))} 只股票）")
    print(f"  无退市记录：{failed} 只")

    if not records:
        return pl.DataFrame(schema={"code": pl.Utf8, "name": pl.Utf8, "imp_date": pl.Utf8})

    return pl.DataFrame(records).with_columns(
        pl.col("imp_date").str.to_date(format="%Y%m%d"),
    ).sort(["imp_date", "code"])


def full_fetch():
    """全量拉取：从 stock_st 获取所有 ST 股并逐个查询 st 接口。"""
    if not os.path.exists(ST_STOCK_PATH):
        print(f"未找到 {ST_STOCK_PATH}，请先运行 fetch_st_stock.py。")
        return

    st_stocks = pl.read_parquet(ST_STOCK_PATH)
    codes = sorted(st_stocks["code"].unique().to_list())
    print(f"ST 股票数：{len(codes)} 只")

    df = scan_all(codes)

    if "imp_date" not in df.columns:
        df.write_parquet(OUTPUT_PATH)
        print("  没有退市整理期数据。")
        return

    df.write_parquet(OUTPUT_PATH)
    print(f"\n  → 已保存到 {OUTPUT_PATH}")
    print(f"  → 日期范围：{df['imp_date'].min()} ~ {df['imp_date'].max()}")


def update():
    """增量更新：重新扫描所有 ST 股，用 (code, imp_date) 去重合并。"""
    if not os.path.exists(OUTPUT_PATH):
        print("未找到 delist_period.parquet，切换到全量模式。")
        full_fetch()
        return

    if not os.path.exists(ST_STOCK_PATH):
        print(f"未找到 {ST_STOCK_PATH}，请先运行 fetch_st_stock.py --update。")
        return

    existing = pl.read_parquet(OUTPUT_PATH)
    st_stocks = pl.read_parquet(ST_STOCK_PATH)
    codes = sorted(st_stocks["code"].unique().to_list())
    print(f"ST 股票数：{len(codes)} 只（现有退市整理期记录 {len(existing)} 条）")

    df = scan_all(codes)

    if "imp_date" not in df.columns:
        print("  没有新退市整理期数据。")
        return

    # 幂等合并：主键去重
    updated = (
        pl.concat([existing, df], how="vertical")
        .unique(subset=["code", "imp_date"], keep="last")
        .sort(["imp_date", "code"])
    )

    new_count = len(updated) - len(existing)
    updated.write_parquet(OUTPUT_PATH)

    print(f"\n  → 新增 {new_count} 条，共 {len(updated)} 条")
    print(f"  → 已保存到 {OUTPUT_PATH}")
    print(f"  → 日期范围：{updated['imp_date'].min()} ~ {updated['imp_date'].max()}")


def main():
    parser = argparse.ArgumentParser(description="退市整理期数据采集脚本（Tushare st）")
    parser.add_argument("--update", action="store_true", help="增量更新模式（幂等）")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    if args.update:
        update()
    else:
        full_fetch()

    print("完成。")


if __name__ == "__main__":
    main()
