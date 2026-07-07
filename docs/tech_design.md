# 技术方案文档

> **系统架构师：高见远** | Phase 2 — 技术方案设计
>
> 基于已确认的两项需求，输出最小可行技术方案。

---

## 1. 需求概述

| # | 需求 | 用户确认 |
|---|------|----------|
| 1 | `stocks_changelog.parquet` 的 `detected_at` 列从 `pl.Datetime` 改为 `pl.Date`（精度损失可接受） | ✅ |
| 2 | `trade_cal.parquet` 只保留 `TRADE_CAL_START_DATE = 2026-01-01` 之后的数据 | ✅ |

### 规模判断

| 指标 | 值 |
|------|-----|
| 涉及源文件 | 4 个：`config/constants.py`, `scripts/sync_stocks.py`, `scripts/sync_trade_cal.py`, `scripts/sync_st.py` |
| 涉及其它文件 | 1 个：`config/__init__.py`（re-export 新常量） |
| 合计核心文件 | 5 个 |
| 关键因子 | 每处改动 ≤ 3 行，总改动 ≈ 20-25 行 |
| 结论 | **小需求**，Phase 3 一次性开发 |

---

## 2. 技术方案总览

### 2.1 需求 1：changelog detected_at → pl.Date

**改动 3 处，共 ~8 行：**

#### 2.1.1 `config/constants.py`

| 位置 | 原文 | 改后 |
|------|------|------|
| L43 | `"detected_at": pl.Datetime` | `"detected_at": pl.Date` |

**理由：** 精度从 微秒级 退到 日期级。用户已确认精度损失可接受。改为 Date 后：
- 数据更紧凑（每个值从 64-bit 降为 32-bit int）
- 与 KLINE / TRADE_CAL / ST 等表的日期粒度一致
- 避免 datetime 与 date 混用带来的下游类型问题

#### 2.1.2 `scripts/sync_stocks.py` — `generate_changelog()`

| 位置 | 原文 | 改后 |
|------|------|------|
| L118 | `detected_at = datetime.now()` | `detected_at = datetime.now().date()` |

**理由：** 与 schema 类型对齐，源头产生 `date` 而非 `datetime`。

#### 2.1.3 `scripts/sync_stocks.py` — `append_changelog()`

**新增向后兼容逻辑**（L156-158 之间插入）：

```python
# 旧文件 detected_at 类型兼容：若为 Datetime 则 cast 为 Date
if existing.schema["detected_at"] == pl.Datetime:
    existing = existing.with_columns(
        pl.col("detected_at").cast(pl.Date)
    )
```

**理由：** 本地硬盘可能已有旧格式的 `stocks_changelog.parquet`（`detected_at` 为 `pl.Datetime`）。不处理则 `pl.concat([existing, changelog_df])` 会因为 schema 不匹配而抛出 `ComputeError`。

**向后兼容策略总结：**

| 场景 | 行为 |
|------|------|
| 首次运行（文件不存在） | 直接写入新格式 |
| 旧文件（pl.Datetime）+ 新数据（pl.Date） | 读取旧文件后 cast detected_at → 再 concat |
| 旧文件已是最新格式 | 无需 cast，直接 concat |
| 仅 cast detected_at 列 | 不破坏旧数据的其他字段 |

### 2.2 需求 2：trade_cal 只保留 TRADE_CAL_START_DATE 之后数据

**改动 3 处，共 ~10 行：**

#### 2.2.1 `config/constants.py`

在现有日期常量区域（L23-25 附近）新增：

```python
TRADE_CAL_START_DATE = "20260101"  # 交易日历保留起始日
```

同时在 `config/__init__.py` 的 re-export 列表中加入 `TRADE_CAL_START_DATE`。

#### 2.2.2 `scripts/sync_trade_cal.py` — `sync()`

在写入前（L70 之前）插入过滤步骤：

```python
from datetime import date
# ...（在 with_columns / select 之后，atomic_write 之前）
from config import TRADE_CAL_START_DATE

start = date(
    int(TRADE_CAL_START_DATE[:4]),
    int(TRADE_CAL_START_DATE[4:6]),
    int(TRADE_CAL_START_DATE[6:8]),
)
df = df.filter(pl.col("cal_date") >= start)
```

**理由：**
- Tushare trade_cal 返回全量（1990 年至今 ~8,000 行），过滤后约 ~250 行
- 数据量大幅减少（写入 ~5KB vs ~30KB）
- 后续所有依赖 trade_cal 的查询不再需要额外的日期过滤

**幂等性说明：** 过滤在 atomic_write 之前，无论运行多少次结果一致。

#### 2.2.3 `scripts/sync_st.py` — `sync_full()`

