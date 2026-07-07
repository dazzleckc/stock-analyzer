# Phase 2 技术方案设计

> 版本：v1.0  
> 作者：高见远（系统架构师）  
> 日期：2026-07-07  
> 前置依赖：Phase 1 需求分析报告（已确认）

---

## 1. 整体架构 & 数据流

```
┌─────────────────────────────────────────────────────────────────┐
│                        数据同步层                                │
│  sync_runner.py (含 5 个子脚本, 已有)                           │
│  sync_stocks / sync_st / sync_indices / sync_kline / sync_delist│
│  + sync_trade_cal.py (新增, 独立运行, 不入拓扑)                 │
└───────────────────┬─────────────────────────────────────────────┘
                    │ 写入
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                       数据存储层                                 │
│  data/                                                          │
│    kline_daily.parquet    ← 个股日K线 (已存在)                   │
│    st_stock.parquet       ← ST状态 (已存在)                      │
│    delist_period.parquet  ← 退市整理期 (已存在)                  │
│    trade_cal.parquet      ← 交易日历 (新建)                      │
│    indicators.parquet     ← 市场指标 (新建)                      │
└───────────────────┬─────────────────────────────────────────────┘
                    │ 读取
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                       指标计算层                                 │
│  scripts/indicators.py  →  读3个源数据 → 算5个指标 → 写.parquet │
│    全量: 遍历所有交易日                                         │
│    增量: 只算缺失日期                                           │
└─────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                       工具层                                     │
│  config/trade_cal_utils.py    ← 交易日历查询工具                 │
│  config/constants.py          ← 新增 INDICATORS_* 和 TRADE_CAL_* │
│  config/__init__.py           ← re-export 新增常量              │
└─────────────────────────────────────────────────────────────────┘
```

### 数据流方向（以 indicators.py 增量模式为例）

```
[数据源]                         [计算]                          [输出]
kline_daily.parquet ──┐
                      ├──→ read → filter(ST/退市) → group by day
st_stock.parquet  ────┤                              ↓
                      │                    top20_amount_ratio
delist_period.parquet ┘                    up_ge7_count
                                           down_le7_count
trade_cal.parquet ──────→ 获取交易日列表
                                           net_high_20d
indicators.parquet ────→ 读"上次最新日期"    net_high_60d
                        → 只算缺失日期         ↓
                                        indicators.parquet (新)
```

---

## 2. 文件影响清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `config/constants.py` | **修改** | 新增 INDICATORS_PATH/COLUMNS/SCHEMA, TRADE_CAL_PATH/COLUMNS/SCHEMA |
| `config/__init__.py` | **修改** | re-export 新增的常量，以及 trade_cal_utils 的函数 |
| `config/trade_cal_utils.py` | **新建** | 3 个交易日历工具函数 |
| `scripts/indicators.py` | **新建** | 市场指标计算脚本 |
| `scripts/sync_trade_cal.py` | **新建** | 交易日历同步脚本 |
| `data/indicators.parquet` | **新建** | 市场指标输出文件（Parquet） |
| `data/trade_cal.parquet` | **新建** | 交易日历数据文件（Parquet） |
| `docs/technical-design.md` | **新建** | 本技术方案文档 |
| 其余文件 | **不变** | 不影响现有逻辑 |

---

## 3. indicators.py 详细设计

### 3.1 主函数签名与流程

```python
def calc_full() -> dict:
    """全量重算模式：所有交易日从头算起。
    
    流程：
    1. 读取 kline_daily.parquet / st_stock.parquet / delist_period.parquet
    2. 读 trade_cal.parquet 获取全部交易日列表（is_open=1，排序 asc）
    3. 对每个交易日依次调用 calc_single_day()
    4. 合并所有日结果 → atomic_write_parquet(indicators.parquet)
    返回: {"dates": int, "rows": int}
    """

def calc_incremental() -> dict:
    """增量计算模式：只算缺失日期。
    
    流程：
    1. 读取 kline_daily.parquet / st_stock.parquet / delist_period.parquet
    2. 读 trade_cal.parquet 获取全部交易日列表
    3. 如果 indicators.parquet 不存在 → print("无已有数据，请用 --full")
       sys.exit(1)
    4. 读 indicators.parquet 获取已有日期列表
    5. 交易日列表 - 已有日期 = 待算日期
    6. 无待算日期 → print("无需更新") 直接返回
    7. 对待算的每个日期依次调用 calc_single_day()
    8. 新数据 + 旧数据 → 按 trade_date 排序 → atomic_write_parquet(indicators.parquet)
    返回: {"dates": int, "rows": int, "new_dates": list}
    """

# CLI 入口
def main():
    parser = argparse.ArgumentParser(...)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if args.full: calc_full()
    else: calc_incremental()
```

