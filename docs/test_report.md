# 测试报告 — 毕达标

> **日期**: 2026-07-07  
> **执行人**: 毕达标（测试工程师）  
> **测试范围**: config/ + scripts/（核心模块）  
> **Python**: 3.13.12, pytest 9.1.1, pytest-cov 7.1.0, Polars

---

## 一、测试文件清单

| 文件 | 类型 | 状态 | Case 数 |
|------|------|:----:|:-------:|
| `tests/conftest.py` | 共享 fixture | 已存在 | — |
| `tests/test_constants.py` | 常量和 Schema 验证 | 已存在 | 4 |
| `tests/test_sync_stocks.py` | Changelog detected_at 类型 | ⬆️ 新增 3 个 case | 6 |
| `tests/test_sync_trade_cal.py` | 交易日历起始过滤 | 🆕 新建 | 4 |
| `tests/test_sync_st.py` | sync_st 使用新常量 | 🆕 新建 | 3 |
| `tests/test_trade_cal_utils.py` | get_nth_trading_day 等工具函数 | 🆕 新建 | 8 |
| `tests/test_indicators.py` | 指标计算（已有） | 已存在 | 20 |
| **合计** | | | **45** |

---

## 二、Case 通过状态

| 结果 | 数量 | 占比 |
|:---:|:----:|:---:|
| ✅ 通过 | **45** | **100%** |
| ❌ 失败 | 0 | 0% |
| ⚠️ 错误 | 0 | 0% |

全部 45 个测试 case 通过，无失败。

---

## 三、覆盖率报告

### 行覆盖率（Line Coverage）

| 模块 | 语句数 | 覆盖数 | 覆盖率 | 关键缺失行 |
|------|:-----:|:-----:|:-----:|:----------:|
| `config/constants.py` | 37 | 37 | **100%** | — |
| `config/__init__.py` | 6 | 6 | **100%** | — |
| `config/trade_cal_utils.py` | 41 | 37 | **90%** | 23(缓存), 53/59(异常), 76 |
| `scripts/indicators.py` | 137 | 112 | **82%** | 163-164(异常), 232-235, 307-308, 333-347(死代码) |
| `scripts/sync_st.py` | 90 | 16 | **18%** | API/CLI 代码 |
| `scripts/sync_stocks.py` | 199 | 54 | **27%** | API/CLI 代码 |
| `scripts/sync_trade_cal.py` | 49 | 0 | **0%** | 全 API 依赖 |
| **合计** | 1446 | 299 | **21%** | |

### 分支覆盖率（Branch Coverage）

| 模块 | 覆盖率 | 说明 |
|------|:-----:|:----:|
| `config/constants.py` | **100%** | ✅ 完美 |
| `config/__init__.py` | **100%** | ✅ 完美 |
| `config/trade_cal_utils.py` | **85%** | ✅ 良好 |
| `scripts/indicators.py` | **78%** | ⚠️ 部分异常分支未覆盖 |
| `scripts/sync_stocks.py` | **27%** | API/CLI 代码 |
| `scripts/sync_st.py` | **15%** | API/CLI 代码 |

> **说明**: 整体覆盖率偏低（21%）是因为 scope 覆盖全量脚本，而多数 sync_*.py 依赖 Tushare API 调用，非 mock 的纯单元测试无法覆盖。核心配置和工具函数模块覆盖率良好。

---

## 四、Phase 1 测试 Case 覆盖状态

### 需求 1：Changelog detected_at 类型（Date vs Datetime）

| # | 场景 | 预期 | 状态 | 测试函数 | 备注 |
|---|------|------|:----:|:--------:|:----:|
| TC1-1 | 首次运行（无旧文件） | changelog 写入成功，detected_at 为 pl.Date | ✅ | `test_append_changelog_first_run` | 验证无旧文件时写入成功，类型正确 |
| TC1-2 | 增量追加（旧文件为 Date 类型） | 连续运行两次不报错 | ✅ | `test_append_changelog_incremental_date` | 两批数据合并，类型保持 pl.Date |
| TC1-3 | 向后兼容（旧文件为 Datetime） | concat 成功，全文件为 pl.Date | ✅ | `test_append_changelog_backward_compat` | 寇豆码已写 |
| TC1-4 | 无变更时执行 | changelog 0 行，跳过不报错 | ✅ | `test_generate_changelog_no_change` | 寇豆码已写 |
| TC1-5 | dry-run 模式 | 不写文件，不触发类型问题 | ⚠️ | — | **未覆盖**：`dry_run()` 内部调用 `get_pro()` 做网络请求，已在报告中记录。在不 mock 网络层的情况下无法测试。 |
| TC1-6 | 同一天多次运行（幂等） | 每次正常写入，无 schema 冲突 | ✅ | `test_append_changelog_idempotent_same_day` | 连续 3 次写入，schema 始终为 pl.Date |