| 位置 | 原文 | 改后 |
|------|------|------|
| L91 | `start_dt = date(2026, 1, 1)` | `start_dt = date(int(TRADE_CAL_START_DATE[:4]), int(TRADE_CAL_START_DATE[4:6]), int(TRADE_CAL_START_DATE[6:8]))` |

**理由：** 硬编码日期 → 引用常量，消除重复。

**简化方案评估：** 是否可以将 `TRADE_CAL_START_DATE` 定义为 `datetime.date` 对象而非字符串？

| 方案 | 优点 | 缺点 |
|------|------|------|
| 字符串 `"20260101"` | 与 `CUTOFF_DATE` 风格一致；易序列化 | 使用时需要手动 parse |
| `date` 对象 | 无需 parse，直接用于比较 | 常量模块被 import 时即执行，不便外部替换 |

**选择：字符串**。与现有 `CUTOFF_DATE`、`KLINE_START_DATE` 风格一致，且序列化友好。

---

## 3. 测试目录结构

### 3.1 目录组织

```
tests/
├── __init__.py
├── test_indicators.py       # 已有
├── conftest.py              # 【新增】共享 fixture（数据路径、辅助函数）
├── test_sync_stocks.py      # 【新增】changelog detected_at 类型测试
└── test_constants.py        # 【新增】常量定义验证
```

**说明：**
- `test_sync_trade_cal.py` 不单独拆分测试文件，因为 trade_cal 过滤逻辑直接在 `test_constants.py` 中覆盖
- `test_sync_st.py` 的改动（硬编码 → 常量引用）由 `test_constants.py` 的集成测试覆盖
- `test_sync_stocks.py` 只测试 `generate_changelog` 和 `append_changelog` 的逻辑，不 mock Tushare API

### 3.2 测试框架约定

| 项 | 选型 |
|----|------|
| 测试框架 | pytest（已有） |
| 命名规范 | `test_<module>.py` |
| 函数命名 | `test_<tcXX>_<描述>()` 或 `test_<描述>()` |
| Fixture 层级 | `conftest.py`（模块级共享）、函数内局部构造 |
| 数据策略 | 构造假数据 DataFrame（不依赖 Tushare API） |

### 3.3 测试用例定义

#### TC-SCHEMA-01：CHANGELOG_SCHEMA detected_at 类型验证

| 字段 | 值 |
|------|-----|
| 测试函数 | `test_changelog_schema_detected_at_is_date()` |
| 位置 | `tests/test_constants.py` |
| 验证 | `CHANGELOG_SCHEMA["detected_at"] == pl.Date` |
| 数据 | 无（仅断言 schema 定义） |

#### TC-SCHEMA-02：TRADE_CAL_START_DATE 常量存在且格式正确

| 字段 | 值 |
|------|-----|
| 测试函数 | `test_trade_cal_start_date_exists()` |
| 位置 | `tests/test_constants.py` |
| 验证 | `TRADE_CAL_START_DATE` 为字符串，长度 8，全为数字 |
| 数据 | 无 |

#### TC-SYNC-01：generate_changelog 产生 Date 类型的 detected_at

| 字段 | 值 |
|------|-----|
| 测试函数 | `test_generate_changelog_detected_at_is_date()` |
| 位置 | `tests/test_sync_stocks.py` |
| 输入 | 构造新旧两个简单的 stocks DataFrame |
| 验证 | 返回的 changelog 的 `detected_at` 列的 dtype 为 `pl.Date` |
| 数据 | 手动构造 2-3 条记录 |

#### TC-SYNC-02：append_changelog 兼容旧 pl.Datetime 数据

| 字段 | 值 |
|------|-----|
| 测试函数 | `test_append_changelog_backward_compat()` |
| 位置 | `tests/test_sync_stocks.py` |
| 输入 | 构造一个 `detected_at` 为 `pl.Datetime` 的旧 parquet 文件（临时文件） |
| 步骤 | append 新数据（`pl.Date`）→ 读取结果 → 验证 schema 统一为 `pl.Date` |
| 验证 | 不抛异常，行数正确，`detected_at` 为 `pl.Date` |
| 数据 | 临时 parquet 文件（test 后清理） |

#### TC-SYNC-03：trade_cal sync 过滤早于 TRADE_CAL_START_DATE 的数据

| 字段 | 值 |
|------|-----|
| 测试函数 | `test_trade_cal_sync_filters_before_start_date()` |
| 位置 | `tests/test_constants.py`（集成验证） |
| 输入 | 构造包含全量日期的假 cal_data DataFrame |
| 步骤 | 应用过滤逻辑（直接调用过滤段代码） |
| 验证 | 结果中最小的 `cal_date` ≥ TRADE_CAL_START_DATE 对应的 date |
| 数据 | 手动构造含早于 2026-01-01 的日期 |

