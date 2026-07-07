#!/usr/bin/env python3
"""
evolve.py — skill-evolve 的版本/账本助手（纯 stdlib，零依赖）。

把「外部账本 + 快照」哲学翻译到 skill 进化：
所有进化状态放在独立工作目录里，目标 skill 目录零污染（不塞 .git / .bak）。

**版本 = 整个 skill 目录的快照**（不是单个 SKILL.md），因此 references/ scripts/
等被引用文件也一并进化、judge 也能在完整上下文里核实资源。

工作目录: ~/.claude/skill-evolve-runs/<skill>-<YYYYMMDD-HHMM>/
  v0/, v1/, ...       每个版本 = skill 目录的完整快照（v0 = 基线）
  results.tsv         账本：每次评估一行（含 9 维明细）
  rejected.md         负反馈缓冲：被门控拒掉的编辑（避免重复同一失败方向）
  pending.md          跨 session 兜底：提炼出但暂未并入的候选规则
  meta.json           记录目标 skill 目录路径等

子命令:
  init <skill目录或SKILL.md>                      建工作目录，整目录快照为 v0/，返回 run_dir
  snapshot <run_dir> <候选目录>                   把候选目录存为下一个 vN/，返回版本号
  ledger9 <run_dir> <ver> <d1> .. <d9> <total> <status> <target_dim> <note>
                                                  追加一行含 9 维明细的账本
  ledger <run_dir> <ver> <struct> <test> <total> <status> <dim> <note>
                                                  旧版兼容：追加一行旧式账本
  diff <run_dir> <vA> <vB>                        递归 diff 两个版本目录
  best <run_dir>                                  返回当前 total 最高的版本号与目录路径
  writeback <run_dir> [version]                   把某版本(默认best)以 overlay 写回目标 skill 目录
  show <run_dir>                                  打印账本全文 + 当前 best
  history <skill_name>                            汇总该 skill 所有进化 run 的 best 分数
  last <skill_name>                               打印该 skill 最近一次 run 的完整 9 维账本

约定:
  - 分数用浮点；status ∈ {baseline, keep, reject, distill-keep, distill-reject}
  - 任何异常都以非 0 退出码 + stderr 信息结束，绝不静默吞掉（对应反模式#5）
"""

import argparse
import csv
import difflib
import json
import os
import re
import shutil
import sys
from datetime import datetime

# Windows 控制台默认 GBK，强制 UTF-8 输出，避免中文乱码（文件读写已显式 encoding="utf-8"）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

def _infer_runs_root():
    """根据 skill-evolve 自身安装位置推断工作目录根。

    evolve.py 位于 <runtime_dir>/skills/skill-evolve/scripts/evolve.py
    工作目录默认放在 <runtime_dir>/skill-evolve-runs/ 下。
    若路径结构不符合预期（例如作为参考资料直接运行时），回退到 ~/.claude。
    """
    evolve_py = os.path.abspath(__file__)
    skill_evolve_dir = os.path.dirname(os.path.dirname(evolve_py))  # .../skill-evolve
    skills_dir = os.path.dirname(skill_evolve_dir)                   # .../skills
    runtime_dir = os.path.dirname(skills_dir)                        # ~/.claude / ~/.codex ...
    # 简单校验：runtime 目录通常以点开头的隐藏目录
    if not os.path.basename(runtime_dir).startswith("."):
        runtime_dir = os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(runtime_dir, "skill-evolve-runs")


RUNS_ROOT = _infer_runs_root()
LEDGER_NAME = "results.tsv"

# 新版 9 维明细表头
LEDGER_HEADER_9 = ["timestamp", "version",
                   "dim1", "dim2", "dim3", "dim4", "dim5",
                   "dim6", "dim7", "dim8", "dim9",
                   "total", "status", "target_dimension", "note"]

# 旧版兼容表头（早期实现）
LEDGER_HEADER_OLD = ["timestamp", "version", "struct_score", "test_score",
                     "total", "status", "target_dimension", "note"]

