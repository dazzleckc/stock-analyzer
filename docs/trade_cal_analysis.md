# trade_cal 接口设计分析报告

> 分析师：徐清楚  
> 日期：2025-07-17  
> 版本：v1.0

---

## 1. 原始需求摘要

在现有数据管线（stocks / kline / ST / delist）中新增 **交易日历（trade_cal）** 数据同步，用于：
1. 精确计算冷启动期：从数据起始日算起，第 20 / 第 60 个交易日是哪天
2. 未来可能的日期对齐和窗口计算

数据源：Tushare Pro `trade_cal` 接口（积分 ≥ 2000，用户确认满足）。

---

## 2. 第一性原理拆解

### 2.1 核心本质

| 层次 | 问题 | 回答 |
|------|------|------|
| **本质需求** | 用户到底要什么？ | 给定任意日期，知道三个信息：①是交易日吗？②从某日起第 N 个交易日是哪天？③上一个交易日是哪天？ |
| **已有假设** | 当前方案隐含了什么？ | 假设日历数据需要"同步"，实际它是一份**静态参考数据**（一年变一次），不需要"每日增量同步" |
| **不可约元素** | 最小可工作单元是什么？ | 一个按日期排序的 `(date, is_trading_day)` 映射表 |

### 2.2 SSE vs SZSE 日历一致性

- **SSE（上交所）** 和 **SZSE（深交所）** 执行同一套国务院节假日安排 + 周末规则，交易日历**完全一致**。
- **北交所（BJ）** 虽属不同交易所，但交易日历也遵循统一规则。
- **结论**：只需拉取 **SSE 一家**的日历，即可覆盖全 A 股。

### 2.3 数据范围估算

| 维度 | 范围 | 行数估算 |
|------|------|---------|
| Exchange | SSE 一家（砍掉 SZSE） | 1 |
| 日期范围 | KLINE_START_DATE(2026-01-05) ~ 2030-12-31 | ~1,825 天 |
| **总计** | 1 exchange × ~5 年 | **~1,800 行** |

> 数据量极小（18 KB 级），一次 API 调用即可拉完。**无需"增量同步"设计。**

---

## 3. 剃刀定律精简

### 3.1 功能点逐项评估

| # | 功能点 | 必要？ | 建议 | 理由 |
|---|--------|--------|------|------|
| 1 | 拉取 SZSE 日历 | ❌ | **砍掉** | SSE = SZSE，数据完全重复 |
| 2 | 日频增量同步 | ❌ | **砍掉** | 节假日一年变一次，不需要每日跑 |
| 3 | 纳入 sync_runner 拓扑 | ❌ | **砍掉** | 无上游依赖 + 无下游依赖，无需编排 |
| 4 | 全量/增量模式切换 | ❌ | **砍掉** | 数据仅 ~1,800 行，每次都全量覆盖即可 |
| 5 | --date 补拉参数 | ❌ | **砍掉** | 同上，全量覆盖即可 |
| 6 | 保存 pretrade_date 字段 | ✅ | **保留** | 用于`上一个交易日`查找，避免自计算 |
| 7 | 转换 is_open 为 Int8 | ✅ | **保留** | 查询条件 is_open==1 / is_open==0，Int8 比 Utf8 更高效 |
| 8 | 工具函数 `get_nth_trading_day` | ✅ | **保留** | 核心消费场景 |
| 9 | 工具函数 `is_trading_day` | ✅ | **保留** | 快速判断某日期状态 |
| 10 | Changelog / diff 机制 | ❌ | **砍掉** | 日历数据只追加不修改，无 diff 必要 |

### 3.2 精简后最小集合

```
保留功能：
  ├─ sync_trade_cal.py          — 独立脚本，非拓扑内
  │     └─ 单次全量拉取 SSE 日历 → 原子覆盖
  ├─ config/trade_cal_utils.py   — 工具函数模块
  │     ├─ get_nth_trading_day(start, n)
  │     ├─ is_trading_day(date)
  │     └─ get_prev_trading_day(date)
  └─ config/constants.py 补丁
        ├─ TRADE_CAL_PATH
        ├─ TRADE_CAL_COLUMNS
        └─ TRADE_CAL_SCHEMA

砍掉：
  ├─ SZSE 交易所数据（冗余）
  ├─ sync_runner 拓扑注册（无必要）
  ├─ 增量模式 / --date 参数（全量覆盖即可）
  └─ changelog / diff 机制（日历只增不改）
```

---

## 4. 影响范围评估（Impact Matrix）

### 4.1 选项对比

