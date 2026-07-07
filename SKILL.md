---
name: skill-evolve
version: 1.0.0
description: "让其他 skill 自我进化：评估 → 改进 → 实测验证 → 人类确认 → 保留或回滚。进化有两种模式——『进化/优化某个 skill』走批量爬山（模式A）；『把刚才用某 skill 踩的坑/纠正沉淀进去』走会话提炼（模式B）。另支持只读的历史查询。当用户提到 进化skill、优化skill质量、让skill变好、给skill打分评估、把这次的经验沉淀进skill、skill爬山、查看skill改进历史、skill优化记录、skill评分变化、这个skill都改了什么 时使用。"
metadata:
  author: MoonCat
  requires:
    bins: ["python"]
---

# skill-evolve — 帮助其他 skill 进化

> 核心理念：**评估 → 有界改进 → 独立盲评 → 验证门控 → 人类确认 → 保留或回滚**。
> 思想来自 autoresearch（固定预算爬山 + 评测隔离 + 外部账本）、SkillOpt（验证门控、有界编辑、负反馈缓冲）、SkillLens（9 维 rubric + 三个元技能维度）。

## 0. 两条铁律（任何时候都不能破）

1. **评测与编辑隔离**：提出编辑的 agent 与给分的 judge **必须是不同的子 agent**，judge 盲评（见 [judge-protocol.md](references/judge-protocol.md)）。绝不「自己改自己打分」。
2. **目标 skill 目录零污染**：所有进化状态（快照、账本、缓冲）都放在 skill-evolve 的独立工作目录（见下表），**不往目标 skill 目录塞 .git 或 .bak**。版本与回滚全靠 [scripts/evolve.py](scripts/evolve.py)。

工具：`python <skill-evolve所在目录>/scripts/evolve.py <子命令>`（纯 stdlib，零依赖）。
`<skill-evolve所在目录>` 取决于你当前 agent runtime 的 skills 目录，常见对应关系如下：

| Runtime | skills 目录 |
|---|---|
| Claude Code | `~/.claude/skills/` |
| Codex CLI | `~/.codex/skills/` |
| Cursor | `~/.cursor/skills/` |
| OpenClaw | `~/.openclaw/workspace/skills/` |
| 其他 skills-compatible runtime | 对应 runtime 的 `skills/` 目录 |

工作目录（快照、账本）默认与 skills 目录同级，如 Claude Code 下为 `~/.claude/skill-evolve-runs/`。

子命令：`init / snapshot / ledger9 / diff / best / writeback / show / history / last`，详见脚本头部 docstring。

> **进化对象是整个 skill 目录，不是单个 SKILL.md**：一个版本（`v0/`、`v1/`…）是 skill 目录的完整快照（含 references/scripts 等被引用文件）。editor 可改 SKILL.md，也可改/增被引用文件；judge 读整个版本目录，能真实核实资源。

> **记账用 `ledger9`**：judge 按 [judge-protocol.md](references/judge-protocol.md) 返回 9 维明细 JSON 后，把 9 个维度分（dim1~dim9）+ total + status + 攻坚维度 + 改动 note 一起写进账本。这样历史可追溯到**每一维的分数变化**和**每次改了什么**，不只是总分。

> **解释器解析**：先试 `python`。若该机器上 `python` 无输出（Windows 常见——PATH 命中的是 Microsoft Store 占位 stub），改用 `uv run --no-project python`。开工前用 `python -c "print(1)"` 探一下，选定能用的那个，后续统一使用。

---

## 1. 入口路由（开场第一步）

先判断是「查看历史」还是「进化」，再在进化里分两种模式：

| 信号                                                       | 走                                                   |
| -------------------------------------------------------- | --------------------------------------------------- |
| 「看看 X 的进化/改进历史」「X 这几次优化都改了什么」「X 的优化记录/评分变化」              | **历史查询**（只读，见下方第 1.1 节，不是进化）                        |
| 「把刚才/这次用 X 踩的坑沉淀进去」「X 老犯这个错，改掉」，或当前 session 明显刚用过某 skill | **模式 B：会话提炼**                                       |
| 「进化 / 优化 / 评估 X skill」「X 质量怎么样、帮我打分并改进」，无具体会话上下文         | **模式 A：批量进化**                                       |
| 说不清                                                      | 直接问一句：「是查看历史，还是把刚才的经历沉淀进去（模式B）／对 X 做一轮整体质量爬山（模式A）？」 |

**「模式」专指进化的两种方式（A 批量 / B 会话提炼）**，二者共用第 4 节的验证门控与 `evolve.py` 账本，只是候选编辑的**来源**不同。历史查询是只读操作，不触发任何编辑，**不算模式**。

### 1.1 历史查询（只读，纯文件读取、零 LLM、高性能）
用户随时可问，与是否正在进化无关。直接调下面命令并把输出展示给用户：
- `evolve.py history <skill名>` — 跨所有 run 汇总：每次进化的 best 版本、总分、攻坚维度、改动摘要。
- `evolve.py last <skill名>` — 最近一次 run 的完整 9 维账本（看每一维分数变化 + 每次改了什么）。
- `evolve.py show <run_dir>` — 指定某次 run 的完整 9 维明细。
若 `history` 报「找不到记录」，说明该 skill 还没被进化过，如实告知用户。

