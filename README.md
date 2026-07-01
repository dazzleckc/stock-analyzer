# Stock Analyzer — A股量化分析工具

基于 **Python + Polars + Parquet** 的轻量级 A 股量化分析框架。零数据库、零服务依赖，数据即文件，分析即脚本。

## 设计哲学

```
数据拉取（通达信 → Parquet） → 量化分析（Polars） → 可视化报告（ECharts HTML）
      一次性的                      反复玩的                    最终产物
```

三条原则：

1. **数据存成 Parquet 文件**，不建数据库。5000 只 A 股 × 半年日线 ≈ 60 万行，压缩后 ~30MB，Polars 毫秒级读入。
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
├── data/                          # Parquet 数据文件（.gitignore）
│   ├── stocks.parquet             # 全市场股票列表（代码/名称/申万行业）
│   ├── kline_daily.parquet        # 日线数据（全量个股 × 半年）
│   └── indices.parquet            # 主要指数日线
├── scripts/
│   ├── fetch_kline.py             # 日K全量拉取（AKShare 东方财富）
│   ├── update_kline.py            # 日K增量更新
│   ├── indicators.py              # 技术指标计算（MA/RSI/波动率/新高新低等）
│   ├── screener.py                # 个股筛选器（基于 K 线条件 + 行业 + 基本面）
│   └── market_thermometer.py      # 市场温度计分析模块
├── templates/
│   └── thermometer.html.j2        # ECharts 报告模板（Jinja2）
├── reports/                       # 生成的 HTML 报告（.gitignore）
├── requirements.txt
└── main.py                        # 主入口：一键生成报告
```

## 技术栈

| 层 | 技术 | 用途 |
|---|------|------|
| 数据获取 | 通达信 MCP / tdx_kline | A 股日线、指数、行业分类 |
| 数据格式 | Apache Parquet | 列式存储，自带压缩和 schema |
| 数据处理 | Polars | 高性能 DataFrame 库，链式 API |
| 报告模板 | Jinja2 + ECharts | GitHub Dark 主题，自包含 HTML |
| 运行环境 | Python ≥ 3.10 | |

## 安装

### 前置条件

- **Python** ≥ 3.10（推荐 3.12+）
- **pip** ≥ 23.0

> 当前项目依赖 Polars，该库针对 Apple Silicon 和 Intel 芯片均提供预编译 wheel。Linux 用户需确保 glibc ≥ 2.28。

### 步骤

```bash
# 1. 克隆仓库
git clone <repo-url>
cd stock-analyzer

# 2. 创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate       # macOS / Linux
# 或
venv\Scripts\activate          # Windows (cmd)
# 或
venv\Scripts\Activate.ps1      # Windows (PowerShell)

# 3. 安装依赖
pip install -r requirements.txt

# 4. 验证安装
python -c "import polars; print(polars.__version__)"
```

### requirements.txt

```
polars>=1.0.0
jinja2>=3.0.0
```

> 依赖精简到最少。Polars 处理数据，Jinja2 渲染报告模板。无需数据库驱动、无需 Web 框架。

### 可选依赖

```bash
# 如需使用 Notebook 交互式探索
pip install jupyter matplotlib

# 如需在脚本中直接调用通达信接口
# 需要 WorkBuddy 环境并已连接通达信 MCP
```

## 使用指南

> 📅 **预置数据**：仓库附带 `data/` 下三份 Parquet 文件，覆盖 **2026-01-05 ~ 2026-07-01**（117 个交易日）。clone 即可用，无需跑全量初始化。日后更新执行 `python scripts/update_kline.py` 和 `python scripts/update_indices.py`。

### 数据准备

数据通过通达信接口拉取。两种方式：

**方式一：WorkBuddy 自动拉取（推荐）**

在 WorkBuddy 环境下，确保已连接通达信 MCP 连接器，运行：

```bash
python scripts/fetch_kline.py
```

脚本会调用 `tdx_kline` / `tdx_quotes` 等接口，自动拉取全市场日线数据并写入 `data/` 目录。

**方式二：手动准备 Parquet 文件**

按照以下 schema 自行准备数据：

| 文件 | 列 | 类型 |
|------|-----|------|
| `data/stocks.parquet` | code, name, industry | str, str, str |
| `data/kline_daily.parquet` | code, trade_date, open, high, low, close, volume, amount | str, date, f64, f64, f64, f64, i64, f64 |
| `data/indices.parquet` | code, trade_date, open, high, low, close, volume | str, date, f64, f64, f64, f64, i64 |

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

#### 示例 2：筛选创 20 日新高且属于科技行业的个股

```python
import polars as pl

stocks = pl.read_parquet("data/stocks.parquet")
kline = pl.read_parquet("data/kline_daily.parquet")

result = (
    kline
    .join(stocks, on="code")
    .filter(pl.col("industry").str.contains("电子|半导体|科创|计算机"))
    .with_columns(
        pl.col("high").rolling_max(window_size=20).over("code").alias("high_20d")
    )
    .filter(pl.col("high") == pl.col("high_20d"))
    .filter(pl.col("trade_date") == pl.col("trade_date").max())
    .select(["code", "name", "industry", "close", "high_20d"])
    .sort("close", descending=True)
)
```

#### 示例 3：一键生成市场温度计报告

```bash
python main.py --report thermometer --output reports/today.html
```

输出一个自包含的 ECharts HTML 文件，包含：

- 全市场温度（Z-score 标准化）
- TOP 20 成交额占比趋势
- 涨跌分布（涨/跌 >5%、±7%）
- 净新高数（20 日 / 60 日）
- 温度分布直方图 + 极端温度日记录

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
- **提交脚本，不提交数据**：`data/` 和 `reports/` 已在 `.gitignore` 排除。

**流程：** `dev` 切分支 → 开发 + 自检 → PR → `dev`，`main` 分支 Squash Merge。

**版本：** 遵循 SemVer，当前 **0.1.0-dev**。

## Roadmap

- [ ] 完整温度计因子计算
- [ ] 行业分赛道温度（科技 / 消费 / 制造等）
- [ ] 个股筛选器（K 线条件 + 行业 + 基本面）
- [ ] 回测框架（基于历史温度信号做择时）
- [ ] 自动日报（定时拉数据 → 生成报告 → 推送）

## License

MIT