| 维度 | **A: 独立 sync_trade_cal.py** | **B: 在 tushare_utils.py 加函数** | **C: 在 indicators.py 内联** |
|------|-------------------------------|----------------------------------|----------------------------|
| **遵循现有模式** | ✅ 与 sync_stocks / sync_st 一致 | ❌ 混合 API 连接与数据同步职责 | ❌ 混合分析与数据获取职责 |
| **可测试性** | ✅ 独立可测 | ⚠️ 需要 mock pro_api | ⚠️ 需要 mock parquet 读取 |
| **复用性** | ✅ 任意脚本均可 import | ✅ 任意脚本均可 import | ❌ 紧耦合于 indicators |
| **代码量** | ~50 行 | ~20 行 + 修改 constants.py | ~30 行 inline |
| **维护成本** | 低 | 中（耦合度上升） | 高（解耦成本在后） |
| **推荐** | **⭐⭐⭐ 最优** | ⭐ 次优 | ❌ 不推荐 |

### 4.2 对现有文件的精确影响

| 文件 | 变更类型 | 变更内容 |
|------|----------|----------|
| `config/constants.py` | **修改** | 新增 3 个常量：TRADE_CAL_PATH / COLUMNS / SCHEMA |
| `config/__init__.py` | **修改** | re-export 新增常量 + trade_cal_utils 函数 |
| `config/trade_cal_utils.py` | **新增** | 3 个工具函数（见 §6） |
| `scripts/sync_trade_cal.py` | **新增** | 独立同步脚本（~50 行，见 §7 样例） |
| `README.md` | **修改** | 新增数据源表格行 + data/ 目录说明 |
| `sync_runner.py` | **不变** | ✅ 不注册 trade_cal |
| `indicators.py` | **后续使用** | 通过 `from config import get_nth_trading_day` 调用 |
| 其他 sync_*.py | **不变** | ✅ 无影响 |

### 4.3 对 indicators.py 的消费影响

```
# indicators.py 中的使用场景

冷启动期计算：
  from config import get_nth_trading_day, TRADE_CAL_PATH
  
  cal = pl.read_parquet(TRADE_CAL_PATH)
  
  # 第 20 个交易日
  cold_start_20 = get_nth_trading_day("2026-01-05", 20, cal)
  
  # 第 60 个交易日  
  cold_start_60 = get_nth_trading_day("2026-01-05", 60, cal)
  
  # 之后用 cold_start_xx 做窗口过滤：
  kline.filter(
      pl.col("trade_date").is_between(
         start_date, cold_start_20
      )
  )
```

---

## 5. Schema 设计

### 5.1 常量定义（constants.py 追加）

参照现有 `KLINE_SCHEMA` / `ST_SCHEMA` 风格：

```python
# ── trade_cal.parquet schema ─────────────────────
TRADE_CAL_PATH = os.path.join(DATA_DIR, "trade_cal.parquet")

TRADE_CAL_COLUMNS = ["exchange", "cal_date", "is_open", "pretrade_date"]
TRADE_CAL_SCHEMA = {
    "exchange": pl.Utf8,           # SSE / SZSE（仅保留 SSE）
    "cal_date": pl.Date,           # 日历日期
    "is_open": pl.Int8,            # 0=休市, 1=交易
    "pretrade_date": pl.Date,      # 上一个交易日（可为 null）
}
```

### 5.2 设计决策说明

| 决策 | 选择 | 理由 |
|------|------|------|
| `is_open` 类型 | `Int8` 而非 `Utf8` | 查询 `cal.filter(pl.col("is_open") == 1)` 比字符串比较更高效 |
| `pretrade_date` 保留 | ✅ 保留 | 用于快速找"上一个交易日"，免去自计算 |
| `cal_date` 类型 | `Date` 而非 `Utf8` | 与 `kline_daily.trade_date` 类型一致，支持 `is_between`、日期运算 |
| Exchange 过滤 | 只存 SSE | 节省 50% 空间，SSE = SZSE 完全一致 |

---

## 6. 工具函数设计（config/trade_cal_utils.py）

