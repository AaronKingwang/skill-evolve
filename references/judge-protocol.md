# judge 协议 — 独立盲评

> 这是整个 skill-evolve 的**核心防作弊机制**：把评测与编辑彻底隔离 ——
> **改 skill 的 agent ≠ 评 skill 的 agent**，评分者碰不到编辑过程，编辑者拿不到评分笔。

## 1. 隔离规则（必须遵守）

- judge **必须是独立的子 agent**（用 Agent 工具单独 spawn），**不能**由提出编辑的同一个 agent 自己打分。
- judge 是**盲评**：spawn 它时**只给**：
  - 待评**版本目录**的路径（如 `run_dir/vN/`），让它 Read `SKILL.md` 并按需 `ls`/Read `references/` 等被引用文件
  - `references/rubric.md` 全文（评分标准）
  - （维度 8 需要时）测试 prompt 的执行轨迹
- **绝不告诉 judge**：这是第几版、上一版多少分、这次改了什么、编辑意图是什么。否则它会锚定、护短，作弊立刻发生。
- 同一候选若要复核，可 spawn 多个独立 judge，按 §5 的方法合成分数，但每个都必须盲评。

## 2. judge 的输入模板（spawn 子 agent 时的 prompt 骨架）

```
你是一个独立的 skill 质量评审。严格按附带的 rubric 给一个 skill 打分。
只看内容本身，不要猜测它的来历或版本。逐维给分 + 一句证据（引用原文片段）。

待评 skill 版本目录：<run_dir>/vN/
请 Read 其中的 SKILL.md，并对维度 6（资源集成）**实际 ls/Read 该目录下 references/ 等被引用文件，核实它们真实存在**，不要凭 SKILL.md 的文字描述臆断。

=== rubric ===
<粘贴 references/rubric.md 全文>

=== 测试执行轨迹（如有）===
<粘贴每个测试 prompt 的「有 skill」vs「无 skill baseline」运行结果>

按指定 JSON 格式输出，不要有多余文字。
```

## 3. 强制输出格式（judge 必须返回此 JSON）

```json
{
  "dimensions": [
    {"id": 1, "name": "frontmatter", "score": 6, "max": 7, "evidence": "name 与目录一致，但 description 以「等」收尾"},
    {"id": 2, "name": "workflow_clarity", "score": 9, "max": 12, "evidence": "..."},
    {"id": 3, "name": "failure_encoding", "score": 4, "max": 12, "evidence": "只有「注意」无 if-fail 分支"},
    {"id": 4, "name": "checkpoint", "score": 5, "max": 6, "evidence": "..."},
    {"id": 5, "name": "actionable_specificity", "score": 8, "max": 17, "evidence": "出现 5 处对冲词：建议/可考虑/视情况..."},
    {"id": 6, "name": "resource_integration", "score": 3, "max": 4, "evidence": "..."},
    {"id": 7, "name": "safety_blacklist", "score": 2, "max": 9, "evidence": "涉及文件覆盖但无黑名单"},
    {"id": 8, "name": "live_test", "score": 14, "max": 23, "evidence": "1 个 full_test，有 skill 优于 baseline 但另一例无差异", "real_test": true},
    {"id": 9, "name": "antipatterns", "score": 3, "max": 9, "evidence": "..."}
  ],
  "struct_score": 41,
  "test_score": 14,
  "total": 55,
  "lowest_dimension": {"id": 5, "name": "actionable_specificity"},
  "dry_run_warning": false,
  "summary": "最弱在可执行具体性(5)与故障编码(3)，对冲词偏多。"
}
```

字段说明：
- `struct_score` = 维度 1+2+3+4+5+6 之和（满分 59）
- `test_score` = 维度 7+8+9 之和（满分 41）。⚠️ 命名沿用「test」是历史习惯，实际是有效性维合计。
- `total` = struct_score + test_score（满分 100）
- `real_test`（维度 8 内）：是否有至少 1 个真实 full_test。若 false → 维度 8 封顶 7 分。
- `dry_run_warning`：维度 8 全是 dry_run 时置 true，调用方需在账本 note 里打 ⚠️。

## 4. 调用方（skill-evolve 主流程）拿到 JSON 后

1. 用 `struct_score` / `test_score` / `total` 调 `evolve.py ledger9` 记账（9 维明细）。
2. 验证门控：`候选 total` **严格大于** `当前 best total` 才 keep；否则 reject。
3. `lowest_dimension` → 模式 A 下一轮的攻坚目标。
4. `dry_run_warning=true` 或 `real_test=false` → 提醒用户本轮实测信号弱，不要据此推进。

## 5. 多 judge 评分计算法

### 5.1 怎么算分（两步，judge 之间一律等权）
**第一步 · 单 judge 内部（9 维 → 该 judge 总分）**——所有 judge 都一样：
```
该judge总分 = Σ(维度分 × 权重) / 满分归一   （即本 rubric 的 total 字段）
```

**第二步 · 多 judge 合成（逐维等权 + 中位数兜底）**：
```
共识维度分_d = median(各 judge 在维度 d 的分)      # 逐维、对每一维单独取中位数
最终 total   = Σ(共识维度分_d)                      # 再合成总分
共识 lowest  = 共识维度分里满分占比最低的维度        # 作为下一轮攻坚目标
```
- **judge 之间等权**（每个 1 票）。**不要给 judge 不等权** —— 没有可靠依据让某个 judge 多说话，加权只会引入主观性。
- **逐维合成（非总分平均）**：数学上总分结果与「总分直接平均」相等，但逐维能额外产出**每维共识分 + 分歧度**，这是选攻坚维必需的信息。
- **中位数兜底（judge≥5 时）**：对每一维取中位数而非均值，抗单个「叛逆 judge」带偏。judge=3 时中位数=中间那个；judge<3 用均值。

### 5.2 记账
- 用 `共识维度分` 调 `evolve.py ledger9` 记一行，note 标注 `consensus-N`（N=judge 数）。

### 5.3 降噪只是治标——能自动判对错就别用 judge
LLM 评 rubric 本身可信度有限（细粒度判别约七成）。若目标 skill 的任务**可自动判对错**（QA/代码/表格/工具调用），应改用**真实任务通过率**（精确匹配 / 部分给分）当门控，绕过 judge 方差；只有**开放生成类**（写 PRD/文案/设计）才退回本协议的 LLM judge + 多 judge 降噪。
