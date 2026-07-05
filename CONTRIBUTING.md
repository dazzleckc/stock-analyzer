# Contributing Guide — 协作规范

本文档约束所有开发者（含 AI）在本仓库的提交流程。规则简单但强制执行。

## 分支策略

采用简化版 GitFlow，只有三条分支：

```
main          ← 稳定版，只接受 PR 合入
  │
  ├── dev     ← 开发主分支，日常提交到这里
  │     │
  │     ├── feature/xxx   ← 功能分支，从 dev 切出，合回 dev
  │     ├── fix/xxx       ← 修复分支，从 dev 切出，合回 dev
  │     └── data/xxx      ← 数据更新分支，从 dev 切出，合回 dev
  │
  └── release/x.y.z       ← 发布分支，从 dev 切出，合回 main + dev
```

| 分支 | 用途 | 谁可以合入 |
|------|------|-----------|
| `main` | 稳定发布版本 | 仅通过 PR，需 1 人 Approve |
| `dev` | 日常开发汇总 | 直接推送或通过 PR |
| `feature/*` | 新功能开发 | 创建者自行合入 dev |
| `fix/*` | Bug 修复 | 创建者自行合入 dev |
| `data/*` | 数据更新（拉新日期数据） | 创建者自行合入 dev |
| `release/*` | 版本发布 | PR → main，1 人 Approve |

## Commit 规范

### 格式

```
<type>(<scope>): <简短描述>

<详细说明（可选）>
```

### Type 必须为以下之一

| type | 含义 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(indicator): 添加 RSI 指标计算` |
| `fix` | Bug 修复 | `fix(screener): 修复 20 日新高筛选日期偏移` |
| `refactor` | 重构（不改变功能） | `refactor(report): 提取 ECharts 配置到独立模块` |
| `docs` | 文档变更 | `docs: 更新 README 中的示例代码` |
| `style` | 代码格式（空格、分号等） | `style: 统一缩进为 4 空格` |
| `data` | 数据文件更新 | `data: 更新日线数据到 2026-07-15` |
| `chore` | 构建/依赖/工具 | `chore: 升级 Polars 到 1.x` |

### Scope（可选但推荐）

```
script, indicator, screener, report, template, data, docs
```

### 规则

- **一个 commit 只做一件事**。改指标和改报表分开提交。
- **禁止包含大文件**（>5MB）。数据文件走 Parquet，不提交到仓库（已在 `.gitignore` 中排除）。
- **禁止 force push 到 `main` 或 `dev`**。

## AI 协作规范

本项目假定所有贡献者都使用 AI 工具（WorkBuddy / Cursor / Copilot 等）辅助开发。这意味着 **产出速度不成瓶颈，审查能力才是**。

### 基本原则

1. **AI 是工具，不是替身。** 每段 AI 生成的代码，贡献者必须理解其行为并能向 Reviewer 解释。如果你自己都没弄明白一段代码为什么能跑——不要提交。
2. **绝不盲信。** AI 可能幻觉出不存在的方法名、API 参数，或写出一眼正确的错误逻辑。未经验证的代码禁止合入任何分支。
3. **先定接口，再写代码。** 让 AI 生成代码前，明确输入输出的类型和边界条件。模糊的 prompt 产生模糊的代码——随之而来的是漫长的 Review。
4. **安全是底线。** Token 等敏感信息必须通过 `config/local.py`（已 `.gitignore`）引入，禁止硬编码在任何脚本中。参照 `config/local.example.py` 模板。

### 提交规则（AI 侧）

这些规则面向 AI 的运行时行为，贡献者需要在 prompt 中明确约束：

#### 最小 diff 原则

```
禁止：
  - 顺手重构无关代码
  - 修改已有代码的格式
  - 删除看起来"没用"的 import（除非是你刚引入的遗留）
  - 给现有功能"顺便加点优化"

允许：
  - 只修改与需求直接相关的行
  - 匹配现有代码风格，不强制统一
```

#### 先读，再改

修改任何文件前，必须先读取完整内容。禁止在不知道上下文的情况下编辑。

#### 数据文件永不提交

`data/` 和 `reports/` 目录已在 `.gitignore` 中排除。提交的是**生成数据的脚本**，而非数据本身。clone 后运行 `sync_* --full` 拉取数据。

### 提交前自检清单（贡献者侧）

每个 commit 推送前，贡献者必须逐项确认：

- [ ] 代码在本地**实际运行过**，而非仅凭 AI 说"应该没问题"
- [ ] 没有引入未使用的 import、变量或函数
- [ ] 没有顺手重构与本次需求无关的代码
- [ ] 变量/函数命名与项目现有风格一致
- [ ] 数据文件没有被意外提交

### PR Review 审查要点

审查 AI 辅助的 PR 时，额外关注以下高风险区域：

| 检查项 | 为什么重要 | 检查方法 |
|--------|-----------|---------|
| 多余的 import / 死代码 | AI 容易留下未清理的依赖 | 检查 diff 中每个新增 import 是否被使用 |
| 风格一致性 | 不同人的 AI 可能产出不同风格 | 对比周边代码的命名和缩进 |
| 边界条件 | AI 倾向于写 happy path | 输入空值、极端值是否能正常运行 |
| API 幻觉 | AI 可能编造不存在的 Polars 方法 | 在新函数上跑一次，确认无 ImportError |
| diff 范围 | AI 容易"顺手优化" | 确认 diff 中每一行都能追溯到需求 |

### Prompt 规范建议

向 AI 描述任务时遵循以下结构，能显著提高产出质量：

```
背景：当前项目的技术栈、文件结构、相关上下文
目标：这一轮要达到的具体效果
输入/输出：参数类型、返回值格式
不需要做：明确排除的操作（错误处理、测试、文档等）
参考：已有的类似实现或代码片段
```

**反面示例：**
> "帮我加个筛选功能"

**正面示例：**
> "在 screener.py 中增加一个筛选条件：筛选今日成交额 > 1亿 且收盘价站上 20 日均线的个股。输入是 kline_daily.parquet，输出是筛选后的 DataFrame。不需要写错误处理和文档。"

## PR 流程

```
1. 从 dev 切出 feature/fix/data 分支
2. 开发 + 本地验证
3. 推送分支
4. 创建 PR → dev
5. 至少 1 人 Review + Approve（main 分支）
6. Squash Merge（推荐）
```

main 分支的 PR **必须 Squash Merge**，保持提交历史干净：

```
# PR 合并后，main 上只产生一个 commit
feat(indicator): 添加 RSI 和 MACD 指标计算模块
```

dev 分支可以走普通 Merge，保留开发历史。

## 版本号

遵循 SemVer：`主版本.次版本.修订号`

| 变更类型 | 版本号变化 |
|---------|-----------|
| 不兼容的 API 改动 | 主版本 +1 |
| 新增向后兼容的功能 | 次版本 +1 |
| 向后兼容的 Bug 修复 | 修订号 +1 |
| 文档/格式类改动 | 不改版本 |

当前版本：**0.1.0-dev**（开发阶段）

## 快捷参考

```bash
# 开始一个新功能
git checkout dev
git pull
git checkout -b feature/my-feature

# 开发完成后
git add <changed-files>
git commit -m "feat(scope): 做了什么"
git push origin feature/my-feature

# 创建 PR 到 dev，Review 后合并

# 发布
git checkout dev
git checkout -b release/0.1.0
# 最后调整 → PR → main
git checkout main && git merge release/0.1.0
git tag v0.1.0 && git push --tags
```