#### TC-SYNC-04：sync_st.py 引用 TRADE_CAL_START_DATE

| 字段 | 值 |
|------|-----|
| 测试函数 | `test_sync_st_references_trade_cal_start_date()` |
| 位置 | `tests/test_constants.py` |
| 验证 | 直接 import 并检查 `sync_full` 函数中硬编码已移除（通过 grep 函数源码文本验证，不做运行时调用） |
| 备注 | 运行时测试需 mock 接口，成本过高。本测试仅静态确认 |

---

## 4. 改动文件清单

### 4.1 影响文件总览

| 文件 | 改动类型 | 改动位置 | 预估行数 | 说明 |
|------|----------|----------|----------|------|
| `config/constants.py` | 修改 + 新增 | L43 修改 + 新增常量 | 2 行 | 改 schema + 新增 `TRADE_CAL_START_DATE` |
| `config/__init__.py` | 修改 | re-export 列表 | 1 行 | 增加 `TRADE_CAL_START_DATE` |
| `scripts/sync_stocks.py` | 修改 | L118 + 兼容逻辑 | 5 行 | 改 `datetime.now()` → `.date()` + append 兼容 |
| `scripts/sync_trade_cal.py` | 修改 | 写入前过滤 | 3 行 | import + filter 语句 |
| `scripts/sync_st.py` | 修改 | L91 | 2 行 | 硬编码 → 常量引用 |
| **合计** | | | **13 行** | |

### 4.2 新文件

| 文件 | 说明 |
|------|------|
| `tests/conftest.py` | 共享 fixture |
| `tests/test_constants.py` | 常量定义验证 |
| `tests/test_sync_stocks.py` | changelog 类型相关测试 |

### 4.3 改动明细

#### `config/constants.py`

```diff
 # ── 日期常量 ──────────────────────────────────────
 CUTOFF_DATE = "20260105"           # stocks 过滤退市股的截止日期
 KLINE_START_DATE = "20260105"      # kline 全量拉取起始日
+TRADE_CAL_START_DATE = "20260101"  # 交易日历保留起始日（含）

 # ── stocks_changelog.parquet schema ───────────────
 CHANGELOG_COLUMNS = ["code", "field", "old_value", "new_value", "detected_at"]
 CHANGELOG_SCHEMA = {
     "code": pl.Utf8,
     "field": pl.Utf8,
     "old_value": pl.Utf8,
     "new_value": pl.Utf8,
-    "detected_at": pl.Datetime,
+    "detected_at": pl.Date,
 }
```

#### `config/__init__.py`

```diff
     # 日期常量
-    CUTOFF_DATE, KLINE_START_DATE,
+    CUTOFF_DATE, KLINE_START_DATE, TRADE_CAL_START_DATE,
```

#### `scripts/sync_stocks.py`

```diff
 # L118: generate_changelog() 内部
-detected_at = datetime.now()
+detected_at = datetime.now().date()

 # L156-158: append_changelog() 内部，concat 之前插入
 if os.path.exists(STOCKS_CHANGELOG_PATH):
     existing = pl.read_parquet(STOCKS_CHANGELOG_PATH)
+    # 向后兼容：旧文件 detected_at 可能为 Datetime
+    if existing.schema["detected_at"] == pl.Datetime:
+        existing = existing.with_columns(
+            pl.col("detected_at").cast(pl.Date)
+        )
     changelog_df = pl.concat([existing, changelog_df], how="vertical")
```

#### `scripts/sync_trade_cal.py`

```diff
+from datetime import date
+from config import TRADE_CAL_START_DATE

 # 在类型转换 / select / sort 之后，atomic_write 之前
+start = date(
+    int(TRADE_CAL_START_DATE[:4]),
+    int(TRADE_CAL_START_DATE[4:6]),
+    int(TRADE_CAL_START_DATE[6:8]),
+)
+df = df.filter(pl.col("cal_date") >= start)
```

#### `scripts/sync_st.py`

```diff
+from config import TRADE_CAL_START_DATE

 # L91
-start_dt = date(2026, 1, 1)
+start_dt = date(
+    int(TRADE_CAL_START_DATE[:4]),
+    int(TRADE_CAL_START_DATE[4:6]),
+    int(TRADE_CAL_START_DATE[6:8]),
+)
```

---

## 5. 部署方案

本阶段不涉及部署架构变更。变更均为源码级别，无基础设施 / CI/CD 改动。

### 5.1 版本兼容