### 3.2 calc_single_day() 算法伪代码

```
输入:
  day_dates  : 该交易日 date 对象（字符串，如 date(2026, 7, 1)）
  kline_all  : 全量 kline DataFrame（已提前读入内存）
  st_all     : 全量 st_stock DataFrame
  delist_all : 全量 delist_period DataFrame
  kline_high_20d_all : 提前计算的 20 日 rolling max of high（见 3.4）
  kline_high_60d_all : 提前计算的 60 日 rolling max of high（见 3.4）
  trade_date_list : 有序的全量交易日列表（用于"创 N 日新高"的 N 判定）

输出:
  dict 含该日 5 个指标

算法:
  # ── Step 1: 取当日所有股票数据 ──
  1. kline_today = kline_all.filter(trade_date == d)
     if kline_today 为空 → 返回 None（跳过该日）

  # ── Step 2: 获取当日 ST 股票集合 ──
  2. st_codes_today = st_all.filter(trade_date == d)["code"].unique()

  # ── Step 3: 获取当日以前已进入退市整理期的股票集合 ──
  3. delist_codes_today = delist_all.filter(imp_date <= d)["code"].unique()

  # ── Step 4: 合并过滤名单 ──
  4. exclude_codes = union(st_codes_today, delist_codes_today)

  # ── Step 5: 过滤后的当日数据 ──
  5. filtered = kline_today.filter(code NOT IN exclude_codes)
     if filtered 为空 → 返回 None

  # ── Step 6: TOP20 成交额占比 ──
  6. total_amount = filtered["amount"].sum()
     排名前20 = filtered.sort(amount desc).head(20)
     top20_amount_ratio = 前20["amount"].sum() / total_amount * 100

  # ── Step 7: 涨幅≥7% 和 跌幅≥7% 个数量 ──
  7. up_ge7_count   = filtered.filter(pct_change >= 7.0).height
     down_le7_count = filtered.filter(pct_change <= -7.0).height

  # ── Step 8: 净新高 ──
  8. 确定 d 在交易日列表中的序号 idx
     20 日窗口起点 = max(0, idx - 19)   # 注意：前 19 天冷启动
     60 日窗口起点 = max(0, idx - 59)   # 注意：前 59 天冷启动

     if idx < 19:    net_high_20d = None  (null)
     else:
       20 日窗口期内（含当天）的 filtered 股票列表
       对窗口期内每只股票，检查当天 high 是否 >= 该股票在该窗口期内的 rolling_max(high, 20)
       注意：这里的 rolling_max(high, 20) = 窗口期内 high 的最大值（含当天）
       等于也算新高
       新高数 = 满足条件的股票数
       新低数 = 当天 low 是否为窗口期内最低（rolling_min(low, 20)）
       net_high_20d = 新高数 - 新低数

     if idx < 59:    net_high_60d = None  (null)
     else:
       类似上，窗口为 60 日

  return {trade_date, top20_amount_ratio, up_ge7_count, 
          down_le7_count, net_high_20d, net_high_60d}
```

**重要澄清 — 净新高计算顺序（已确认）：**

```
全量 rolling → 标记(新高/新低) → 过滤(ST/退市) → 计数
```

即：先在全量数据上标记每只股票是否创 N 日新高/新低，然后针对过滤后的股票进行计数。不在过滤范围内的股票不计入新高/新低计数。

### 3.3 增量 vs 全量模式的具体逻辑

| 维度 | 全量 `--full` | 增量（默认） |
|------|-------------|------------|
| 启动条件 | 无前置条件 | 要求 `indicators.parquet` 已存在，否则报错退出 |
| 数据日期范围 | 从 kline 数据第一日到最后一日的所有交易日 | 从已有指标最新日期的**次日**起，到 kline 数据最后一日 |
| 读取已有指标 | 否，从头算 | 是，读已有文件获取已算日期列表 |
| 写入策略 | 原子覆盖写入完整文件 | 读旧 → 追加新日 → 按 trade_date 排序 → 原子写入 |
| 使用场景 | 首次运行 / schema 变更后重算 | 每日增量更新（几秒完成） |

**增量模式的日期发现逻辑：**

