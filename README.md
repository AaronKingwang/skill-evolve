# skill-evolve

> 让你的其他 skill 自我进化：**评估 → 有界改进 → 独立盲评 → 验证门控 → 人类确认 → 保留或回滚**。
>
> Author: **AaronKingwang**

把「训练模型」的那套方法搬到「训练 skill 文档」上：不改模型权重，而是把 `SKILL.md` 当成可优化的外部状态，用一个带验证门控的爬山循环，让它越用越好——而且每一步改动都要过独立评审、要你点头才落地。

---

## 它解决什么问题

你手里的 skill（`SKILL.md`）写完往往就不动了。但实际上：
- 它可能有质量短板（指令含糊、没写故障处理、缺安全约束）——你想系统地改好它；
- 你在**用某个 skill 干活时**经常顺手纠正它（「这次输出怎么没带链接」「这个接口又用错了」），这些教训本该沉淀回 skill，却往往丢失了。

skill-evolve 用**两种模式**分别对应这两件事，并保证「只会变好、不会变差」。

---

## 安装

skill-evolve 基于开放的 [Agent Skills](https://agentskills.io) 协议，可在任何 skills-compatible 的 AI agent runtime 中运行。

### 方式一：一句话安装（推荐，跨 runtime）

打开你正在用的 agent（Claude Code、Codex、Cursor、OpenClaw、Hermes、CodeBuddy、Workbuddy、Gemini CLI、kimi CLI、OpenCode 等），告诉它：

```
帮我安装这个 skill：https://github.com/AaronKingwang/skill-evolve
```

或者用通用 CLI 安装器 [`vercel-labs/skills`](https://github.com/vercel-labs/skills)（支持 55+ runtime）：

```bash
npx skills add AaronKingwang/skill-evolve
```

它会自动识别你当前的 runtime 并把 skill 放到正确目录。需要指定时加 `-a claude-code` / `-a codex` / `-a cursor` / `-a openclaw` 等参数。

### 方式二：手动安装

<details>
<summary>展开查看各 runtime 的 skills 目录</summary>

| Runtime | 安装路径 |
|---|---|
| Claude Code | `~/.claude/skills/skill-evolve/` |
| Codex CLI | `~/.codex/skills/skill-evolve/` |
| Cursor | `~/.cursor/skills/skill-evolve/` |
| OpenClaw | `~/.openclaw/workspace/skills/skill-evolve/` |
| Hermes Agent | 跑 `tools/install_hermes_skill.py` |
| 其他 runtime | clone 到对应 runtime 的 `skills/` 目录 |

</details>

```bash
git clone https://github.com/AaronKingwang/skill-evolve <上面对应的路径>
```

### 方式三：作为参考资料直接使用

把 `SKILL.md` 和 `references/` 的内容作为提示词喂给当前 agent，让它按流程执行即可。适合临时试用或不方便安装的场景。

---

## 两种用法

### 模式 A · 批量进化（给 skill 做一次体检 + 爬坡）
适合：想整体提升某个 skill 的质量。

直接说：
```
进化 lark-task 这个 skill
优化 xxx skill 的质量
评估一下 yyy skill 打个分
```
它会：定位目标 → 设计测试集（**让你确认**）→ 独立评审打基线分 → 找最弱维度 → 每轮做一处小改并重新评审 → **只保留真正变好的版本** → 触顶自动停 → **你确认后**写回。

### 模式 B · 会话沉淀（把刚才踩的坑变成 skill 的能力）★ 特色
适合：你刚用某个 skill 干完活，过程中纠正过它，想把经验固化下来。

直接说：
```
把刚才用 xxx 踩的坑沉淀进这个 skill
刚才那个错，改进一下对应的 skill
```
关键点：**你不需要自己总结「可泛化规则」**。你只管提具体问题、做具体纠正，skill-evolve 会**回看本次对话**，自动把「具体事件」抽象成「可复用规则」，并区分：
- 「输出要带可点击链接」→ 可泛化 → 进候选；
- 「这次用张三的 id」→ 一次性 → 丢弃。

抽象出的候选会**先列给你确认**，确认后同样走验证门控才并入。

---

## 查看进化历史

随时问 skill-evolve「**XX skill 的改进历史**」，它会读取账本展示给你——不只是总分，**每一维的分数变化**和**每次改了什么**都能看到：

```
看看 lark-task 的进化历史
lark-task 这几次优化都改了什么
```

底层有三个零依赖、纯文件读取的高性能命令：

| 命令 | 展示内容 |
|---|---|
| `evolve.py history <skill名>` | 跨所有进化轮次的汇总：每次的最优版本、总分、攻坚维度、改动摘要 |
| `evolve.py last <skill名>` | 最近一次进化的完整 9 维明细账本（v0→v1→… 每一维分数 + 每次 note） |
| `evolve.py show <run_dir>` | 指定某一次进化的完整 9 维明细 |

账本（`results.tsv`）记录了 9 个维度各自的分数、总分、keep/reject 状态、本轮攻坚维度和改动说明，所以历史可以精确追溯到「哪一维涨了、为什么改」。

---

## 安全设计（为什么它不会把你的 skill 改坏）

| 机制 | 作用 |
|---|---|
| **编辑者 ≠ 评审者** | 改 skill 的 agent 和打分的 agent 是不同实例，评审为盲评，杜绝「自己改自己打分」 |
| **验证门控** | 新版本必须分数**严格高于**当前最优才被采纳，否则自动回滚 |
| **有界编辑** | 每轮最多改 3 处，不会一次重写整篇 |
| **目标目录零污染** | 所有版本快照、账本都放在 skill-evolve 独立工作目录，默认位于 skills 目录同级的 `skill-evolve-runs/` 下（如 Claude Code 里为 `~/.claude/skill-evolve-runs/`），**不往你的 skill 目录塞 .git 或 .bak** |
| **人在环检查点** | 设计测试集、采纳改动、写回前都会停下等你确认 |
| **负反馈缓冲** | 被否决的改动记进 `rejected.md`，不会反复尝试同一个失败方向 |

回滚很安全：最优版本始终以快照留存，「回滚」只是不采用新版本，不需要任何 `reset`。

---

## 借鉴的论文与项目思想

这个 skill 是三种「自进化」思想的融合：

1. **autoresearch**（karpathy）— 提供「爬山循环 + 评测隔离 + 外部账本」的骨架：
   编辑 → 固定预算评估 → 比指标 → keep/discard；评测函数与被改对象隔离以防作弊；用外部 `results.tsv` 记录每次实验。本 skill 把它从「改训练代码、比 val_bpb」翻译成「改 SKILL.md、比 rubric 分」。

2. **SkillOpt: Executive Strategy for Self-Evolving Agent Skills**（arXiv:2605.23904，Microsoft 等）— 提供 **validation-gated（验证门控）** 范式：
   把 skill 当作冻结模型的「可训练外部状态」，每次编辑是**有界**的（文本版 learning rate），必须在**留出验证集**上**严格改善**才被接受，被拒编辑进入**负反馈缓冲**。本 skill 的门控、有界编辑、`rejected.md` 都来自这里。

3. **SkillLens: A Systematic Study of Model-Generated Agent Skills**（arXiv:2605.23899，Microsoft）— 提供**适应度函数**：
   关键实证——LLM 凭直觉自评 skill 准确率仅 **46.4%**（≈随机），引入三个**元技能维度**后升到 **73.8%**。本 skill 的 9 维评分细则（[references/rubric.md](references/rubric.md)）即源于此，其中三个★维度（**故障机制编码 / 可执行具体性 / 高危操作黑名单**）是评审核查重点。

> 注：本 skill 为从零独立实现，未依赖任何现成的 skill 优化工具。

---

## 目录结构

```
skill-evolve/
├── SKILL.md                      主工作流（agent 读取执行）
├── README.md                     本文件（给人看）
├── references/
│   ├── rubric.md                 SkillLens 9 维评分细则（100 分）
│   └── judge-protocol.md         独立 judge 盲评协议 + JSON 输出格式
├── scripts/
│   └── evolve.py                 版本/账本助手（纯 stdlib，零依赖）
└── templates/
    └── test-prompts.template.json  测试 prompt 模板
```

工作目录（运行时生成，与 skill 本体分离）：
```
<skill-evolve工作目录>/<skill>-<时间戳>/
├── v0.md, v1.md, ...   各版本快照（v0 = 基线）
├── results.tsv         账本：每次评估一行（含分数、keep/reject）
├── rejected.md         被否决编辑的负反馈缓冲
├── pending.md          提炼出但暂未并入的候选（跨 session 兜底）
└── meta.json           目标路径等元信息
```

`<skill-evolve工作目录>` 默认与 skills 目录同级，如 Claude Code 下为 `~/.claude/skill-evolve-runs/`。

---

## 环境要求

- 一个可用的 Python（仅用标准库，无需安装任何依赖）。
- Windows 提示：若命令行里的 `python` 无输出（多为 Microsoft Store 占位 stub），改用 `uv run --no-project python`。SKILL.md 已内置该回退判断。

---

## 一句话哲学

> **Train your skills like you train your models.**
> 不靠灵感一次写好，而是让 skill 在「评估—改进—验证—保留」的循环里，被数据和门控逼着变好。

---

*© Author: **AaronKingwang** · skill-evolve v1.0.0*
