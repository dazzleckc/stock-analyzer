# Stock Analyzer — A股量化分析工具

基于 **Python + Polars + Parquet** 的轻量级 A 股量化分析框架。零数据库、零服务依赖，数据即文件，分析即脚本。

## 设计哲学

```
数据拉取（Tushare Pro → Parquet） → 量化分析（Polars） → 可视化报告（ECharts HTML）
      一次性的                      反复玩的                    最终产物
```

三条原则：

1. **数据存成 Parquet 文件**，不建数据库。5,542 只 A 股 × 119 个交易日 ≈ 65 万行，压缩后 ~20MB，Polars 毫秒级读入。
2. **分析用 Polars 链式调用**。窗口函数、滚动计算、分组聚合、多表 JOIN 都是一行链式调用，表达能力等价于 SQL 但更符合分析思维。
3. **报告用 ECharts 输出 HTML**。自包含的单文件，用浏览器打开即可查看，方便分享。

## 为什么不用数据库？

| 对比维度 | MySQL/PostgreSQL | Parquet + Polars |
|---------|-----------------|-----------------|
| 安装运维 | 需要安装、配置、备份 | 一个 pip install |
| 数据存储 | 一张表存一个数据库 | 一个文件就是一张表 |
| 查询性能 | 毫秒级（有索引） | 毫秒级（列式读取 + 延迟执行 + 查询下推） |
| 分析能力 | 需要子查询/CTE | `.rolling_max().over("code")` 一行搞定 |
| 数据迁移 | dump/restore | 复制粘贴文件 |
| 内存占用 | 服务常驻 1~2GB | 用完即走 |

**结论**：分析场景（百万行级）下，Parquet + Polars 比关系型数据库更合适，也更快。

## 项目结构

```
stock-analyzer/
├── README.md
├── CONTRIBUTING.md
├── config/
│   ├── __init__.py                 # 公共配置模块
│   ├── local.example.py            # 本地配置模板（可提交）
│   └── local.py                    # 敏感配置（已 .gitignore）
├── requirements.txt
├── data/                           # Parquet 数据文件
│   ├── stocks.parquet              # 全市场股票列表（code, name, list_status, delist_date）
│   ├── stocks_changelog.parquet    # 股票列表变更日志
│   ├── kline_daily.parquet         # 日线数据
│   ├── indices.parquet             # 主要指数日线（7 指数 + 全市场）
│   ├── st_stock.parquet            # ST 风险警示板数据
│   ├── delist_period.parquet       # 退市整理期记录
│   ├── trade_cal.parquet           # 交易日历（SSE 交易所，1990~2026 年）
│   └── indicators.parquet          # 市场日频指标（TOP20占比/涨跌幅≥7%/净新高）
├── scripts/
│   ├── sync_runner.py              # 统一入口，自动处理依赖顺序
│   ├── sync_trade_cal.py           # 交易日历同步（Tushare trade_cal）
│   ├── sync_stocks.py              # 股票列表同步（Tushare stock_basic）
│   ├── sync_kline.py               # 日K数据同步（Tushare pro_bar）
│   ├── sync_indices.py             # 指数数据同步（Tushare pro_bar）
│   ├── sync_st.py                  # ST 状态数据同步（Tushare stock_st）
│   ├── sync_delist.py              # 退市整理期数据同步（Tushare st）
│   ├── indicators.py               # 市场指标计算（TOP20成交额占比/极值/净新高）
│   ├── screener.py                 # 个股筛选器（基于 K 线条件 + 行业 + 基本面）
│   └── market_thermometer.py       # 市场温度计分析模块
├── templates/
│   └── thermometer.html.j2         # ECharts 报告模板（Jinja2）
├── reports/                        # 生成的 HTML 报告（.gitignore）
└── main.py                         # 主入口：一键生成报告
```

## 技术栈

| 层 | 技术 | 用途 |
|---|------|------|
| 数据获取 | Tushare Pro (pro_bar / stock_basic / stock_st / st) | A 股日线、指数、股票列表、ST 风险警示 |
| 交叉验证 | 通达信 MCP / tdx_kline | 数据质量交叉比对 |
| 数据格式 | Apache Parquet | 列式存储，自带压缩和 schema |
| 数据处理 | Polars | 高性能 DataFrame 库，链式 API |
| 报告模板 | Jinja2 + ECharts | GitHub Dark 主题，自包含 HTML |
| 运行环境 | Python ≥ 3.10 | |

## 数据源

### 主数据源：Tushare Pro（需 Token）