VALID_STATUS = {"baseline", "keep", "reject", "distill-keep", "distill-reject"}
DIM_NAMES = {
    1: "Frontmatter",
    2: "Workflow",
    3: "FailureEncoding",
    4: "Checkpoint",
    5: "ActionableSpecificity",
    6: "ResourceIntegration",
    7: "ArchitectureAntipatterns",
    8: "LiveTest",
    9: "SafetyBlacklist",
}


def _die(msg, code=1):
    print(f"[evolve.py ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def _now_stamp():
    # 脚本里允许用真实时间（SKILL.md 工作流不依赖模型生成时间）
    return datetime.now().strftime("%Y%m%d-%H%M")


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _ledger_path(run_dir):
    return os.path.join(run_dir, LEDGER_NAME)


def _detect_header(path):
    """返回 (header_list, is_9dim)"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
    if header == LEDGER_HEADER_9:
        return header, True
    if header == LEDGER_HEADER_OLD:
        return header, False
    _die(f"账本表头损坏: {header!r}，期望新版 {LEDGER_HEADER_9!r} 或旧版 {LEDGER_HEADER_OLD!r}")


def _read_ledger(run_dir):
    path = _ledger_path(run_dir)
    if not os.path.exists(path):
        _die(f"账本不存在: {path}（先跑 init）")
    header, is_9 = _detect_header(path)
    rows = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for r in reader:
            # 统一补 dim1-dim9 字段，方便下游处理
            if not is_9:
                # 旧版只有 struct/test/total，无法还原 9 维，置空占位
                for i in range(1, 10):
                    r[f"dim{i}"] = ""
            rows.append(r)
    return rows, is_9


def _version_num(ver):
    """'v3' -> 3 ; '3' -> 3"""
    m = re.fullmatch(r"v?(\d+)", str(ver).strip())
    if not m:
        _die(f"非法版本号: {ver!r}（应形如 v0 / 3）")
    return int(m.group(1))


def _version_dir(run_dir, ver):
    return os.path.join(run_dir, f"v{_version_num(ver)}")


def _next_version(run_dir):
    n = -1
    for name in os.listdir(run_dir):
        m = re.fullmatch(r"v(\d+)", name)
        if m and os.path.isdir(os.path.join(run_dir, name)):
            n = max(n, int(m.group(1)))
    return n + 1


def _safe_skill_name(skill_name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", skill_name)


def _normalize(text):
    """统一换行符为 LF，避免 CRLF/LF 差异污染 diff。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


# 视为文本（进化/diff 关心）的扩展名；其余按二进制原样拷贝
TEXT_EXTS = {".md", ".txt", ".py", ".json", ".html", ".htm",
             ".yaml", ".yml", ".toml", ".csv", ".sh", ".mjs", ".js", ".ts"}
# 拷贝时跳过的目录（无意义/会爆量）
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".DS_Store"}


def _is_text(path):
    return os.path.splitext(path)[1].lower() in TEXT_EXTS


def _copytree(src, dst):
    """递归拷贝 src 目录树到 dst。文本文件归一化为 LF，其余原样。跳过 SKIP_DIRS。"""
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(root, src)
        out_root = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out_root, exist_ok=True)
        for fn in files:
            sp = os.path.join(root, fn)
            dp = os.path.join(out_root, fn)
            if _is_text(sp):
                try:
                    with open(sp, "r", encoding="utf-8") as f:
                        data = _normalize(f.read())
                    with open(dp, "w", encoding="utf-8", newline="\n") as f:
                        f.write(data)
                    continue
                except UnicodeDecodeError:
                    pass  # 当作二进制
            shutil.copy2(sp, dp)


def _rel_files(root):
    """返回 root 下所有文件的相对路径集合（跳过 SKIP_DIRS）。"""
    out = set()
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            out.add(os.path.relpath(os.path.join(r, fn), root).replace("\\", "/"))
    return out