```python
def _find_missing_dates(trade_cal: pl.DataFrame, existing_path: str) -> list[date]:
    """返回需要计算的缺失交易日列表（有序，递增）。"""
    # 1. 读 trade_cal，筛出 is_open=1 的交易日
    all_trade_dates = (
        trade_cal.filter(pl.col("is_open") == 1)
        .sort("cal_date")["cal_date"]
        .to_list()
    )
    
    # 2. 读已有 indicators
    existing = pl.read_parquet(existing_path, columns=["trade_date"])
    existing_dates = set(existing["trade_date"].to_list())
    
    # 3. 差集
    missing = [d for d in all_trade_dates if d not in existing_dates]
    
    # 4. 进一步过滤：只算 kline 数据范围内（含）的日期
    #    如果 missing 中存在冷启动期（前19天/前59天），照算不误
    return missing
```

### 3.4 冷启动处理 — 代码级实现方式

**核心原则：**

- `net_high_20d`：交易日列表索引 < 19 的行 → `None`（null）；≥19 开始计算
- `net_high_60d`：交易日列表索引 < 59 的行 → `None`（null）；≥59 开始计算
- `min_samples=1`：Polars 的 `rolling_max` / `rolling_min` 函数设置 `min_periods=1`

**实现方式 — 基于 Polars 的 rolling window：**

```python
def _precompute_high_low_rolling(kline: pl.DataFrame, trade_dates: list[date]) -> pl.DataFrame:
    """
    提前计算每只股票的 rolling_max(high, N) 和 rolling_min(low, N)。
    
    N=20 和 N=60 分别计算，结果与 kline 行一一对应。
    
    思路：
    1. 对 kline 按 code 分组，每组按 trade_date 排序
    2. 对每组用 rolling:
       - high.rolling_max(window_size=20, min_periods=1) → rolling_high_20d
       - high.rolling_max(window_size=60, min_periods=1) → rolling_high_60d
       - low.rolling_min(window_size=20, min_periods=1)  → rolling_low_20d
       - low.rolling_min(window_size=60, min_periods=1)   → rolling_low_60d
    3. 合并回原 kline

    注意：这里的 window_size 是"行数滚动"，由于数据只有交易日，
    行数滚动 = 交易日滚动，符合需求。
    """
    kline = kline.sort(["code", "trade_date"])
    
    # 对每只股票计算 rolling 值
    for window, suffix in [(20, "20d"), (60, "60d")]:
        # 利用 Polars 的 rolling 表达式
        kline = kline.with_columns([
            pl.col("high").rolling_max(window_size=window, min_periods=1)
                .over("code").alias(f"rolling_high_{suffix}"),
            pl.col("low").rolling_min(window_size=window, min_periods=1)
                .over("code").alias(f"rolling_low_{suffix}"),
        ])
    
    return kline
```

**冷启动判定（在 calc_single_day 中）：**

```python
# 交易日列表索引
idx = trade_date_index_map[d]  # 从 dict 快速 O(1) 查找

# net_high_20d
if idx < 19:
    net_high_20d = None
else:
    today_rows = filtered.with_column(pl.lit(True))
    new_high = filtered.filter(
        pl.col("high") >= pl.col("rolling_high_20d")
    ).height if idx >= 19 else 0
    
    new_low = filtered.filter(
        pl.col("low") <= pl.col("rolling_low_20d")
    ).height if idx >= 19 else 0
    
    net_high_20d = new_high - new_low
```

**简化方案 — 直接在滚动窗口内比较：**

更准确的做法其实是在 `_precompute_high_low_rolling` 中已经计算好了每行的 rolling_max/min。然后判断当天 high 是否 `>=` rolling_max（含等于算新高），low 是否 `<=` rolling_min（含等于算新低）。

但由于需要过滤 ST 和退市，**预计算必须在过滤前**（因为 rolling 需要连续数据），过滤后再判断。

### 3.5 性能预期

**数据规模估算：**

| 维度 | 估算值 |
|------|--------|
| 股票数 | ~5,000 只 |
| 交易日数 | ~120 天（按当前 kline 数据范围估算）|
| kline_daily 行数 | ~600,000 行（5000×120） |
| st_stock 行数 | ~3,000 行 |
| delist_period 行数 | ~50 行 |

**全量模式耗时估算（120天）：**