| 接口 | 用途 | 对应脚本 |
|------|------|---------|
| `pro_bar` (asset='E') | 个股前复权日线（OHLCV + 换手率 + 量比） | `sync_kline.py` |
| `pro_bar` (asset='I') | 指数日线（7 只主要指数 + 全市场汇总） | `sync_indices.py` |
| `stock_basic` | 全市场股票列表 + 退市日期过滤 | `sync_stocks.py` |
| `stock_st` | ST 风险警示板每日标记 | `sync_st.py` |
| `st` | 风险警示生命周期事件（含退市整理期） | `sync_delist.py` |
| `trade_cal` | 交易日历（SSE 交易所） | `sync_trade_cal.py` |

Tushare Token 需要 ≥ 120 积分（日线接口权限），6000 积分可获得 ST 数据。

### 交叉验证：通达信

通达信 MCP 通过 WorkBuddy 连接，用于验证 Tushare 数据的准确性。经全量 119 日比对，OHLC 偏差 ≤ 0.03 元（< 0.3%），成交量/成交额偏差 < 0.001%。

## 安装

### 前置条件

- **Python** ≥ 3.10
- **Tushare Pro Token**（注册地址：https://tushare.pro）

### 步骤

```bash
# 1. 克隆仓库
git clone <repo-url>
cd stock-analyzer

# 2. 创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate       # macOS / Linux
# 或
venv\Scripts\activate          # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 Tushare Token
cp config/local.example.py config/local.py
# 编辑 config/local.py，填入你的 Tushare Token

# 5. 验证安装
python -c "import polars, tushare; print('OK')"
```

### requirements.txt

```
polars>=1.0.0
tushare>=1.4.0
pandas>=2.0.0
tqdm>=4.60.0
```

## 使用指南

> 📅 **数据获取**：clone 后运行 `sync_* --full` 拉取全量数据。历史数据覆盖 2026-01-05 起的所有交易日。

### 数据初始化（首次使用或重建）

```bash
# 一键全量初始化（自动处理依赖顺序）
python scripts/sync_runner.py --full
```

等价于按依赖顺序依次执行 6 个 `sync_*.py`：
1. `sync_trade_cal.py --full` — 交易日历
2. `sync_stocks.py --full` — 股票列表 + 变更日志
3. `sync_st.py --full` — ST 风险警示数据
4. `sync_indices.py --full` — 指数日线
5. `sync_kline.py --full` — 个股日K（依赖步骤2）
6. `sync_delist.py --full` — 退市整理期（依赖步骤3）

任一脚本失败时，依赖它的下游脚本自动跳过，最后打印汇总表。

### 日常更新

```bash
# 一键增量更新（默认今天）
python scripts/sync_runner.py

# 补拉指定日期
python scripts/sync_runner.py --date 20260701
```

自动按依赖顺序执行 6 个 `sync_*.py` 的增量模式。

### 统一入口说明

`sync_runner.py` 封装了数据同步的完整流程：

- **自动依赖排序**：先 Layer 1（trade_cal / stocks / ST / indices），再 Layer 2（kline / delist）
- **失败隔离**：Layer 1 某脚本失败 → 对应 Layer 2 跳过，其他不受影响
- **汇总报告**：每个脚本的状态、耗时和结果一目了然
- **退出码**：全部成功返回 0，任一失败返回 1，可在 CI / cron 中使用

### 交易日历同步

```bash
# 全量拉取交易日历（首次运行，独立无依赖）
python scripts/sync_trade_cal.py --full

# 同效果（默认即全量）
python scripts/sync_trade_cal.py
```

交易日历由 `sync_runner.py` 自动排在拓扑第 0 位执行，无需单独运行。

### 市场指标计算

```bash
# 全量计算所有指标（首次运行）
python scripts/indicators.py --full

# 增量更新（每日运行，只补缺失日）
python scripts/indicators.py
```

输出写入 `data/indicators.parquet`，共 6 列 5 指标，用于温度计因子计算。

### 使用示例

#### 示例 1：全市场涨跌分布

```python
import polars as pl

df = pl.read_parquet("data/kline_daily.parquet")
today = df.filter(pl.col("trade_date") == df["trade_date"].max())

result = (
    today
    .with_columns(
        pl.col("close").pct_change().over("code").alias("pct_chg")
    )
    .group_by(
        pl.col("pct_chg").cut(
            [-0.07, -0.05, -0.03, -0.01, 0.01, 0.03, 0.05, 0.07],
            labels=["跌停", ">5%", "3-5%", "1-3%", "±1%", "1-3%", "3-5%", ">5%", "涨停"]
        ).alias("区间")
    )
    .agg(pl.count().alias("个股数"))
)
```

#### 示例 2：筛选创 20 日新高的个股