def _read_maybe(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _normalize(f.read()), True
    except (UnicodeDecodeError, OSError):
        return None, False  # 二进制/不可读


def _dirs_text_equal(a, b):
    """两目录的文件集合 + 文本内容是否完全一致（二进制按字节比）。"""
    fa, fb = _rel_files(a), _rel_files(b)
    if fa != fb:
        return False
    for rel in fa:
        pa, pb = os.path.join(a, rel), os.path.join(b, rel)
        ta, oka = _read_maybe(pa)
        tb, okb = _read_maybe(pb)
        if oka and okb:
            if ta != tb:
                return False
        else:  # 二进制按字节比
            if open(pa, "rb").read() != open(pb, "rb").read():
                return False
    return True



def _list_runs(skill_name):
    """返回某 skill 的所有 run_dir，按时间戳升序"""
    safe = _safe_skill_name(skill_name)
    if not os.path.isdir(RUNS_ROOT):
        return []
    runs = []
    for name in os.listdir(RUNS_ROOT):
        # 格式: <skill>-<YYYYMMDD-HHMM> 或 skill-...
        if not name.startswith(safe + "-"):
            continue
        full = os.path.join(RUNS_ROOT, name)
        if os.path.isdir(full) and os.path.exists(_ledger_path(full)):
            runs.append(full)
    runs.sort(key=lambda p: os.path.basename(p))
    return runs


def _best_row(rows):
    """从 rows 里选 total 最高的 keep/baseline/distill-keep，失败则选最高 total"""
    if not rows:
        return None
    kept = [r for r in rows if r.get("status") in ("baseline", "keep", "distill-keep")]
    pool = kept or rows
    return max(pool, key=lambda r: float(r.get("total") or 0))


# --------------------------------------------------------------------------- #

def cmd_init(args):
    target = os.path.abspath(os.path.expanduser(args.target_skill_md))
    if not os.path.exists(target):
        _die(f"目标不存在: {target}")
    # 解析到 skill 目录（传 SKILL.md 则取其父目录）
    if os.path.isfile(target):
        skill_dir = os.path.dirname(target)
    else:
        skill_dir = target
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.exists(skill_md):
        _die(f"skill 目录里没有 SKILL.md: {skill_dir}")

    with open(skill_md, "r", encoding="utf-8") as f:
        content = f.read()
    if not content.lstrip().startswith("---"):
        print("[evolve.py WARN] SKILL.md 缺少 frontmatter（--- 开头），仍继续，但 rubric 维度1 会扣分。",
              file=sys.stderr)

    skill_name = _safe_skill_name(os.path.basename(skill_dir) or "skill")
    run_dir = os.path.join(RUNS_ROOT, f"{skill_name}-{_now_stamp()}")
    if os.path.exists(run_dir):
        _die(f"工作目录已存在: {run_dir}（同名 run 同分钟内重复？请稍后重试或手动清理）")
    os.makedirs(run_dir)

    # 整目录快照为 v0/
    _copytree(skill_dir, _version_dir(run_dir, 0))
    with open(_ledger_path(run_dir), "w", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter="\t").writerow(LEDGER_HEADER_9)
    open(os.path.join(run_dir, "rejected.md"), "w", encoding="utf-8").write(
        "# 被门控拒掉的编辑（负反馈缓冲）\n\n")
    open(os.path.join(run_dir, "pending.md"), "w", encoding="utf-8").write(
        "# 提炼出但暂未并入的候选规则（跨 session 兜底）\n\n")
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"target": skill_dir, "skill_name": skill_name,
                   "created": _now_iso()}, f, ensure_ascii=False, indent=2)

    nfiles = len(_rel_files(_version_dir(run_dir, 0)))
    print(run_dir)
    print(f"[evolve.py] v0/ 已快照整目录（{nfiles} 个文件）", file=sys.stderr)