---

## 2. 模式 A：批量进化（由 rubric 短板驱动）

### Phase 0 — 设置与隔离
1. 定位目标 skill **目录**（确认其中有 SKILL.md；不存在见第 5 节异常表）。
2. `evolve.py init <目标skill目录>` → 拿到 `run_dir`，整目录已快照为 `v0/`。
3. 一句话向用户声明隔离原则（编辑≠评分、目录零污染）。

### Phase 0.5 — 设计验证集（🛑 检查点①）
1. 参照 [templates/test-prompts.template.json](templates/test-prompts.template.json) 起草 **3-5 个测试 prompt**，覆盖典型/边界/歧义三类。
2. **展示给用户确认/增删改**，确认后才继续。测试集是适应度函数的一半，必须真实。

### Phase 1 — 基线评估
1. spawn **独立 judge** 盲评 `v0/`（读整个版本目录：SKILL.md + references 等，结构分），并按测试集跑「有 skill vs 无 skill baseline」得实测分（见 judge-protocol）。
2. `evolve.py ledger9 <run_dir> v0 <dim1..dim9> <total> baseline - "baseline"`（9 维明细全部记账）。
3. 记下 `lowest_dimension` 作为首轮攻坚目标。

### Phase 2 — 进化循环（爬山 + 验证门控 + 棘轮）
重复以下，直到收敛：
1. **editor agent**（与 judge 不同实例）针对**当前最低维度**提出**有界编辑（≤3 处）**，先读 `run_dir/rejected.md` 避免重复已被拒的方向。把当前 best 版本目录拷为 `run_dir/cand/`，在 `cand/` 内做改动（可改 SKILL.md，也可改/增 references 等被引用文件）。
2. `evolve.py snapshot <run_dir> run_dir/cand` → 得 `vN/`（snapshot 归一化换行符；若候选整目录与上一版相同会报错拒绝）。
3. spawn **新的独立 judge** 盲评 `vN/`（读整个版本目录，结构 + 实测），拿到 9 维明细。
4. **验证门控**：`vN.total` **严格 >** 当前 best.total？
   - 是 → `ledger9 ... keep <攻坚维度> <note>`，best 推进到 vN。note 写清**改了什么**。
   - 否 → `ledger9 ... reject <攻坚维度> <note>`，把这次编辑摘要追加到 `rejected.md`。
5. 🛑 **检查点②**：`evolve.py diff <run_dir> <best前> vN` 展示多文件差异 + 评分变化，等用户确认再进下一轮。
6. **收敛判定**：连续 2 轮 Δtotal < 2 → 自动 break，告知用户「已触顶」。

### Phase 3 — 收尾（🛑 检查点③）
1. `evolve.py best <run_dir>` 取最优版本目录。
2. `evolve.py writeback <run_dir> --dry-run` 给用户看将覆盖/新增的文件清单；用户确认后 `evolve.py writeback <run_dir>` 以 overlay 写回目标 skill 目录（覆盖+新增，不删除）。
3. `evolve.py show <run_dir>` 输出 9 维演进轨迹给用户。

---

## 3. 模式 B：会话提炼（随用随学）★ 本 skill 的特色

**前提认知**：用户**不会**直接说「给 skill 加条可泛化规则」。他们只会提**具体问题 / 具体纠正 / 具体偏好**（如「这次输出怎么没带链接」「这个接口又用错了」「不对，应该先查再写」）。
**把具体事件抽象成可泛化规则，是你（agent）的职责，不是用户的。** 触发时你**回看当前会话上下文**完成这件事——无需 hook、无需常驻收件箱。

### Phase D1 — 提炼（distill）
1. 回看当前 session 中**目标 skill 被使用的那段对话**，定位关键事件：
   - (a) 用户给出具体纠正 / 改了你的做法；
   - (b) 你犯了一个**本该被该 skill 拦住**的错；
   - (c) 用户表达了具体偏好（输出格式、默认值、顺序…）。
2. 对每个事件做 **抽象 + 泛化性判定**：「这个具体事件 → 能否变成一条跨任务复用的规则？」
   - **可泛化** → 进候选（例：「输出任务结果时附可点击 url」）。
   - **一次性** → 丢弃，并向用户说明为何不沉淀（例：「这次用张三的 id」只对本任务有效）。
   - 判不准 → 默认**不**沉淀，列出来问用户。（对应反模式⑥：宁可漏，不可把一次性细节当规则塞进去。）
3. 给每条候选标注：**来自哪段对话** + **抽象成什么规则** + **归到 rubric 哪一维**。

### 🛑 检查点① — 候选确认
把候选规则列表（含上面三项标注）展示给用户，删 / 改 / 留由用户定。**没有用户确认不得并入。**