```python
import polars as pl

kline = pl.read_parquet("data/kline_daily.parquet")

result = (
    kline
    .with_columns(
        pl.col("high").rolling_max(window_size=20).over("code").alias("high_20d")
    )
    .filter(pl.col("high") == pl.col("high_20d"))
    .filter(pl.col("trade_date") == pl.col("trade_date").max())
    .select(["code", "close", "high_20d"])
    .sort("close", descending=True)
)
```

#### 示例 3：过滤 ST 股和退市整理期股票

```python
import polars as pl

kline = pl.read_parquet("data/kline_daily.parquet")
st_stock = pl.read_parquet("data/st_stock.parquet")
delist = pl.read_parquet("data/delist_period.parquet")

# 过滤 ST 股
st_codes = st_stock["code"].unique()
clean = kline.filter(~pl.col("code").is_in(st_codes))

# 过滤退市整理期（从 imp_date 起排除）
# ...
```

## 数据字段说明

### stocks.parquet — 全市场股票列表

| 字段 | 类型 | 说明 |
|------|------|------|
| code | `Utf8` | 6 位股票代码 |
| name | `Utf8` | 股票名称 |
| list_status | `Utf8` | 上市状态：`L`=上市, `D`=退市, `P`=暂停上市 |
| delist_date | `Utf8` | 退市日期（上市中为 `null`） |

> **数据源**: Tushare `stock_basic`（L+D+P），过滤 D 类中 delist_date ≤ 2026-01-05 的记录。

### stocks_changelog.parquet — 股票变更日志

| 字段 | 类型 | 说明 |
|------|------|------|
| code | `Utf8` | 6 位股票代码 |
| field | `Utf8` | 变更字段：`name`/`list_status`/`delist_date`/`_new_`（新增）/`_removed_`（移除）|
| old_value | `Utf8` | 变更前值（可为 null）|
| new_value | `Utf8` | 变更后值（可为 null）|
| detected_at | `Date` | 检测日期 |

### kline_daily.parquet — 个股日K线

| 字段 | 类型 | 单位 | 说明 |
|------|------|------|------|
| code | `Utf8` | — | 6 位股票代码 |
| trade_date | `Date` | — | 交易日 |
| open | `Float64` | 元 | 开盘价（前复权） |
| high | `Float64` | 元 | 最高价（前复权） |
| low | `Float64` | 元 | 最低价（前复权） |
| close | `Float64` | 元 | 收盘价（前复权） |
| volume | `Int64` | 手 | 成交量 |
| amount | `Float64` | 千元 | 成交额 |
| amplitude | `Float64` | % | 振幅 = (high - low) / pre_close × 100 |
| pct_change | `Float64` | % | 涨跌幅 |
| turnover_rate | `Float64` | % | 换手率 |

> **数据源**: Tushare `pro_bar(asset='E')`，起始日期 2026-01-05。
> **非空约束**: `code`、`trade_date`、`close` 不允许为空。

### indices.parquet — 指数日线

| 字段 | 类型 | 单位 | 说明 |
|------|------|------|------|
| code | `Utf8` | — | 指数代码（含 `999999` 全市场汇总） |
| trade_date | `Date` | — | 交易日 |
| open | `Float64` | 点 | 开盘点位 |
| high | `Float64` | 点 | 最高点位 |
| low | `Float64` | 点 | 最低点位 |
| close | `Float64` | 点 | 收盘点位 |
| volume | `Int64` | 手 | 成交量 |
| amount | `Float64` | 千元 | 成交额 |
| amplitude | `Float64` | % | 振幅 |
| pct_change | `Float64` | % | 涨跌幅 |
| turnover_rate | `Float64` | % | 换手率 |

跟踪的 6 只指数：

| 指数代码 | 名称 |
|---------|------|
| `000001.SH` | 上证指数 |
| `399001.SZ` | 深证成指 |
| `399006.SZ` | 创业板指 |
| `000688.SH` | 科创50 |
| `000300.SH` | 沪深300 |
| `899050.BJ` | 北证50 |

附加 `code=999999` 的全市场汇总行（字段含义同指数日线）。

> **数据源**: Tushare `pro_bar(asset='I')`，起始日期 2026-01-05。

### st_stock.parquet — ST 风险警示数据

| 字段 | 类型 | 说明 |
|------|------|------|
| code | `Utf8` | 6 位股票代码 |
| name | `Utf8` | 股票名称 |
| exchange | `Utf8` | 交易所代码 |
| trade_date | `Date` | 交易日 |
| type | `Utf8` | ST 类型编码 |
| type_name | `Utf8` | ST 类型说明（如 ST、*ST）|