```python
"""
交易日历工具函数：基于 trade_cal.parquet 提供日期计算。

所有函数接受可选的 trade_cal DataFrame 参数（一次读入，多次复用），
避免在循环中重复读取 parquet 文件。
"""

import datetime
import os
from typing import Optional

import polars as pl

from config.constants import TRADE_CAL_PATH


def _load_cal() -> pl.DataFrame:
    """加载交易日历（惰性加载，仅首次调用时读取 parquet）。"""
    if not os.path.exists(TRADE_CAL_PATH):
        raise FileNotFoundError(
            f"未找到 {TRADE_CAL_PATH}，请先运行 scripts/sync_trade_cal.py --full"
        )
    return pl.read_parquet(TRADE_CAL_PATH)


def get_nth_trading_day(
    start_date: str | datetime.date,
    n: int,
    trade_cal: Optional[pl.DataFrame] = None,
) -> datetime.date:
    """从 start_date（含）起第 n 个交易日。

    参数:
        start_date: 起始日期（YYYY-MM-DD 字符串或 date 对象）
        n: > 0 往后找第 n 个交易日；< 0 往前找第 |n| 个交易日
        trade_cal: 可复用的日历 DataFrame，None 则自动加载

    返回:
        第 n 个交易日的 date 对象

    示例:
        get_nth_trading_day("2026-01-05", 20)   # 第 20 个交易日
        get_nth_trading_day("2026-07-17", -5)   # 往前第 5 个交易日
    """
    if trade_cal is None:
        trade_cal = _load_cal()

    if isinstance(start_date, str):
        start_date = datetime.date.fromisoformat(start_date)

    trading_days = (
        trade_cal
        .filter(pl.col("is_open") == 1)
        .sort("cal_date")
    )

    if n > 0:
        # 往后找
        subset = trading_days.filter(pl.col("cal_date") >= start_date)
        if subset.height < n:
            raise ValueError(
                f"从 {start_date} 起仅有 {subset.height} 个交易日，不足 {n} 个"
            )
        return subset.row(n - 1)[1]  # (exchange, cal_date, is_open, pretrade_date) → cal_date
    else:
        # 往前找
        n_abs = abs(n)
        subset = trading_days.filter(pl.col("cal_date") <= start_date)
        if subset.height < n_abs:
            raise ValueError(
                f"从 {start_date} 往前仅有 {subset.height} 个交易日，不足 {n_abs} 个"
            )
        return subset.row(subset.height - n_abs)[1]


def is_trading_day(
    date_str: str | datetime.date,
    trade_cal: Optional[pl.DataFrame] = None,
) -> bool:
    """判断某日期是否为交易日。"""
    if trade_cal is None:
        trade_cal = _load_cal()
    if isinstance(date_str, str):
        date_str = datetime.date.fromisoformat(date_str)
    row = trade_cal.filter(pl.col("cal_date") == date_str)
    if row.is_empty():
        return False
    return row[0, "is_open"] == 1


def get_prev_trading_day(
    date_str: str | datetime.date,
    trade_cal: Optional[pl.DataFrame] = None,
) -> datetime.date:
    """获取上一个交易日（若本身是交易日，则返回自身）。"""
    if trade_cal is None:
        trade_cal = _load_cal()
    if isinstance(date_str, str):
        date_str = datetime.date.fromisoformat(date_str)
    subset = (
        trade_cal
        .filter((pl.col("cal_date") <= date_str) & (pl.col("is_open") == 1))
        .sort("cal_date", descending=True)
    )
    if subset.is_empty():
        raise ValueError(f"在 {date_str} 之前无交易日")
    return subset[0, "cal_date"]
```

---

## 7. Sync 脚本设计（sync_trade_cal.py）

### 7.1 设计方案

```python
"""交易日历同步脚本

数据源：Tushare Pro trade_cal
策略：单次全量拉取 SSE 日历 → 原子覆盖（约 1,800 行，一次 API 调用）

使用方式：
  python scripts/sync_trade_cal.py       # 全量拉取
  python scripts/sync_trade_cal.py --full # 同上（统一习惯）
"""

import argparse
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (
    TRADE_CAL_PATH, TRADE_CAL_COLUMNS, TRADE_CAL_SCHEMA,
    get_pro, atomic_write_parquet,
)


def sync() -> dict:
    """拉取 SSE 交易日历 → 转换 → 原子覆盖。

    返回: {"rows": int, "years": str}
    """
    pro = get_pro()
    print("拉取 trade_cal（SSE） ...")
    pdf = pro.trade_cal(exchange="SSE")

    if pdf is None or pdf.empty:
        raise RuntimeError("trade_cal 接口返回空数据")

    df = (
        pl.from_pandas(pdf)
        .with_columns([
            pl.col("cal_date").str.to_date(format="%Y%m%d"),
            pl.col("is_open").cast(pl.Int8),
            pl.col("pretrade_date").str.to_date(format="%Y%m%d"),
        ])
        .select(TRADE_CAL_COLUMNS)
        .sort("cal_date")
    )

    print(f"  {len(df)} 行，{df['cal_date'].min()} ~ {df['cal_date'].max()}")

    atomic_write_parquet(df, TRADE_CAL_PATH)
    print(f"  → 已保存到 {TRADE_CAL_PATH}")

    return {"rows": len(df)}


def main():
    parser = argparse.ArgumentParser(
        description="交易日历同步：全量拉取 SSE 日历 → 原子覆盖"
    )
    parser.add_argument("--full", action="store_true", help="全量模式（默认行为）")
    parser.parse_args()
    result = sync()
    print(f"\n完成。{result['rows']} 行")


if __name__ == "__main__":
    main()
```

