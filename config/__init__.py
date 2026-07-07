"""
stock-analyzer 公共配置模块。

从各子模块 re-export，提供统一的 import 入口:
    from config import (
        get_pro, atomic_write_parquet, truncate_and_insert,
        RateLimiter, retry_on_failure,
        STOCKS_PATH, KLINE_PATH, ST_STOCK_PATH, DELIST_PATH,
        ...
    )

本地敏感配置（token 等）放在 config/local.py，该文件已加入 .gitignore。
首次 clone 后请复制模板：
    cp config/local.example.py config/local.py
"""

from config.constants import (          # noqa: F401
    # 路径
    PROJECT_ROOT, DATA_DIR,
    STOCKS_PATH, STOCKS_CHANGELOG_PATH, KLINE_PATH,
    ST_STOCK_PATH, DELIST_PATH,
    # 日期常量
    CUTOFF_DATE, KLINE_START_DATE, TRADE_CAL_START_DATE,
    # Schema
    STOCKS_COLUMNS, STOCKS_SCHEMA,
    CHANGELOG_COLUMNS, CHANGELOG_SCHEMA,
    KLINE_COLUMNS, KLINE_SCHEMA, KLINE_REQUIRED_NONNULL,
    # 指数
    INDICES_PATH, INDEX_LIST,
    INDICES_COLUMNS, INDICES_SCHEMA, INDICES_REQUIRED_NONNULL,
    ST_COLUMNS, ST_SCHEMA,
    DELIST_COLUMNS, DELIST_SCHEMA,
    # API 限制
    TUSHARE_RATE_LIMIT, TUSHARE_RATE_WINDOW, MAX_RETRIES,
    # 指标
    INDICATORS_PATH, INDICATORS_COLUMNS, INDICATORS_SCHEMA,
    # 交易日历
    TRADE_CAL_PATH, TRADE_CAL_COLUMNS, TRADE_CAL_SCHEMA,
)

from config.tushare_utils import (      # noqa: F401
    get_pro, clear_proxy, code_to_ts_code, ymd_to_dashed,
)

from config.retry import retry_on_failure  # noqa: F401

from config.ratelimit import RateLimiter  # noqa: F401

from config.trade_cal_utils import (      # noqa: F401
    get_nth_trading_day, is_trading_day, get_prev_trading_day,
)

from config.io import (                  # noqa: F401
    atomic_write_parquet,
    truncate_and_insert,
    full_merge_upsert,
    validate_no_null,
    validate_unique,
)