> **数据源**: Tushare `stock_st`，覆盖 2026-01-01 至今。

### delist_period.parquet — 退市整理期记录

| 字段 | 类型 | 说明 |
|------|------|------|
| code | `Utf8` | 6 位股票代码 |
| name | `Utf8` | 股票名称 |
| imp_date | `Date` | 整理期起始日期 |

> **数据源**: Tushare `st`（筛选 `st_type="退市整理期"`），两阶段获取：先按时间窗口扫描，再逐只个股补全。

### trade_cal.parquet — 交易日历

| 字段 | 类型 | 说明 |
|------|------|------|
| exchange | `Utf8` | 交易所代码（SSE） |
| cal_date | `Date` | 日历日期 |
| is_open | `Int8` | 是否交易日：1=交易日，0=休市 |
| pretrade_date | `Date` | 前一个交易日 |

> **数据源**: Tushare `trade_cal(exchange="SSE")`，保留 `TRADE_CAL_START_DATE`（2026-01-01）之后的日历记录。

### indicators.parquet — 市场日频指标

| 字段 | 类型 | 说明 |
|------|------|------|
| trade_date | `Date` | 交易日 |
| top20_amount_ratio | `Float64` | TOP20 成交额占比（%）|
| up_ge7_count | `Int64` | 涨跌幅 ≥ 7% 的股票数 |
| down_le7_count | `Int64` | 涨跌幅 ≤ -7% 的股票数 |
| net_high_20d | `Int64` | 20 日净新高数（新高 - 新低）|
| net_high_60d | `Int64` | 60 日净新高数（新高 - 新低）|

> **数据源**: 基于 `kline_daily.parquet` + `st_stock.parquet` + `delist_period.parquet` 计算，排除当日 ST 及退市整理期股票。全量计算通过 `scripts/indicators.py --full`，增量更新通过 `scripts/indicators.py`。

## 市场温度计指标说明

基于"趋势市场温度计 v15"的因子体系：

| 因子 | 含义 | 权重 |
|------|------|------|
| F1 | 涨跌比（上涨家数 / (上涨 + 下跌)） | 17% |
| F2 | TOP 20 成交额占比 | 21% |
| F3 | 涨幅 >5% 个股占比 | 14% |
| F5 | 涨跌停比 | 8% |
| F6 | 连板效应 | 14% |
| F7 | 新高新低比 | 11% |
| F11 | 中期趋势（均线偏离度） | 14% |

温度区间定义（0-100）：

| 区间 | 含义 | 建议 |
|------|------|------|
| < 25 | 冰点 | 极端恐慌，可能反转 |
| 25-35 | 偏冷 | 低迷，注意止跌信号 |
| 35-50 | 中性 | 正常波动 |
| 50-65 | 偏温 | 温和上行 |
| 65-80 | 过热 | 注意风险 |
| > 80 | 沸点 | 极端亢奋，历史高位区 |

## 贡献指南

欢迎贡献！本项目的协作建立在 AI 辅助编码之上——**代码产出不是瓶颈，审查能力才是。** 提交前请务必通读 [CONTRIBUTING.md](./CONTRIBUTING.md)，以下为速览。

**核心纪律：**

- **AI 是工具，不是替身**：每段代码你必须理解并能解释。
- **绝不盲信**：AI 会幻觉 API、写出错误逻辑，未经本地验证不得提交。
- **最小 diff**：只改需求相关的代码，不顺手重构。
- **提交脚本，不提交数据**：`data/raw/` 和 `reports/` 已在 `.gitignore` 排除。clone 后运行 `sync_* --full` 拉取数据。

**流程：** `dev` 切分支 → 开发 + 自检 → PR → `dev`，`main` 分支 Squash Merge。

**本地配置：** 首次 clone 后执行 `cp config/local.example.py config/local.py` 并填入 Tushare Token。

**版本：** 遵循 SemVer，当前 **0.1.0-dev**。

## Roadmap

- [x] 全市场日线数据采集（Tushare pro_bar）
- [x] 指数 + 全市场汇总
- [x] ST 风险警示板数据
- [x] 退市整理期数据
- [x] 交易日历数据接入
- [x] 技术指标计算管线（TOP20成交额占比 / 涨跌幅≥7% / 净新高）
- [ ] 完整温度计因子计算
- [ ] 行业分赛道温度（科技 / 消费 / 制造等）
- [ ] 个股筛选器（K 线条件 + 行业 + 基本面）
- [ ] 回测框架（基于历史温度信号做择时）
- [ ] 自动日报（定时拉数据 → 生成报告 → 推送）

## License

MIT