def cmd_snapshot(args):
    run_dir = args.run_dir
    cand = args.candidate_file  # 现在是候选目录
    if not os.path.isdir(run_dir):
        _die(f"工作目录不存在: {run_dir}")
    if not os.path.isdir(cand):
        _die(f"候选目录不存在或不是目录: {cand}（editor 应把改后的整个 skill 目录写到候选目录，如 run_dir/cand）")
    # 候选目录不得是版本快照本身（防 editor 把基线/快照当工作目录）
    if re.fullmatch(r"v\d+", os.path.basename(os.path.normpath(cand))):
        _die(f"候选目录不能是版本快照 {os.path.basename(cand)}；快照只读，editor 应写到独立候选目录（如 cand/）")
    if not os.path.exists(os.path.join(cand, "SKILL.md")):
        _die(f"候选目录缺少 SKILL.md: {cand}")
    n = _next_version(run_dir)
    # 护栏：候选与上一版整目录内容相同 → 极可能 editor 改错了地方或没改动
    if n > 0 and _dirs_text_equal(cand, _version_dir(run_dir, n - 1)):
        _die(f"候选与上一版 v{n-1} 内容完全相同：editor 可能改错了位置（如误改了基线/快照）或根本没改动。"
             f"已拒绝快照，请检查 editor 是否在独立候选目录里做了改动。")
    _copytree(cand, _version_dir(run_dir, n))
    print(f"v{n}")


def cmd_ledger9(args):
    """新版 9 维明细账本"""
    run_dir = args.run_dir
    if args.status not in VALID_STATUS:
        _die(f"非法 status: {args.status!r}，应 ∈ {sorted(VALID_STATUS)}")
    _read_ledger(run_dir)
    dims = [f"{float(getattr(args, f'd{i}')):.1f}" for i in range(1, 10)]
    row = [_now_iso(), f"v{_version_num(args.version)}",
           *dims,
           f"{float(args.total):.1f}", args.status,
           args.dimension, args.note]
    with open(_ledger_path(run_dir), "a", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter="\t").writerow(row)
    print("\t".join(row))


def cmd_ledger(args):
    """旧版兼容：struct/test/total 拆到 dim8/5/7 不可靠，直接写空 9 维"""
    run_dir = args.run_dir
    if args.status not in VALID_STATUS:
        _die(f"非法 status: {args.status!r}，应 ∈ {sorted(VALID_STATUS)}")
    rows, is_9 = _read_ledger(run_dir)
    if is_9:
        _die("当前 run 是 9 维账本，请改用 ledger9")
    row = [_now_iso(), f"v{_version_num(args.version)}",
           f"{float(args.struct):.1f}", f"{float(args.test):.1f}",
           f"{float(args.total):.1f}", args.status,
           args.dimension, args.note]
    with open(_ledger_path(run_dir), "a", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter="\t").writerow(row)
    print("\t".join(row))


def cmd_diff(args):
    run_dir = args.run_dir
    da, db = _version_dir(run_dir, args.vA), _version_dir(run_dir, args.vB)
    for p in (da, db):
        if not os.path.isdir(p):
            _die(f"版本目录不存在: {p}")
    fa, fb = _rel_files(da), _rel_files(db)
    added = sorted(fb - fa)
    removed = sorted(fa - fb)
    common = sorted(fa & fb)

    changed = []
    for rel in common:
        ta, oka = _read_maybe(os.path.join(da, rel))
        tb, okb = _read_maybe(os.path.join(db, rel))
        if oka and okb:
            if ta != tb:
                changed.append(rel)
        else:
            if open(os.path.join(da, rel), "rb").read() != open(os.path.join(db, rel), "rb").read():
                changed.append(rel)

    # 文件级清单
    print(f"# diff v{_version_num(args.vA)} → v{_version_num(args.vB)}")
    print(f"新增 {len(added)} | 删除 {len(removed)} | 修改 {len(changed)}")
    for rel in added:
        print(f"  + {rel}")
    for rel in removed:
        print(f"  - {rel}")
    for rel in changed:
        print(f"  ~ {rel}")
    print()

    # 逐文本文件 unified diff
    for rel in changed + added:
        pa = os.path.join(da, rel)
        pb = os.path.join(db, rel)
        if not _is_text(pb if rel in added else pa):
            print(f"=== {rel} (binary changed) ===")
            continue
        a = (_read_maybe(pa)[0] or "").splitlines(keepends=True) if rel not in added else []
        b = (_read_maybe(pb)[0] or "").splitlines(keepends=True)
        print(f"=== {rel} ===")
        sys.stdout.writelines(difflib.unified_diff(
            a, b, fromfile=f"v{_version_num(args.vA)}/{rel}",
            tofile=f"v{_version_num(args.vB)}/{rel}"))
        print()


