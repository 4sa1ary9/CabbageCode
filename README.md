# CabbageCode

终端里的 AI 编程助手（Coding Agent），用 Python 实现，支持 Anthropic 协议和 OpenAI 兼容接口（含 DeepSeek）。

## 能力

- **Agent 循环**：ReAct 与计划模式；子 Agent、长期协作的 Agent 团队、Git worktree 隔离。
- **工具**：文件读写与编辑、Bash（Windows 上走 Git Bash）、Glob、Grep、MCP、Skill，工具定义按需加载。
- **上下文管理**：三层。单批工具结果超预算时溢写到磁盘；上下文过半时把旧工具结果换成占位符，攒够一批才清，减少打断 Prompt Cache；接近窗口上限时全量摘要。另有跨会话记忆。
- **编辑**：精确匹配失败时逐级放宽（行尾空白、首尾空白与缩进、Unicode 标点），唯一命中才写；失败时返回文件里最相似的几行；改 `.py` 后检查语法，把原本合法的文件改坏就撤销。
- **安全**：五层权限拦截；无交互模式下需审批的调用默认拒绝；远程模式默认只听本机并要求访问令牌；Linux 与 macOS 上可用系统沙箱。
- **检查点**：影子 git 仓库给每轮结束时的整个工作树拍快照，`/rewind` 连 Bash 造成的改动也能回退，不碰项目自己的 `.git`。
- **评测**：内置任务级评测，按 Aider polyglot 的两次尝试协议跑，统计通过率、Token、缓存命中率、成本、编辑失败率。

## 快速开始

```powershell
uv sync --dev
```

在 `~/.cabbagecode/config.yaml` 配置模型服务商，例如 DeepSeek：

```yaml
providers:
  - name: deepseek
    protocol: openai-compat
    base_url: https://api.deepseek.com
    api_key: "<你的 key>"
    model: deepseek-flash
    context_window: 128000
    max_output_tokens: 32768
```

```powershell
uv run cabbagecode                      # 交互界面
uv run cabbagecode -p "解释这个仓库的结构" --output-format stream-json --max-turns 20
```

`-p` 是无交互模式：输出 NDJSON 事件流，撞到 `--max-turns` 或出错时退出码为 1。需要放开写文件或执行命令时，显式加 `--mode acceptEdits` 或 `--mode bypassPermissions`。

## 评测

```powershell
git clone --depth 1 https://github.com/Aider-AI/polyglot-benchmark .bench/polyglot-benchmark
uv run python -m cabbagecode.evals --suite polyglot `
  --tasks .bench/polyglot-benchmark/python/exercises/practice `
  --agent-args "--mode bypassPermissions --max-turns 30" `
  --save-dir runs/ --out runs/results.jsonl --price "input=<输入单价>,output=<输出单价>,cache_read=<缓存命中单价>"
```

每题复制到临时目录，跑题时藏起测试文件；第 1 次失败就把测试输出交给第 2 次。多轮结果用 `uv run python -m cabbagecode.evals --aggregate runs/a.jsonl runs/b.jsonl runs/c.jsonl` 汇总，输出平均通过率、pass^k、不稳定题和成本。`--price` 的单位是美元 / 百万 Token，按所用模型的实际价格填写。

做对照实验时用环境变量关掉单项能力：

| 环境变量 | 作用 |
| --- | --- |
| `CABBAGECODE_EDIT_TOLERANCE=0` | 编辑只做精确匹配 |
| `CABBAGECODE_EDIT_GUARD=0` | 关闭编辑后的语法检查 |
| `CABBAGECODE_CONTEXT_MASKING=0` | 关闭旧工具结果清理 |

## 评测结果（2026-10-20）

基准是 Aider polyglot 的 Python 34 题：每题两次尝试，跑题时藏起测试，第 1 次失败才把测试输出交给第 2 次。模型是 `deepseek-flash`（DeepSeek-V4.1-Flash，默认思考模式），每次尝试最多 30 轮，4 路并发，独立跑三轮。

| 指标 | 三轮平均 | 每轮 |
| --- | --- | --- |
| 两次内通过（pass@2） | 88.2% | 85.3% / 94.1% / 85.3% |
| 第 1 次通过（pass@1） | 51.0% | 47.1% / 47.1% / 58.8% |
| 三轮都通过（pass^3） | 79.4%（27/34） | |
| 任一轮通过 | 97.1%（33/34） | |
| 平均轮数 / 耗时 | 10.2 轮 / 72 秒每题 | |
| Prompt 缓存命中率 | 95.9% | |
| 成本（按 DeepSeek 高峰价） | 0.015 美元每题 | |
| 审计标记的尝试 | 0 | 0 / 0 / 0 |

单轮通过率在 85% 到 94% 之间波动，所以报三轮平均和 pass^3，不报单轮最好成绩。时过时不过的有 6 题：dot-dsl、forth、go-counting、grade-school、paasio、react；三轮都没做出来的是 tree-building。

三轮共 226 次编辑调用，唯一一次失败是没先读文件就编辑。容错匹配和编辑后语法检查在这批题上没有被触发，它们的作用要在更大、更乱的代码库上才测得出来。

**测试被藏起来时，Agent 会去找答案。** 最早一轮没有禁止联网和外部搜索，34 题里有 7 题的 Agent 去下载官方测试和标准用例，或在整个磁盘上搜索题库。原始通过率 94.1%，把这些题算作失败后只有 76.5%。之后在提示词里禁止联网和外部搜索，并由评测脚本审计每次尝试的工具调用（联网命令、题库关键词、全盘搜索），命中即记为可疑、不计通过；上表的三轮都没有尝试被标记。

## 开发

```powershell
uv run pytest -q
```
