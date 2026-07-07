# 需求分析报告

> **产品分析师：** 徐清楚
> **日期：** 2025-07-16
> **上下文：** 基于 sync_runner 系列脚本代码调研结果

---

## 目录

1. [需求 1：changelog detected_at 格式化为日期](#需求-1-changelog-detected_at-格式化为日期)
2. [需求 2：trade_cal 只保留全局起始日后数据](#需求-2-trade_cal-只保留全局起始日后数据)

---

# 需求 1：changelog detected_at 格式化为日期

## 原始需求摘要

将 `stocks_changelog.parquet` 的 `detected_at` 列从 `pl.Datetime` 改为 `pl.Date`（yyyy-MM-dd 精度），且要求**幂等**——已有旧文件重跑时不报错。

---

## 第一性原理拆解表

| 维度 | 分析 |
|------|------|
| **用户的真实意图** | changelog 是"按天粒度的变更记录"。用户只关心哪天检测到了变更，不需要精确到时分秒 |
| **核心要解决的问题** | 去除冗余的时间精度，让数据更精简、查询更直观 |
| **被认定为「已有假设」的部分** | ① 假设一天内同一个字段不会变化两次（如果一天内同一只股票改名又改回，按 Date 粒度会丢失一次变更 → **需要确认用户是否接受**）<br>② 假设所有消费者的查询维度都是按天，不需要按小时/分钟排序或聚合 |
| **不可约元素** | 变更检测的最细粒度单位就是"天"，时间戳的更高精度是噪声 |
| **幂等的本质** | 不是"同一个 datetime 重复写入不报错"，而是"不管旧文件的 schema 是 Datetime 还是 Date，新版本都能兼容追加" |

### ⚠ 需与用户确认的问题

> **问题：** 一天内同一个字段变更两次（如：某只股票早上改名，下午改回原名），按 Date 粒度会丢失中间的一次变更记录。当前业务是否接受此精度损失？

> **建议答：** 可以接受——stocks 的增量同步通常每天只跑一次，不存在一天内多次变化的情况。

---

## 剃刀定律精简建议

### 最小改动方案

**只有 3 处改动，不引入新函数、不重写旧文件、不改其他列：**

| 步骤 | 文件 | 改动内容 | 改动类型 |
|------|------|----------|----------|
| ① | `/Users/chenkaichen/stock-analyzer/config/constants.py` L43 | `"detected_at": pl.Datetime` → `"detected_at": pl.Date` | 1 行修改 |
| ② | `/Users/chenkaichen/stock-analyzer/scripts/sync_stocks.py` L118 | `detected_at = datetime.now()` → `detected_at = datetime.now().date()` | 1 行修改 |
| ③ | `/Users/chenkaichen/stock-analyzer/scripts/sync_stocks.py` `append_changelog()` | 在 concat 前检查旧文件 `detected_at` 类型：若为 `pl.Datetime`，先 `.cast(pl.Date)` 再 concat | 新增 ~5 行兼容逻辑 |

### 「可以砍掉」的部分

- ❌ **不需要** 重写/迁移已有 `stocks_changelog.parquet` 文件——运行时 cast 即可，首次运行后数据自动变成 pl.Date
- ❌ **不需要** 改动 `print_diff()`——它不检查 `detected_at` 的精度
- ❌ **不需要** 改动 `config/io.py`——`atomic_write_parquet` 不关心具体 schema
- ❌ **不需要** 改动 `sync_runner.py`——无依赖关系变化

### append_changelog 兼容逻辑伪代码

```python
def append_changelog(changelog_df: pl.DataFrame) -> None:
    if changelog_df.height == 0:
        return
    if os.path.exists(STOCKS_CHANGELOG_PATH):
        existing = pl.read_parquet(STOCKS_CHANGELOG_PATH)
        # 兼容旧数据：如果旧文件的 detected_at 是 Datetime，cast 为 Date
        if existing["detected_at"].dtype == pl.Datetime:
            existing = existing.with_columns(
                pl.col("detected_at").cast(pl.Date)
            )
        changelog_df = pl.concat([existing, changelog_df], how="vertical")
    atomic_write_parquet(changelog_df, STOCKS_CHANGELOG_PATH)
```

---

## Impact 评估矩阵

| 维度 | 影响 | 风险等级 |
|------|------|----------|
| **功能/用户侧** | 用户看到的 detected_at 从 `2026-07-16 10:30:45` 变为 `2026-07-16`。对按天分析的场景无影响。如果有人在消费 changelog 时依赖时分秒排序，会受影响。 | 🟢 低 |
| **技术栈/架构** | 仅涉及 2 个文件共 ~7 行改动。不涉及新依赖、新工具链。 | 🟢 低 |
| **业务/交付时间线** | 预计工时：~20 分钟编码 + ~10 分钟测试。可在同一次 PR 中完成。 | 🟢 低 |
| **向后兼容** | 旧文件（pl.Datetime）与新代码的第一次 concat 会因类型不匹配报错 → **必须处理**。上述方案中的 cast 逻辑已覆盖。 | 🟡 中 |

---

## 测试 Case 清单

| # | 场景 | 前置条件 | 输入/操作 | 预期结果 | 类型 |
|---|------|----------|-----------|----------|------|
| TC1-1 | 首次运行（无旧文件） | `stocks_changelog.parquet` 不存在 | `python sync_stocks.py --full` | changelog 写入成功，`detected_at` 列为 `pl.Date` 类型 | ✅ 正常路径 |
| TC1-2 | 增量追加（旧文件为 Date 类型） | 已有 changelog 文件 schema 为 `pl.Date` | 连续运行两次 `sync_stocks.py` | 第二次正常追加，不报错 | ✅ 正常路径 |
| TC1-3 | **向后兼容：旧文件为 Datetime 类型** | 已有 changelog 文件 schema 为 `pl.Datetime` | 运行 `sync_stocks.py` | concat 成功，新追加行 cast 为 Date，写入后全文件为 `pl.Date` | ✅ **幂等性** |
| TC1-4 | 无变更时执行 | 本地数据与远程一致 | 运行 `sync_stocks.py` | changelog 为 0 行，`append_changelog` 跳过，不报错 | ✅ 空值/边界 |
| TC1-5 | dry-run 模式 | 任意状态 | `python sync_stocks.py --dry-run` | 不写文件，不触发类型兼容问题 | ✅ 边界条件 |
| TC1-6 | 同一天多次运行（幂等验证） | 有 1 条旧 changelog | 连续运行 3 次 `sync_stocks.py` | 每次运行均成功写入，无 schema 冲突 | ✅ **幂等性** |
| TC1-7 | `detected_at` 值为空 | 无——generate_changelog 始终填充 | — | 不需要覆盖（generate_changelog 保证非空） | — |

---

# 需求 2：trade_cal 只保留全局起始日后数据

## 原始需求摘要

`sync_trade_cal.py` 目前全量拉取 Tushare 的交易日历数据（约 2000 年至今 ~1800 行）。要求只保留"全局起始日"之后的数据。

同时发现 `sync_st.py` 硬编码了 `date(2026, 1, 1)` 作为起始日，与 `KLINE_START_DATE = "20260105"` 不一致（差 4 天）。

---

## 第一性原理拆解表

| 维度 | 分析 |
|------|------|
| **用户的真实意图** | trade_cal 唯一用途是回答"某天是否为交易日""下一个/上一个交易日是哪天"。2026 年之前的历史数据对当前业务完全无用 |
| **核心要解决的问题** | ① 减少 trade_cal 数据量（从 ~1800 行到 ~250 行）<br>② 统一起始日定义，消除 `sync_st.py` 的硬编码魔数 |
| **被认定为「已有假设」的部分** | ① 假设"全局起始日" = `KLINE_START_DATE = "20260105"` → **但注意**：trade_cal 需要比实际数据起始日**更早**，因为 `get_nth_trading_day(d, -1)` 可能需要往前查找<br>② 假设未来不会将起始日调整到 2026 年之前 |
| **不可约元素** | 所有数据源（kline/indices/st_stock/delist）都需要一个统一的"起始参考日期"，trade_cal 只需要包含从该参考日期**往前足够覆盖天数**的交易日数据 |

### 关键发现：KLINE_START_DATE 与 sync_st.py 的差异

| 位置 | 当前值 | 说明 |
|------|--------|------|
| `config/constants.py` `KLINE_START_DATE` | `"20260105"` | kline 和 indices 的起始日 |
| `scripts/sync_st.py` L91 | `date(2026, 1, 1)` | ST 数据的起始日（**硬编码**） |

**差异分析：** 2026-01-01 是元旦假期（非交易日），2026-01-05 是周一且为当年第一个交易日。两者的差异在实际数据中没有影响（因为 1/1~1/4 没有交易数据），但概念上不一致，应当在常量层统一。

### 关于 TRADE_CAL_START_DATE 的选择

| 候选值 | 优点 | 缺点 |
|--------|------|------|
| `"20260105"`（= KLINE_START_DATE） | 与 kline 完全对齐 | `get_nth_trading_day(start_date=20260105, n=-1)` 需要 2025 年数据时不可用 |
| `"20260101"` | 更安全——包含了 KLINE_START_DATE 之前最近的非交易日，且与 sync_st.py 当前硬编码值对齐 | 略多几行数据（4 天，全为非交易日，不增加有效行数） |
| `"20250101"` | 最安全——向前覆盖一年 | 多了 ~250 行无用数据 |

**建议：** 使用 `"20260101"`。理由：
- trade_cal 的数据用途是"给定某日，判断是否为交易日 / 找上一个/下一个交易日"
- `get_nth_trading_day` 的 n 值通常很小（-1 ~ -5）
- `"20260101"` 在 2026-01-05 之前覆盖了 4 天（3 天周末 + 1 天元旦），足够满足 `n=-1` 的查找需求
- 与当前 sync_st.py 的 `date(2026, 1, 1)` 一致，消除魔数差异

---

## 剃刀定律精简建议

### 最小改动方案

| 步骤 | 文件 | 改动内容 | 改动类型 |
|------|------|----------|----------|
| ① | `/Users/chenkaichen/stock-analyzer/config/constants.py` | 新增常量：`TRADE_CAL_START_DATE = "20260101"` | 新增 1 行 |
| ② | `/Users/chenkaichen/stock-analyzer/scripts/sync_trade_cal.py` `sync()` | 在 `df.select(...).sort(...)` 之后、`atomic_write_parquet` 之前，添加过滤：`df.filter(pl.col("cal_date") >= pl.lit(date(2026, 1, 1)).cast(pl.Date))` | 新增 ~3 行 |
| ③ | `/Users/chenkaichen/stock-analyzer/scripts/sync_st.py` L91 | `date(2026, 1, 1)` → 从 config 导入并使用 `TRADE_CAL_START_DATE` | 1 行修改 |
| ④ | `/Users/chenkaichen/stock-analyzer/config/__init__.py` | 在新常量被其他模块引用时，加入 re-export | 1 行新增（如需） |

### 「可以砍掉」的部分

- ❌ **不需要** 改为增量模式（全量拉取 + 本地过滤写入，仍然是幂等的，改动量最小）
- ❌ **不需要** 把 `sync_st.py` 的 `date(2026, 1, 1)` 换成引用 `KLINE_START_DATE`——两个起始日的语义不同，trade_cal 起始日应该比 kline 起始日略早
- ❌ **不需要** 改动 `config/trade_cal_utils.py`——它只是读 parquet 文件，过滤后的文件仍然包含必要的数据
- ❌ **不需要** 改动 `sync_runner.py`——拓扑顺序和依赖关系不变
- ❌ **不需要** 改动 `sync_kline.py` / `sync_indices.py`——它们引用 `KLINE_START_DATE` 且不受影响

### sync_trade_cal.py 过滤逻辑

```python
# 在 df.select(...).sort(...) 之后写入之前
from datetime import date
from config.constants import TRADE_CAL_START_DATE

START = date(int(TRADE_CAL_START_DATE[:4]),
             int(TRADE_CAL_START_DATE[4:6]),
             int(TRADE_CAL_START_DATE[6:8]))
df = df.filter(pl.col("cal_date") >= START)
```

更简洁的方式——使用 `ymd_to_dashed` 转换：

```python
from config import ymd_to_dashed
from config.constants import TRADE_CAL_START_DATE

start_date = ymd_to_dashed(TRADE_CAL_START_DATE)  # "2026-01-01"
df = df.filter(pl.col("cal_date") >= pl.lit(start_date).str.to_date())
```

---

## Impact 评估矩阵

| 维度 | 影响 | 风险等级 |
|------|------|----------|
| **功能/用户侧** | trade_cal 从 ~1800 行减少到 ~250 行（2026 年至今的交易日）。`get_nth_trading_day(start_date=20260105, n=-1)` 仍可正常返回 `2025-12-31`——如果 start_date 本身在 2026-01-05 之后。但如果以后有人用 `get_nth_trading_day("2025-12-01", 1)` 会报错（数据不存在）。**需要在代码注释中明确 TRADE_CAL_START_DATE 的语义。** | 🟡 中 |
| **技术栈/架构** | 涉及 2~3 个文件共 ~5 行改动。不引入新依赖。 | 🟢 低 |
| **业务/交付时间线** | 预计工时：~15 分钟编码 + ~10 分钟测试 | 🟢 低 |
| **向后兼容** | trade_cal 是**全量覆盖写入**（非追加），不存在旧文件 schema 兼容问题。每次运行都会重新生成完整的过滤后的数据。 | 🟢 低 |

---

## 测试 Case 清单

| # | 场景 | 前置条件 | 输入/操作 | 预期结果 | 类型 |
|---|------|----------|-----------|----------|------|
| TC2-1 | 正常全量运行 | 无旧文件 | `python sync_trade_cal.py` | 只写入 ≥2026-01-01 的数据，`cal_date.min()` = 2026-01-01 或之后的第一个交易日 | ✅ 正常路径 |
| TC2-2 | 已存在旧文件，重新运行（幂等） | 已有全量旧 trade_cal | 运行 `python sync_trade_cal.py` | 新文件覆盖旧文件，结果与 TC2-1 一致 | ✅ **幂等性** |
| TC2-3 | 确认数据范围缩减 | 对比全量模式 | 比较过滤前后的行数 | 过滤后行数显著减少（~250 vs ~1800） | ✅ 正常路径 |
| TC2-4 | TRADE_CAL_START_DATE 为空 | — | 不需要覆盖（常量硬编码，不会为空） | — | — |
| TC2-5 | 验证 sync_st.py 使用新常量 | 任意 | 确认 `sync_st.py` 不再包含硬编码 `date(2026, 1, 1)` | 改为引用 `TRADE_CAL_START_DATE` | ✅ 边界条件 |
| TC2-6 | get_nth_trading_day 使用过滤后数据 | trade_cal 已更新 | `get_nth_trading_day("2026-01-05", -1)` | 返回上一个交易日（如果有的话，可能是 2025-12-31） | ✅ 向后兼容 |
| TC2-7 | get_nth_trading_day 查询超范围日期 | trade_cal 已更新 | `get_nth_trading_day("2025-12-01", 1)` | 应抛出 `ValueError`（因 2025-12-01 不在数据中，无交易日可返回） | ✅ **边界条件** |

---

## 总结

| 需求 | 核心改动文件 | 新增/修改行数 | 风险等级 | 优先级 |
|------|-------------|--------------|----------|--------|
| 需求 1：changelog detected_at 改为 Date | `constants.py`, `sync_stocks.py` | ~7 行 | 🟢 低 | P0 |
| 需求 2：trade_cal 保留起始日后数据 | `constants.py`, `sync_trade_cal.py`, `sync_st.py` | ~5 行 | 🟢 低 | P0 |

### 建议同时交付的理由

两个需求都是**原子级改动**，互不依赖，且都可在同一次 PR 中交付。合并交付的好处：
1. 只跑一次 CI
2. 只发一次 review
3. 改动量极小，评审成本低