def cmd_best(args):
    rows, _ = _read_ledger(args.run_dir)
    if not rows:
        _die("账本为空，还没有任何评估记录")
    best = _best_row(rows)
    ver = best["version"]
    print(json.dumps({
        "version": ver,
        "dir": _version_dir(args.run_dir, ver),
        "total": float(best["total"]),
        "status": best["status"],
    }, ensure_ascii=False))


def cmd_writeback(args):
    """把某版本(默认 best)以 overlay 写回目标 skill 目录：覆盖/新增，绝不删除。"""
    run_dir = args.run_dir
    meta_path = os.path.join(run_dir, "meta.json")
    if not os.path.exists(meta_path):
        _die(f"meta.json 不存在: {meta_path}")
    with open(meta_path, "r", encoding="utf-8") as f:
        target = json.load(f)["target"]
    if not os.path.isdir(target):
        _die(f"目标 skill 目录不存在: {target}")

    if args.version:
        ver = f"v{_version_num(args.version)}"
    else:
        rows, _ = _read_ledger(run_dir)
        if not rows:
            _die("账本为空，无法确定 best")
        ver = _best_row(rows)["version"]
    src = _version_dir(run_dir, ver)
    if not os.path.isdir(src):
        _die(f"版本目录不存在: {src}")

    src_files = _rel_files(src)
    tgt_files = _rel_files(target)
    overwrite = sorted(f for f in src_files if f in tgt_files)
    add = sorted(f for f in src_files if f not in tgt_files)
    kept = sorted(f for f in tgt_files if f not in src_files)  # overlay 不删，仅报告

    if args.dry_run:
        print(f"# writeback dry-run: {ver} → {target} (overlay，不删除)")
        print(f"覆盖 {len(overwrite)} | 新增 {len(add)} | 保留(快照中没有，不动) {len(kept)}")
        for f in overwrite:
            print(f"  ~ {f}")
        for f in add:
            print(f"  + {f}")
        for f in kept:
            print(f"  = {f}")
        return

    for rel in sorted(src_files):
        sp = os.path.join(src, rel)
        dp = os.path.join(target, rel)
        os.makedirs(os.path.dirname(dp) or ".", exist_ok=True)
        shutil.copy2(sp, dp)
    print(f"[evolve.py] writeback 完成: {ver} → {target}（覆盖 {len(overwrite)}，新增 {len(add)}，"
          f"保留未动 {len(kept)}）")


def _format_dim_row(r):
    """把一行的 9 维分数格式化成表格行"""
    cells = [r.get("version", ""), r.get("status", "")]
    for i in range(1, 10):
        v = r.get(f"dim{i}", "")
        cells.append(v if v != "" else "-")
    cells.append(r.get("total", ""))
    cells.append(r.get("target_dimension", ""))
    cells.append(r.get("note", ""))
    return cells


def cmd_show(args):
    rows, is_9 = _read_ledger(args.run_dir)
    print(f"== 账本 {_ledger_path(args.run_dir)} ==")
    if is_9:
        header = ["ver", "status", "d1", "d2", "d3", "d4", "d5",
                  "d6", "d7", "d8", "d9", "total", "target", "note"]
        col_widths = [6, 10, 5, 5, 5, 5, 5, 5, 5, 5, 5, 7, 18, 30]
        print(" ".join(h.ljust(w) for h, w in zip(header, col_widths)))
        for r in rows:
            cells = _format_dim_row(r)
            print(" ".join(str(c).ljust(w) for c, w in zip(cells, col_widths)))
    else:
        print("\t".join(LEDGER_HEADER_OLD))
        for r in rows:
            print("\t".join(r[h] for h in LEDGER_HEADER_OLD))
    if rows:
        best = _best_row(rows)
        print(f"\n当前 best: {best['version']} (total={best['total']}, status={best['status']})")


