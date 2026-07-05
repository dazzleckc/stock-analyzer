"""
集中管理所有 Parquet schema、列名、路径，避免各脚本散落魔法字符串。

用途：
- 统一 schema 定义，确保读写一致
- 路径变更只需改一处
- IDE 自动补全列名，减少拼写错误
"""

import os
import polars as pl

# ── 路径 ──────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

STOCKS_PATH           = os.path.join(DATA_DIR, "stocks.parquet")
STOCKS_CHANGELOG_PATH = os.path.join(DATA_DIR, "stocks_changelog.parquet")
KLINE_PATH            = os.path.join(DATA_DIR, "kline_daily.parquet")
ST_STOCK_PATH         = os.path.join(DATA_DIR, "st_stock.parquet")
DELIST_PATH           = os.path.join(DATA_DIR, "delist_period.parquet")

# ── 日期常量 ──────────────────────────────────────
CUTOFF_DATE = "20260105"           # stocks 过滤退市股的截止日期
KLINE_START_DATE = "20260105"      # kline 全量拉取起始日

# ── stocks.parquet schema ─────────────────────────
STOCKS_COLUMNS = ["code", "name", "list_status", "delist_date"]
STOCKS_SCHEMA = {
    "code": pl.Utf8,
    "name": pl.Utf8,
    "list_status": pl.Utf8,         # L=上市, D=退市, P=暂停
    "delist_date": pl.Utf8,         # None 表示未退市
}

# ── stocks_changelog.parquet schema ───────────────
CHANGELOG_COLUMNS = ["code", "field", "old_value", "new_value", "detected_at"]
CHANGELOG_SCHEMA = {
    "code": pl.Utf8,
    "field": pl.Utf8,               # name / list_status / delist_date / _new_ / _removed_
    "old_value": pl.Utf8,           # 可为 null
    "new_value": pl.Utf8,           # 可为 null
    "detected_at": pl.Datetime,     # 检测时间
}

# ── kline_daily.parquet schema ────────────────────
KLINE_COLUMNS = [
    "code", "trade_date",
    "open", "high", "low", "close",
    "volume", "amount",
    "amplitude", "pct_change", "turnover_rate",
]
KLINE_SCHEMA = {
    "code": pl.Utf8,
    "trade_date": pl.Date,
    "open": pl.Float64, "high": pl.Float64,
    "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Int64, "amount": pl.Float64,
    "amplitude": pl.Float64, "pct_change": pl.Float64,
    "turnover_rate": pl.Float64,
}
KLINE_REQUIRED_NONNULL = ["code", "trade_date", "close"]

# ── st_stock.parquet schema ───────────────────────
ST_COLUMNS = ["code", "name", "exchange", "trade_date", "type", "type_name"]
ST_SCHEMA = {
    "code": pl.Utf8, "name": pl.Utf8,
    "exchange": pl.Utf8, "trade_date": pl.Date,
    "type": pl.Utf8, "type_name": pl.Utf8,
}

# ── delist_period.parquet schema ──────────────────
# 保留 imp_date 列名（不改为 pub_date）
DELIST_COLUMNS = ["code", "name", "imp_date"]
DELIST_SCHEMA = {
    "code": pl.Utf8, "name": pl.Utf8,
    "imp_date": pl.Date,
}

# ── API 限制常量 ──────────────────────────────────
TUSHARE_RATE_LIMIT = 500            # 次/分钟
TUSHARE_RATE_WINDOW = 60.0          # 秒
MAX_RETRIES = 3                     # 单次请求最大重试次数