### 需求 2：交易日日历起始过滤

| # | 场景 | 预期 | 状态 | 测试函数 | 备注 |
|---|------|------|:----:|:--------:|:----:|
| TC2-1 | 正常全量运行 | 只写入 ≥2026-01-01 数据 | ✅ | `test_tc2_1_filter_start_date` + `test_tc2_1_real_parquet_filtering` | 模拟原始数据验证过滤行为；验证 real parquet 的 schema 兼容性 |
| TC2-2 | 已有旧文件重新运行 | 幂等覆盖 | ✅ | `test_tc2_2_idempotent_rerun` | 对已过滤数据二次过滤结果不变 |
| TC2-3 | 数据范围缩减 | 过滤后行数显著减少 | ✅ | `test_tc2_3_data_range_reduction` | 验证被过滤的行数 = start 之前的行数 |
| TC2-5 | sync_st.py 使用新常量 | 不再包含硬编码 | ✅ | `test_tc2_5_imports_trade_cal_start_date` + `test_tc2_5_sync_full_uses_constant` + `test_tc2_5_no_hardcoded_date_in_source` | 源码检查：引用 TRADE_CAL_START_DATE，无硬编码日期 |
| TC2-6 | get_nth_trading_day 使用过滤后数据 | 正常返回 | ✅ | `test_tc2_6_get_nth_trading_day_forward/multiple/backward/zero/on_trading_day/with_real_data` (6 个子 case) + `is_trading_day` 验证 | 全覆盖正/反/边界 |

### 汇总

| 需求 | 总 case | ✅ 已覆盖 | ⚠️ 未覆盖(可接受) | ❌ 必须补 |
|:----:|:------:|:---------:|:-----------------:|:---------:|
| 需求 1 | 6 | 5 | 1 (TC1-5) | 0 |
| 需求 2 | 5 | 5 | 0 | 0 |
| **合计** | **11** | **10** | **1** | **0** |

---

## 五、未覆盖 Case 详情

### TC1-5：dry-run 模式

- **原因**: `dry_run()` 函数内部调用 `get_pro()` 发起 Tushare API 网络请求，且涉及 `fetch_stock_basic_all` → `transform` → `apply_filter` → `validate` → `print_diff` 完整链路。不 mock 网络层的情况下无法执行。
- **建议**: 如果团队需要覆盖此 case，可行方案：
  1. 将 `get_pro()` 分离出去，使 `dry_run()` 接受可选的 pro 参数（类同 `sync_incremental`），这样测试中可以注入 mock；
  2. 或者将 dry_run 的纯逻辑部分（拉取后的对比打印）提取为独立函数。

---

## 六、发现的源代码问题

> **铁律提醒**: 以下仅为发现的 bug/问题，我未修改任何源代码。

### 1. test_indicators.py 中标注的 `_find_missing_dates` 死代码 bug

```python
# 在 test_indicators.py 中已标注：
# _find_missing_dates 中存在一个死代码 bug（l.152-157），
# 当文件存在时会触发 TypeError: unhashable type: 'Series'。
```

测试通过使用不存在的文件路径绕过此 bug。如需修复，需审视 `_find_missing_dates` 中该分支逻辑。

---

## 七、测试资产清单

测试文件均已写入磁盘，随源码版本管理：

| 路径 | 用途 |
|------|:----:|
| `tests/conftest.py` | 共享 fixture: `sample_stocks_old`, `sample_stocks_new` |
| `tests/test_constants.py` | 常量/Schema 格式验证（4 case） |
| `tests/test_sync_stocks.py` | Changelog detected_at 类型完整验证（6 case） |
| `tests/test_sync_trade_cal.py` | 交易日历过滤行为验证（4 case） |
| `tests/test_sync_st.py` | sync_st 使用 TRADE_CAL_START_DATE 验证（3 case） |
| `tests/test_trade_cal_utils.py` | get_nth_trading_day 等工具函数验证（8 case） |
| `tests/test_indicators.py` | 指标计算全链路验证（20 case） |

---

*报告完毕。毕达标签字：📋 45/45 通过，10/11 Phase 1 case 覆盖，1 case 因 API 依赖标记为可接受未覆盖。*