| 步骤 | 耗时 | 说明 |
|------|------|------|
| 读取 3 个 Parquet 文件 | ~0.5s | Polars 延迟读取 + 列裁剪 |
| 预计算 rolling指标 | ~1-2s | 分组 rolling（20d+60d），分组数~5000 |
| 每日计算（120天） | ~3-6s | 每天约 25-50ms 过滤和聚合 |
| 写入 | ~0.3s | 原子写入 parquet |
| **总计** | **~5-9s** | 全量一次完成 |

**增量模式（1天）**：只需过滤计算 1 天，预计 **<1s**。

**内存峰值：** ~500MB（主要在预计算阶段，kline 全量在内存中做 group rolling）。

---

## 4. sync_trade_cal.py 详细设计

### 4.1 脚本设计

```python
"""交易日历同步脚本 — 独立运行，不纳入 sync_runner 拓扑。

数据源：Tushare trade_cal 接口（SSE 交易所）
策略：单次全量拉取 → 原子覆盖写入

使用方式：
  python scripts/sync_trade_cal.py

数据量：~1,800 行 / ~18KB（SSE 约 5 年交易日历）
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import polars as pl
from config import (
    TRADE_CAL_PATH, TRADE_CAL_COLUMNS,
    get_pro, atomic_write_parquet,
)


def sync_trade_cal() -> dict:
    """全量拉取 SSE 交易日历 → 原子覆盖写入。"""
    pro = get_pro()
    
    # 拉取数据（不设起止日期 → 返回全部可用历史）
    raw = pro.trade_cal(exchange="SSE")
    # raw 返回 pandas.DataFrame，列含：exchange, cal_date, is_open, pretrade_date
    
    # 转换：pandas → polars，类型对齐
    df = pl.DataFrame(raw.to_dict(orient="records"))
    df = df.with_columns([
        pl.col("cal_date").str.to_date(format="%Y%m%d"),
        pl.col("is_open").cast(pl.Int8),
        pl.col("pretrade_date").str.to_date(format="%Y%m%d"),
    ]).select(TRADE_CAL_COLUMNS).sort("cal_date")
    
    atomic_write_parquet(df, TRADE_CAL_PATH)
    
    n = len(df)
    n_open = df.filter(pl.col("is_open") == 1).height
    print(f"完成。共 {n} 行，{n_open} 个交易日，已写入 {TRADE_CAL_PATH}")
    return {"rows": n, "trading_days": n_open}


if __name__ == "__main__":
    sync_trade_cal()
```

### 4.2 数据转换细节

| Tushare 原始列 | 类型 | 目标列 | 目标类型 | 转换方式 |
|---------------|------|--------|---------|---------|
| exchange | str | exchange | pl.Utf8 | 直通 |
| cal_date | str (YYYYMMDD) | cal_date | pl.Date | `.str.to_date("%Y%m%d")` |
| is_open | int | is_open | pl.Int8 | `.cast(pl.Int8)` |
| pretrade_date | str (YYYYMMDD) | pretrade_date | pl.Date | `.str.to_date("%Y%m%d")` |

### 4.3 原子写入方案

利用已有的 `config.io.atomic_write_parquet()`：
1. 在 `data/` 目录创建 `.tmp` 临时文件
2. 写入转换后的完整 DataFrame
3. `os.replace()` 原子替换目标文件

无需 truncate-and-insert（交易日历是静态全量表，每次覆盖写入）。

### 4.4 不纳入 sync_runner 的理由

| 理由 | 说明 |
|------|------|
| 数据更新频率极低 | 交易日历几乎不变（仅每年底更新下一年历），无需跟随每日同步 |
| 无拓扑依赖 | sync_runner 中的其他脚本都不依赖 trade_cal |
| 操作简单 | 单次全量拉取 + 覆盖写入，无需增量逻辑 |

---

## 5. trade_cal_utils.py 详细设计

### 5.1 函数签名与实现要点

