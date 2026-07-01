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
├── data/                          # Parquet 数据文件（.gitignore）
│   ├── stocks.parquet             # 全市场股票列表（代码/名称/申万行业）
│   ├── kline_daily.parquet        # 日线数据（全量个股 × 半年）
│   └── indices.parquet            # 主要指数日线
├── scripts/
│   ├── fetch_data.py              # 数据采集（可通过 WorkBuddy 通达信 MCP 拉取）
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

## 快速开始

### 环境准备

```bash
# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 获取数据

数据通过通达信接口拉取，需要 WorkBuddy 环境支持（已集成通达信 MCP 连接器）：

```bash
# WorkBuddy 会自动调用 fetch_data.py 拉取全量数据
# 输出到 data/ 目录
```

也可以手动准备以下 Parquet 文件（详见 `scripts/fetch_data.py`）：

- `data/stocks.parquet` — 列：code, name, industry（申万行业分类）
- `data/kline_daily.parquet` — 列：code, trade_date, open, high, low, close, volume, amount
- `data/indices.parquet` — 列：code, trade_date, open, high, low, close, volume

### 使用示例

**示例 1：全市场涨跌分布**

```python
import polars as pl

df = pl.read_parquet("data/kline_daily.parquet")
today = df.filter(pl.col("trade_date") == df["trade_date"].max())

result = today.with_columns(
    pl.col("close").pct_change().over("code").alias("pct_chg")
).group_by(
    pl.col("pct_chg").cut(
        [-0.07, -0.05, -0.03, -0.01, 0.01, 0.03, 0.05, 0.07],
        labels=["跌停", ">5%", "3-5%", "1-3%", "±1%", "1-3%", "3-5%", ">5%", "涨停"]
    ).alias("区间")
).agg(pl.count().alias("个股数"))
```

**示例 2：筛选创 20 日新高且属于科技行业的个股**

```python
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
    .filter(pl.col("trade_date") == "2026-07-01")
)
```

**示例 3：一键生成市场温度计报告**

```bash
python main.py --report thermometer --output reports/today.html
```

输出一个自包含的 ECharts HTML 文件，包含：
- 全市场温度（Z-score 标准化）
- TOP20 成交额占比趋势
- 涨跌分布（涨/跌 >5%、±7%）
- 净新高数（20 日 / 60 日）
- 温度分布直方图 + 极端温度日记录

## 市场温度计指标说明

基于"趋势市场温度计 v15"的因子体系：

| 因子 | 含义 | 权重 |
|------|------|------|
| F1 | 涨跌比（上涨家数/(上涨+下跌)） | 17% |
| F2 | TOP20 成交额占比 | 21% |
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

## Roadmap

- [ ] 完整温度计因子计算
- [ ] 行业分赛道温度（科技 / 消费 / 制造等）
- [ ] 个股筛选器（K 线条件 + 行业 + 基本面）
- [ ] 回测框架（基于历史温度信号做择时）
- [ ] 自动日报（定时拉数据 → 生成报告 → 推送）

## License

MIT