def cmd_history(args):
    """汇总某 skill 所有 run 的 best 分数与改动 note"""
    runs = _list_runs(args.skill_name)
    if not runs:
        _die(f"找不到 {args.skill_name} 的任何进化记录（在 {RUNS_ROOT}）")
    print(f"# {args.skill_name} 的进化历史\n")
    header = ["run", "best_ver", "best_total", "best_status", "target", "note"]
    col_widths = [32, 10, 12, 12, 18, 40]
    print("| " + " | ".join(h.ljust(w) for h, w in zip(header, col_widths)) + " |")
    print("|" + "|".join("-" * (w + 2) for w in col_widths) + "|")
    for run_dir in runs:
        rows, is_9 = _read_ledger(run_dir)
        best = _best_row(rows)
        if not best:
            continue
        run_label = os.path.basename(run_dir)
        note = (best.get("note") or "")[:col_widths[-1]]
        cells = [
            run_label,
            best.get("version", ""),
            best.get("total", ""),
            best.get("status", ""),
            best.get("target_dimension", ""),
            note,
        ]
        print("| " + " | ".join(str(c).ljust(w) for c, w in zip(cells, col_widths)) + " |")


def cmd_last(args):
    """打印某 skill 最近一次 run 的完整 9 维账本"""
    runs = _list_runs(args.skill_name)
    if not runs:
        _die(f"找不到 {args.skill_name} 的任何进化记录（在 {RUNS_ROOT}）")
    run_dir = runs[-1]
    print(f"# {args.skill_name} 最近一次进化: {os.path.basename(run_dir)}\n")
    cmd_show(argparse.Namespace(run_dir=run_dir))


def build_parser():
    p = argparse.ArgumentParser(description="skill-evolve 版本/账本助手（纯 stdlib）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="建工作目录，整目录快照为 v0/")
    s.add_argument("target_skill_md", help="目标 skill 目录（或其中的 SKILL.md 路径）")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("snapshot", help="把候选目录存为下一个 vN/")
    s.add_argument("run_dir")
    s.add_argument("candidate_file", help="候选 skill 目录（如 run_dir/cand）")
    s.set_defaults(func=cmd_snapshot)

    s = sub.add_parser("ledger9", help="追加一行含 9 维明细的账本")
    s.add_argument("run_dir")
    s.add_argument("version")
    for i in range(1, 10):
        s.add_argument(f"d{i}")
    s.add_argument("total")
    s.add_argument("status")
    s.add_argument("dimension")
    s.add_argument("note")
    s.set_defaults(func=cmd_ledger9)

    s = sub.add_parser("ledger", help="旧版兼容：追加一行旧式账本（仅限旧 run）")
    s.add_argument("run_dir")
    s.add_argument("version")
    s.add_argument("struct")
    s.add_argument("test")
    s.add_argument("total")
    s.add_argument("status")
    s.add_argument("dimension")
    s.add_argument("note")
    s.set_defaults(func=cmd_ledger)

    s = sub.add_parser("diff", help="两版差异")
    s.add_argument("run_dir")
    s.add_argument("vA")
    s.add_argument("vB")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("best", help="返回当前最优版本（JSON）")
    s.add_argument("run_dir")
    s.set_defaults(func=cmd_best)

    s = sub.add_parser("writeback", help="把某版本(默认best)以 overlay 写回目标 skill 目录")
    s.add_argument("run_dir")
    s.add_argument("version", nargs="?", default=None, help="版本号（默认 best）")
    s.add_argument("--dry-run", action="store_true", help="只打印将覆盖/新增的文件清单，不写")
    s.set_defaults(func=cmd_writeback)

    s = sub.add_parser("show", help="打印账本 + 当前 best")
    s.add_argument("run_dir")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("history", help="汇总某 skill 所有 run 的 best 分数与 note")
    s.add_argument("skill_name")
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("last", help="打印某 skill 最近一次 run 的完整账本")
    s.add_argument("skill_name")
    s.set_defaults(func=cmd_last)

    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