```python
import datetime
from typing import Optional
import polars as pl

def _load_trade_cal() -> pl.DataFrame:
    """加载交易日历，缓存在模块级变量中避免重复读盘。
    
    首次调用时从 TRADE_CAL_PATH 读取，之后复用缓存的 DataFrame。
    使用模块全局变量 _TRADE_CAL_CACHE 实现。
    
    返回：包含 exchange, cal_date, is_open, pretrade_date 的 DataFrame
    """
    ...


def get_nth_trading_day(
    start_date: datetime.date,
    n: int,
    trade_cal: Optional[pl.DataFrame] = None,
) -> datetime.date:
    """获取从 start_date 起第 n 个交易日。
    
    参数:
      start_date: 起始日期（包含当天）
      n: 偏移量。n=0 → 返回 start_date 本身（如果是交易日）或下一交易日
         n=1 → 下个交易日
         n=-1 → 上个交易日
         以此类推
      trade_cal: 可选的交易日历 DataFrame（一次读入多次复用）
    
    返回:
      计算后的交易日日期
    
    实现要点:
      1. 过滤出 is_open=1 的交易日列表，按 cal_date 排序
      2. 找到 start_date 在列表中的位置（如不在列表中，定位到下一个交易日）
      3. idx = pos + n，边界检查
      4. 返回 trade_dates[idx]
    
    异常:
      IndexError → 转为 ValueError（超出交易日历范围）
    """


def is_trading_day(
    date_str: str,
    trade_cal: Optional[pl.DataFrame] = None,
) -> bool:
    """判断某日是否为交易日。
    
    参数:
      date_str: "YYYY-MM-DD" 或 "YYYYMMDD" 格式的日期字符串
    
    返回:
      True / False
    
    实现要点:
      1. 解析为 date 对象
      2. 在 trade_cal 中查找：cal_date == d AND is_open == 1
      3. 存在 → True，否则 False
    """


def get_prev_trading_day(
    date_str: str,
    trade_cal: Optional[pl.DataFrame] = None,
) -> datetime.date:
    """获取上一个交易日。
    
    等效于 get_nth_trading_day(start_date, -1, trade_cal)
    
    实现要点:
      内部委托给 get_nth_trading_day
    """
```

### 5.2 缓存策略

```python
_TRADE_CAL_CACHE: Optional[pl.DataFrame] = None

def _load_trade_cal(trade_cal=None) -> pl.DataFrame:
    if trade_cal is not None:
        return trade_cal
    global _TRADE_CAL_CACHE
    if _TRADE_CAL_CACHE is None:
        from config import TRADE_CAL_PATH
        _TRADE_CAL_CACHE = pl.read_parquet(TRADE_CAL_PATH)
    return _TRADE_CAL_CACHE
```

### 5.3 使用示例

```python
# 一次性加载，多次复用
cal = pl.read_parquet(TRADE_CAL_PATH)

d1 = get_nth_trading_day(date(2026, 7, 1), 5, cal)     # 第5个交易日后
d2 = get_nth_trading_day(date(2026, 7, 1), -3, cal)    # 第3个交易日前
ok = is_trading_day("2026-07-01", cal)
prev = get_prev_trading_day("2026-07-01", cal)
```

---

## 6. 测试目录结构建议

项目目前尚无测试目录，建议按以下结构建立：

```
stock-analyzer/
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  # 共享 fixtures（如 mock kline data, mock st_stock）
│   │
│   ├── test_indicators.py            # indicators.py 单元测试
│   │   ├── test_calc_single_day()    # 测试单日指标计算
│   │   ├── test_top20_amount_ratio() # 验证 TOP20 比例计算
│   │   ├── test_up_ge7_down_le7()    # 验证涨跌幅计数
│   │   ├── test_net_high_20d_cold_start()  # 前19天 null
│   │   ├── test_net_high_60d_cold_start()  # 前59天 null
│   │   ├── test_st_filter_exact_date()     # ST 按日精准过滤
│   │   ├── test_delist_filter_cumulative() # 退市整理期累计过滤
│   │   ├── test_incremental_missing_dates()# 增量缺失日期发现
│   │   └── test_full_vs_incremental_consistency() # 全量=增量结果一致性
│   │
│   ├── test_sync_trade_cal.py        # sync_trade_cal.py 测试
│   │   ├── test_transform()          # pandas→polars 类型转换
│   │   └── test_atomic_write()       # 原子写入验证
│   │
│   ├── test_trade_cal_utils.py       # trade_cal_utils.py 测试
│   │   ├── test_get_nth_trading_day()
│   │   ├── test_is_trading_day()
│   │   ├── test_get_prev_trading_day()
│   │   └── test_cache_reuse()
│   │
│   └── data/                         # 测试数据（小样本 Parquet）
│       ├── sample_kline.parquet
│       ├── sample_st_stock.parquet
│       ├── sample_delist_period.parquet
│       └── sample_trade_cal.parquet
```

### 测试策略

| 层级 | 范围 | 工具 | 目标覆盖率 |
|------|------|------|-----------|
| 单元测试 | 单函数/单指标计算 | pytest | 核心算法 100% |
| 集成测试 | 全量/增量流程 | pytest + 临时文件 | 主流程覆盖 |
| 数据验证 | 与已有数据的一致性 | 手动对比 | 关键边界值 |