| 场景 | 兼容性 |
|------|--------|
| 旧 stocks_changelog.parquet（pl.Datetime） | ✅ append_changelog 自动 cast |
| 新写入的数据（pl.Date） | ✅ 正常读写 |
| 暂无 trade_cal.parquet | ✅ sync_trade_cal 首次运行自动过滤 |
| 已有旧 trade_cal.parquet（1990 年起） | ⚠️ 需要手动重新运行一次 `sync_trade_cal.py` 以应用过滤 |

### 5.2 数据迁移

**trade_cal.parquet**：此次改动不自动迁移旧文件。用户需要：

```bash
python scripts/sync_trade_cal.py   # 重新拉取并过滤
```

因为 trade_cal 是全量覆盖写入，重新运行一次即可。

**stocks_changelog.parquet**：无需迁移。向后兼容代码自动处理旧文件。

---

## 6. 风险矩阵

| # | 风险 | 概率 | 影响 | 缓解措施 |
|---|------|------|------|----------|
| R1 | 旧 stocks_changelog.parquet 的 `detected_at` 为 `pl.Datetime`，新数据为 `pl.Date`，concat 抛出 `SchemaError` | **高** | 高（脚本崩溃） | `append_changelog()` 中读取旧文件后检查 dtype 并 cast |
| R2 | `sync_st.py` 中 `TRADE_CAL_START_DATE` 引用后，全量跑批从 `2026-01-01` 改为别的值导致数据不一致 | 低 | 中 | 常量的语义已在需求阶段确认，不允许随意变更 |
| R3 | 其他脚本也硬编码了 `date(2026, 1, 1)` 做 ST 相关查询 | 中 | 低 | 扫描代码库确认无其他硬编码，本次只涉及 `sync_st.py` L91 |
| R4 | `ymd_to_dashed` 与 `TRADE_CAL_START_DATE` 格式一致性问题 | 低 | 低 | `TRADE_CAL_START_DATE` 也是 `%Y%m%d` 格式，与 `ymd_to_dashed` 兼容 |

### 6.1 代码扫描确认

搜索是否有其他文件硬编码了 `date(2026, 1, 1)` 或类似日期：

```bash
grep -rn "date(2026" scripts/  # 预期只有 sync_st.py L91
grep -rn "20260101" scripts/   # 预期只有本阶段新增的引用
```

---

## 7. 验证方案

### 7.1 单元验证（pytest）

```bash
cd /Users/chenkaichen/stock-analyzer

# 运行所有新增测试
python -m pytest tests/test_constants.py tests/test_sync_stocks.py -v

# 运行全量测试（确保不破坏已有逻辑）
python -m pytest tests/ -v
```

### 7.2 语法验证

```bash
# Python 语法检查
python -m py_compile scripts/sync_stocks.py
python -m py_compile scripts/sync_trade_cal.py
python -m py_compile scripts/sync_st.py
python -m py_compile config/constants.py
```

### 7.3 集成验证（手动执行，建议 dry-run）

```bash
# 验证 changelog 写入不报错
python scripts/sync_stocks.py --dry-run

# 验证 trade_cal 过滤（先备份旧文件）
cp data/trade_cal.parquet data/trade_cal.parquet.bak
python scripts/sync_trade_cal.py
# 验证 trade_cal 行数大幅减少（~250 行 vs ~8000 行）
```

### 7.4 验收标准

| 检查项 | 预期 |
|--------|------|
| pytest 全量通过 | ✅ 所有测试通过 |
| `sync_stocks.py --dry-run` | ✅ 正常输出，无 SchemaError |
| `trade_cal.parquet` 重新拉取后行数 | ✅ < 260 行 |
| `stocks_changelog.parquet` 读入后 `detected_at` 的 dtype | ✅ `pl.Date` |
| `sync_st.py` 全量初始化正常 | ✅ 从 2026-01-01 开始拉取 |

---

## 8. 补充说明

### 8.1 为什么不封装 TRADE_CAL_START_DATE 的 parse 函数？

`date(int(s[:4]), int(s[4:6]), int(s[6:8]))` 出现了两次（`sync_trade_cal.py` + `sync_st.py`）。如果后续出现第三次使用，应考虑在 `tushare_utils.py` 或 `constants.py` 中加一个辅助函数：

```python
def parse_ymd(ymd: str) -> date:
    return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
```

当前仅重复两次，**不引入新函数**，保持最小改动。

### 8.2 测试文件为什么不测试 Tushare API 调用？

所有测试用例均**不依赖外部 API**。理由：
- Tushare API 需要 token，不适合 CI
- 测试速度慢（网络开销）
- API 行为由 Tushare 负责，项目只测试自身逻辑

---

*文档版本：v1.0 | 作者：高见远 | 日期：2026-07-07*
