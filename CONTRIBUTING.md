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

## 适用于 AI 协作者的额外规则

如果你的朋友用 AI 工具（WorkBuddy / Cursor / Copilot 等）参与开发，AI 必须遵守以下纪律：

### 1. 最小 diff 原则

```
禁止：
  - 顺手重构没关系的代码
  - 修改已有代码的格式
  - 删除看起来"没用"的 import（除非它是你刚引入的遗留）
  - 给现有功能"顺便加点优化"

允许：
  - 只修改与用户需求直接相关的行
  - 匹配现有代码风格，不要强制统一
```

### 2. 先读，再改

对一个文件做任何修改前，必须先读取它的完整内容。禁止盲改。

### 3. 改动后自检

每次 commit 前自查：

- [ ] 改动只涉及本次需求相关代码？
- [ ] 没有引入无关的格式变化？
- [ ] import 语句没有多余/遗漏？
- [ ] 代码能在本地跑通？

### 4. 数据文件永不提交

`data/` 和 `reports/` 目录已在 `.gitignore` 中排除。AI 不应尝试 `git add` 这些目录下的文件。数据通过脚本生成，不需要版本管理。

如果数据更新是本次改动的目的，提交 **获取数据的脚本**，而非数据本身。

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