### 7.2 为什么不纳入 sync_runner 拓扑？

| 角度 | 说明 |
|------|------|
| **依赖关系** | trade_cal 无上游依赖（不依赖 stocks/st/indices），也无下游 sync 脚本依赖它 |
| **更新频率** | 节假日一年变一次，不需要每日跑；只需在有节假日变动时手动跑一次 |
| **数据量** | ~1,800 行 ≈ 18KB，一次 API 调用搞定，无"增量"必要 |
| **失败影响** | trade_cal 同步失败不影响 stocks/kline/indices 等核心数据管线 |

**结论**：在 `sync_runner.py` 中注册 trade_cal 只会增加拓扑复杂度、降低 runner 可靠性，没有实际收益。

---

## 8. 测试用例清单

### 8.1 工具函数测试

| # | 测试场景 | 输入 | 期望输出 |
|---|----------|------|----------|
| TC1 | 往后第 20 个交易日（正常） | start="2026-01-05", n=20 | 返回 date 对象，且 is_open==1 |
| TC2 | 往后第 1 个交易日（含起始） | start="2026-01-05", n=1 | 若 2026-01-05 是交易日 → 返回 2026-01-05 |
| TC3 | 往前第 5 个交易日 | start="2026-07-17", n=-5 | 返回 5 个交易日前 date |
| TC4 | 越界（往后天数不足） | start="2026-12-31", n=20 | 抛 ValueError |
| TC5 | 越界（往前天数不足） | start="2026-01-05", n=-100 | 抛 ValueError |
| TC6 | 判断交易日 | date="2026-01-05" | True / False（对照实际日历） |
| TC7 | 判断节假日 | date="2026-01-01" | False |
| TC8 | 判断不存在日期 | date="2099-01-01" | False（不抛异常） |
| TC9 | 获取上一个交易日（工作日后一天） | date="2026-01-06" | 若 01-05 是交易日 → 返回 2026-01-05 |
| TC10 | 获取上一个交易日（自身是交易日） | date="2026-01-05" | 返回 2026-01-05 |

### 8.2 Sync 脚本测试

| # | 测试场景 | 预期结果 |
|---|----------|----------|
| TC11 | 首次运行（data/ 下无 trade_cal.parquet） | 创建新文件，打印行数 + 日期范围 |
| TC12 | 二次运行（已有文件） | 原子覆盖，行数不变 |
| TC13 | Tushare 接口返回空 | 抛 RuntimeError |

---

## 9. 实施步骤（推荐顺序）

```
Step 1: config/constants.py    — 追加 TRADE_CAL_PATH / COLUMNS / SCHEMA
Step 2: config/trade_cal_utils.py — 实现 3 个工具函数
Step 3: config/__init__.py     — re-export 新增常量和函数
Step 4: scripts/sync_trade_cal.py — 实现同步脚本
Step 5: 手动运行一次 python scripts/sync_trade_cal.py 验证
Step 6: 后续在 indicators.py 中通过 get_nth_trading_day() 消费
```

> **无需修改的文件**：sync_runner.py、所有已有 sync_*.py、tushare_utils.py、io.py

---

## 10. 附录：与现有模式的对照

| 维度 | 现有 sync_*.py | sync_trade_cal.py（本设计） |
|------|---------------|---------------------------|
| 拓扑位置 | sync_runner 编排 | **独立运行**，不注册拓扑 |
| 模式 | sync_full / sync_incremental | **单次全量**，不含增量逻辑 |
| 并发 | ThreadPoolExecutor | **无并发**（单次 API 调用） |
| 写入策略 | truncate_and_insert / full_merge_upsert | **atomic_write_parquet**（全量覆盖） |
| 参数 | --full / --date / --workers | **--full**（仅保持习惯一致） |
| 变更追踪 | changelog 生成 + 打印 diff | **无需 changelog**（日历不修改） |