### Phase D2 — 门控并入（复用第 4 节门控）
对每条确认的候选：
1. `evolve.py init <目标skill目录>`（若本 session 尚无 run_dir）。
2. editor agent 把当前 best 版本目录拷为 `run_dir/cand/`，把该规则落成**一处有界编辑**（改 SKILL.md 或对应被引用文件）→ `snapshot run_dir/cand` 得 vN。
3. spawn **独立 judge** 盲评 `vN/`（读整目录；该规则**若可测**则补 1 个真实实测）。
4. **棘轮**：vN.total 严格 > best → keep；否则 reject 并入 `rejected.md`。
   `ledger9` 的 status 用 `distill-keep` / `distill-reject`，note 标注 `session:<事件简述>`。

### 🛑 检查点② — 写回确认
`evolve.py diff` 展示最终多文件改动 + 评分变化 → 用户确认 → `evolve.py writeback <run_dir>` overlay 写回目标 skill 目录 → `show` 给轨迹。

### 跨 session 兜底
用户若想**暂不并入**，把候选规则追加到 `run_dir/pending.md`，下次可用模式 A 入口继续走门控。**不引入 hook、不引入常驻收件箱**——保持「触发时回看上下文」这一个机制。

---

## 4. 验证门控（两模式共用的核心）

> 这是 SkillOpt validation-gated 的落地，也是防止 skill 退化的唯一闸门。

```
候选编辑 → snapshot vN → 独立 judge 盲评(结构+实测) → 得 vN.total

第一步 · 定「比较分」score：
  IF |vN.total − best.total| ≥ 3 → score = vN.total          （差距够大，单 judge 足够）
  ELSE                          → 读 [judge-protocol.md](references/judge-protocol.md) §5，
                                  升级 3~5 个独立 judge 取共识 → score = 共识 total

第二步 · 唯一判定一次：
  IF score 严格 > best.total → keep   （best 推进，ledger9 记 keep/distill-keep）
  ELSE                       → reject （best 不动，编辑写入 rejected.md，ledger9 记 reject/distill-reject）
```
- **严格大于**才接受：相等也回滚（除非是「同分但更简洁」，此时由检查点交用户定夺——对应 autoresearch 的简单性偏好）。
- **为何要升级**：单 judge 有 ±1~2 分噪声，差距 <3 时单 judge 判不准，必须用多 judge 共识再判，否则会被噪声误判。升级后若 `|score − best| < 1`，视为统计无差异，按 reject 处理（保守，遵循严格改善）。
- best 永远以快照形式留在 run_dir，**回滚=不采用 vN**，不需要任何 reset，天然安全。

---

## 5. 异常表（遇到必须先告知用户再 fallback，绝不静默跳过）

| 异常 | 检测 | 处理 |
|---|---|---|
| 目标 SKILL.md 不存在 | `init` 报错 | 告知用户，请其确认 skill 名 / 路径；可查看当前 runtime 的 skills 目录协助定位 |
| 目标无 frontmatter | `init` 打 WARN | 继续，但提示维度1会扣分；建议先补 frontmatter |
| 工作目录已存在（同分钟重复 init） | `init` 报错 | 告知用户，稍候重试或指定复用已有 run_dir |
| 测试 prompt 跑失败/无法执行 | 实测无输出 | 告知用户，降级为 dry_run 并在维度8封顶7分打 ⚠️，**不**伪造实测分 |
| 全程无真实实测 | judge `real_test=false` | 明确告诉用户「本轮实测信号弱，分数仅供参考」，不据此自动推进 |
| 模式B 上下文里找不到可泛化事件 | D1 候选为空 | 如实告知「这段对话没提炼出可泛化规则」，不硬凑，结束或转模式A |
| 账本/快照损坏 | `evolve.py` 报表头错 | 告知用户，备份后重 init，不静默重建 |

---

## 6. 反模式（来自论文 + 实跑教训，务必规避）

1. **judge 与 editor 同体** → 自评作弊。永远 spawn 独立 judge 盲评。
2. **全 dry-run 无真实实测** → 分数虚高。至少 1 个真实 full_test，否则维度8封顶并打 ⚠️。
3. **一次重写整个文档** → 失控。坚持有界编辑（≤3 处，textual learning rate）。
4. **不记被拒编辑** → 反复撞同一堵墙。每次 reject 写入 `rejected.md`，下轮先读。
5. **异常静默跳过** → 破坏棘轮完整性。按第 5 节，先告知用户。
6. **把一次性细节当可泛化规则并入**（模式B 特有）→ 污染 skill。显式做泛化性判定，存疑就丢并说明。
7. **跳过验证门控直接 append 用户建议** → 可泛化但错误的规则会让 skill 变差。会话提炼出的候选也必须走 judge 盲评 + 棘轮。

---

## 7. 一句话自检
开始前确认：① 我会 spawn 独立 judge 吗？② 测试集里有真实实测吗？③ 编辑有界吗？④ 每个写回前有检查点吗？四个都是「是」才动手。

---

*Author: **MoonCat** · skill-evolve v1.0.0 · 思想来源见 [README.md](README.md)*