**约定：** 测试数据放在 `tests/data/` 下，使用小型 Parquet 样本（~10 只股票 × ~30 个交易日），快速执行。

---

## 7. 风险点评估

| # | 风险 | 影响 | 可能性 | 缓解措施 |
|---|------|------|--------|---------|
| 1 | **kline 数据量过大撑爆内存** | calc_full 时 ~600k 行 kline 做 group rolling，内存峰值 ~500MB | 低 | 使用列裁剪（只读需要的列：code/trade_date/high/low/amount/pct_change）；若仍不足可改为分批处理 |
| 2 | **净新高判定边界不一致** | 新高判定用 `>=` 还是 `>` 影响计数 | 低 | 已确认用 `>=`（等于也算新高），在 `_precompute_high_low_rolling` 和 `calc_single_day` 中统一语义 |
| 3 | **增量模式下次日发现算法精度** | 全量交易日 vs kline 实际有数据的交易日可能不一致 | 中 | 增量模式的 `_find_missing_dates` 先用 trade_cal 得交易日列表，再取差集，确保不遗漏任何交易日 |
| 4 | **ST 过滤不够精准** | 股票当天后半段才被 ST，全天数据都算？ | 低 | 已确认按日精准匹配。ST 状态的 `trade_date` 是生效日，当天 filter 掉 |
| 5 | **退市整理期过滤边界** | `imp_date <= d` 含当天，是否合理？ | 低 | 已确认含当天。进入整理期当天起就过滤掉 |
| 6 | **trade_cal 数据陈旧** | sync_trade_cal 长期不运行，`get_nth_trading_day` 返回错误 | 低 | 脚本独立运行 + 文档提醒每年至少运行一次；函数中 trade_cal 参数留空时自动从缓存读取 |
| 7 | **Polars rolling 的 min_periods=1 行为差异** | Polars 不同版本对 `rolling_max` 的 `min_periods` 处理可能有差异 | 中 | 锁定 requirements.txt 中的 polars 版本；在测试中覆盖 min_periods=1 的边界 case |
| 8 | **全量与增量结果不一致** | 增量模式因数据拼接顺序导致差异 | 中 | 在测试中增加 `test_full_vs_incremental_consistency`，用同一数据集验证两种模式产生相同结果 |

### 关键决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 计算引擎 | Polars 原生 / 逐日 Pandas 循环 | **Polars 原生** | 零新增依赖，全量内存计算，行数滚动符合需求 |
| 净新高预计算 | calc_single_day 内逐股算 / 预计算 rolling | **预计算 rolling** | 避免 N 只股票 × N 天 × 2 窗口 的 O(n²) 复杂度 |
| 交易日历存储 | Parquet / JSON / SQLite | **Parquet** | 统一数据存储格式，Polars 原生支持，~18KB 无压力 |
| indicators 写入策略 | 每日追加 / 全量重写 | **全量读-改-写**（增量模式） | indicators 仅 ~120 行 × 6 列，全量重写代价极小，避免文件碎片 |

---

## 8. 依赖关系总结

```
                    trade_cal_utils.py
                           ↑
                 ┌─────────┴──────────┐
                 │                    │
          sync_trade_cal.py    indicators.py
                 │                    │
                 │              ┌─────┴──────┐
                 │              │            │
           trade_cal.parquet  kline, st,  delist.parquet
                               delist     (已存在)
```

- `trade_cal_utils.py` → 依赖 `trade_cal.parquet`（通过 `sync_trade_cal.py` 生成）
- `indicators.py` → 依赖 `kline_daily.parquet` + `st_stock.parquet` + `delist_period.parquet` + `trade_cal.parquet`
- `sync_trade_cal.py` → 独立脚本，无上游依赖

**推荐执行顺序：**
```
1. python scripts/sync_trade_cal.py         （生成 trade_cal.parquet）
2. python scripts/sync_runner.py --full      （确保 kline/st/delist 最新）
3. python scripts/indicators.py --full       （全量计算指标）
```

**后续每日增量：**
```
1. python scripts/sync_runner.py              （增量同步数据）
2. python scripts/indicators.py               （增量计算缺失日指标）
```

---

> **文档版本记录**
> 
> | 版本 | 日期 | 变更内容 | 作者 |
> |------|------|---------|------|
> | v1.0 | 2026-07-07 | 初稿 | 高见远 |
