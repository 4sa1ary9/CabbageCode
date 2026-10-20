"""任务级评测：在临时工作区里用 -p 模式跑任务，再用检查命令判分。

两种任务来源：
  --suite dirs      --tasks 下每个含 task.json 的子目录是一个任务：
                    {"prompt": "...", "check": ["{python}", "-m", "pytest", "-q"], "hide": [".meta"]}
  --suite polyglot  --tasks 指向 polyglot-benchmark/python/exercises/practice（Aider 的基准），
                    按 Aider 的两次尝试协议跑：首轮失败就把测试输出喂给第二轮。

用法：
    uv run python -m cabbagecode.evals --suite polyglot --tasks <practice 目录> --save-dir runs/ --price input=3,output=15
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

EDIT_TOOLS = {"EditFile", "WriteFile"}
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read", "cache_creation")
COUNT_KEYS = ("turns", "tool_calls", "tool_errors", "edit_calls", "edit_errors", "edit_fuzzy", "edit_guarded", *TOKEN_KEYS)
PRICE_ALIASES = {"input": "input_tokens", "output": "output_tokens"}

# 有 shell、能联网的 Agent 在测试被藏起来时会去找答案（下载官方测试、全盘搜题库）。
# 提示词里禁止，再审计每次尝试的工具调用：命中这些模式的尝试记为可疑，干净通过率按失败计。
_NET = re.compile(r"\b(curl|wget)\b|urllib|requests\.(get|post)|https?://|git\s+clone|pip\s+(install|download)", re.I)
_LEAK = re.compile(r"exercism|problem-specifications|canonical-data|polyglot|\.bench\b"
                   r"|\bfind\s+(/|~|/c/Users/[^/\s\"']+)(?=[\s\"']|$)", re.I)
_AUDIT_KEYS = ("command", "file_path", "pattern", "path", "url")

# 以下两段提示词取自 Aider 的 benchmark/prompts.py（Apache-2.0），保持原文以便和 Aider 榜单对照。
INSTRUCTIONS_ADDENDUM = """
####

Use the above instructions to modify the supplied files: {file_list}
Don't change the names of existing functions or classes, as they may be referenced from other code like unit tests, etc.
Only use standard libraries, don't suggest installing any packages.
"""
# 在 Aider 原文之外加的一句：Aider 不能执行命令，这里的 Agent 能，必须说清边界
NO_PEEKING = """Work only with the files in the current directory. Do not access the internet, and do not search other directories for this exercise, its tests or reference solutions.
"""
TEST_FAILURES = """
####

See the testing errors above.
The tests are correct, don't try and change them.
Fix the code in {file_list} to resolve the errors.
"""


@dataclass
class Task:
    task_id: str
    src: Path
    prompt: str
    check: list[str]
    hide: list[str] = field(default_factory=list)   # 开跑前删掉，判分也不需要（如参考答案）
    tests: list[str] = field(default_factory=list)  # 每轮 agent 运行时藏起来，判分前从 src 拷回
    attempts: int = 1
    retry_suffix: str = ""


def load_tasks(root: Path) -> list[Task]:
    tasks = []
    for d in sorted(p for p in root.iterdir() if (p / "task.json").is_file()):
        spec = json.loads((d / "task.json").read_text(encoding="utf-8"))
        hide = [*spec.get("hide", []), "task.json"]
        tasks.append(Task(d.name, d, spec["prompt"], spec["check"], hide))
    return tasks


def load_polyglot(root: Path) -> list[Task]:
    tasks = []
    for d in sorted(p for p in root.iterdir() if (p / ".meta" / "config.json").is_file()):
        files = json.loads((d / ".meta" / "config.json").read_text(encoding="utf-8")).get("files", {})
        docs = [d / ".docs" / n for n in ("introduction.md", "instructions.md", "instructions.append.md")]
        text = "".join(p.read_text(encoding="utf-8") for p in docs if p.exists())
        file_list = " ".join(files.get("solution", []))
        tests = files.get("test", [])
        tasks.append(Task(
            task_id=d.name, src=d,
            prompt=text + INSTRUCTIONS_ADDENDUM.format(file_list=file_list) + NO_PEEKING,
            check=["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
            hide=[".meta", ".approaches", ".articles"],
            tests=tests, attempts=2,
            retry_suffix=TEST_FAILURES.format(file_list=file_list),
        ))
    return tasks


def parse_stream(lines) -> dict:
    """把 -p --output-format stream-json 的 NDJSON 汇总成一行统计。"""
    stats = {**{k: 0 for k in COUNT_KEYS}, "result": "", "errors": [], "suspicious": []}
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "tool_use":
            args = ev.get("args") or {}
            text = " ".join(str(args[k]) for k in _AUDIT_KEYS if isinstance(args.get(k), str))
            if _NET.search(text) or _LEAK.search(text):
                stats["suspicious"].append(f"{ev.get('tool_name')}: {text[:160]}")
        elif kind == "tool_result":
            is_edit = ev.get("tool_name") in EDIT_TOOLS
            stats["tool_calls"] += 1
            stats["edit_calls"] += is_edit
            if ev.get("is_error"):
                stats["tool_errors"] += 1
                stats["edit_errors"] += is_edit
                if is_edit and "would introduce a syntax error" in (ev.get("output") or ""):
                    stats["edit_guarded"] += 1  # 语法守卫拦下的坏编辑
            elif is_edit and "(matched after" in (ev.get("output") or ""):
                stats["edit_fuzzy"] += 1  # 精确匹配失败、靠容错匹配救回来的编辑
        elif kind == "usage":  # usage 是本次运行的累计值，取最后一次
            for k in TOKEN_KEYS:
                stats[k] = ev.get(k, stats[k])
        elif kind == "result":
            stats["turns"] = ev.get("num_turns", 0)
            stats["result"] = (ev.get("result") or "")[-500:]
        elif kind == "error":
            stats["errors"].append(ev.get("message", ""))
    return stats


def _remove(ws: Path, names: list[str]) -> None:
    for name in names:
        p = ws / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


def _run(cmd: list[str], cwd: Path, timeout: int, env: dict) -> tuple[str, str, bool, int]:
    """返回 (stdout, stderr, 是否超时, 退出码)。超时在 Windows 上杀整棵进程树，agent 起的 bash 不会残留。"""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=env)
    try:
        out, err = proc.communicate(timeout=timeout)
        return out, err, False, proc.returncode
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        out, err = proc.communicate()
        return out or "", err or "", True, proc.returncode


def run_task(task: Task, agent_cmd: list[str], timeout: int, keep: bool = False,
             traj_dir: Path | None = None) -> dict:
    ws = Path(tempfile.mkdtemp(prefix=f"eval-{task.task_id}-"))
    shutil.copytree(task.src, ws, dirs_exist_ok=True)
    _remove(ws, task.hide)
    env = {**os.environ, "PYTHONUTF8": "1"}
    check_cmd = [sys.executable if a == "{python}" else a for a in task.check]
    totals = {k: 0 for k in COUNT_KEYS}
    errors: list[str] = []
    suspicious: list[str] = []
    result, check_out = "", ""
    seconds, timed_out, passed, passed_1, attempts = 0.0, False, False, False, 0
    prompt = task.prompt
    for attempt in range(1, task.attempts + 1):
        attempts = attempt
        _remove(ws, task.tests)
        start = time.monotonic()
        out, _, to, _ = _run([*agent_cmd, "-p", prompt, "--output-format", "stream-json"], ws, timeout, env)
        seconds += time.monotonic() - start
        timed_out = timed_out or to
        if traj_dir is not None:
            (traj_dir / f"{task.task_id}.{attempt}.ndjson").write_text(out, encoding="utf-8")
        stats = parse_stream(out.splitlines())
        for k in COUNT_KEYS:
            totals[k] += stats[k]
        result = stats["result"]
        errors += stats["errors"]
        suspicious += [f"[{attempt}] {s}" for s in stats["suspicious"]]
        for name in task.tests:
            (ws / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(task.src / name, ws / name)
        c_out, c_err, _, rc = _run(check_cmd, ws, 300, env)
        check_out = c_out + c_err
        passed = rc == 0
        if attempt == 1:
            passed_1 = passed
        if passed:
            break
        prompt = task.prompt + "\n\n" + check_out[-4000:] + task.retry_suffix
    row = {"task_id": task.task_id, "passed": passed, "passed_1": passed_1, "attempts": attempts,
           "timed_out": timed_out, "seconds": round(seconds, 1), **totals,
           "contaminated": bool(suspicious), "suspicious": suspicious,
           "result": result, "errors": errors, "check_tail": check_out[-1500:]}
    if keep:
        row["workspace"] = str(ws)
    else:
        shutil.rmtree(ws, ignore_errors=True)
    return row


def summarize(rows: list[dict], prices: dict[str, float] | None = None) -> dict:
    n = len(rows)

    def total(k: str) -> float:
        return sum(r.get(k, r["passed"] if k == "passed_1" else 0) for r in rows)

    def ratio(a: float, b: float) -> float:
        return round(a / b, 3) if b else 0.0

    prompt_tokens = total("input_tokens") + total("cache_read") + total("cache_creation")
    summary = {
        "tasks": n,
        "pass_rate": ratio(total("passed"), n),
        "pass_at_1": ratio(total("passed_1"), n),
        "contaminated": sum(1 for r in rows if r.get("contaminated")),
        "clean_pass_rate": ratio(sum(1 for r in rows if r["passed"] and not r.get("contaminated")), n),
        "clean_pass_at_1": ratio(sum(1 for r in rows if r.get("passed_1", r["passed"]) and not r.get("contaminated")), n),
        "timeouts": int(total("timed_out")),
        "avg_turns": ratio(total("turns"), n),
        "avg_tool_calls": ratio(total("tool_calls"), n),
        "avg_seconds": ratio(total("seconds"), n),
        "edit_error_rate": ratio(total("edit_errors"), total("edit_calls")),
        "edit_fuzzy_rate": ratio(total("edit_fuzzy"), total("edit_calls")),
        "edit_guarded": int(total("edit_guarded")),
        "cache_hit_rate": ratio(total("cache_read"), prompt_tokens),
        "tokens": {k: int(total(k)) for k in TOKEN_KEYS},
    }
    if prices:
        cost = sum(total(k) * prices.get(k, 0.0) for k in TOKEN_KEYS) / 1e6
        summary["cost_usd"] = round(cost, 4)
        summary["cost_per_task_usd"] = round(cost / n, 4) if n else 0.0
    return summary


def aggregate(runs: list[list[dict]], prices: dict[str, float] | None = None) -> dict:
    """多轮结果汇总：每轮的干净通过率、平均值，以及 pass^k（每轮都通过的题目占比，衡量稳定性）。"""
    k = len(runs)
    per_run = [summarize(rows, prices) for rows in runs]
    by_task: dict[str, list[dict]] = {}
    for rows in runs:
        for r in rows:
            by_task.setdefault(r["task_id"], []).append(r)
    complete = {t: rs for t, rs in by_task.items() if len(rs) == k}
    n = len(complete)

    def ok(r: dict, first: bool = False) -> bool:
        passed = r.get("passed_1", r["passed"]) if first else r["passed"]
        return bool(passed) and not r.get("contaminated")

    def mean(key: str) -> float:
        return round(sum(s[key] for s in per_run) / k, 3) if k else 0.0

    def frac(cond) -> float:
        return round(sum(1 for rs in complete.values() if cond(rs)) / n, 3) if n else 0.0

    return {
        "runs": k,
        "tasks": n,
        "pass_rate_mean": mean("clean_pass_rate"),
        "pass_at_1_mean": mean("clean_pass_at_1"),
        "pass_rate_per_run": [s["clean_pass_rate"] for s in per_run],
        "pass_at_1_per_run": [s["clean_pass_at_1"] for s in per_run],
        f"pass^{k}": frac(lambda rs: all(ok(r) for r in rs)),
        f"pass_at_1^{k}": frac(lambda rs: all(ok(r, first=True) for r in rs)),
        "solved_in_any_run": frac(lambda rs: any(ok(r) for r in rs)),
        "contaminated_per_run": [s["contaminated"] for s in per_run],
        "cache_hit_rate_mean": mean("cache_hit_rate"),
        "cost_per_task_usd_mean": mean("cost_per_task_usd") if prices else None,
        "avg_turns_mean": mean("avg_turns"),
        "avg_seconds_mean": mean("avg_seconds"),
        "flaky": sorted(t for t, rs in complete.items() if any(ok(r) for r in rs) and not all(ok(r) for r in rs)),
        "never_solved": sorted(t for t, rs in complete.items() if not any(ok(r) for r in rs)),
    }


def parse_prices(spec: str) -> dict[str, float]:
    """"input=3,output=15,cache_read=0.3,cache_creation=3.75"，单位为美元 / 百万 token。"""
    prices = {}
    for part in filter(None, (s.strip() for s in spec.split(","))):
        key, _, value = part.partition("=")
        key = PRICE_ALIASES.get(key, key)
        if key not in TOKEN_KEYS:
            raise ValueError(f"unknown price key: {key}")
        prices[key] = float(value)
    return prices


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cabbagecode.evals", description=__doc__.splitlines()[0])
    ap.add_argument("--tasks", type=Path, default=None)
    ap.add_argument("--aggregate", type=Path, nargs="+", default=None,
                    help="汇总多轮的结果文件（jsonl），输出平均通过率与 pass^k，不跑任务")
    ap.add_argument("--suite", choices=["dirs", "polyglot"], default="dirs")
    ap.add_argument("--out", type=Path, default=Path("eval-results.jsonl"))
    ap.add_argument("--save-dir", type=Path, default=None, help="保存每题每轮的原始事件流")
    ap.add_argument("--agent-cmd", default="", help="默认用当前解释器跑 -m cabbagecode")
    ap.add_argument("--agent-args", default="", help="追加给 agent 的参数，如 '--max-turns 30'")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="逗号分隔的任务名，只跑这些")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=600, help="单轮 agent 运行的秒数上限")
    ap.add_argument("--price", default="", help="input=3,output=15,cache_read=0.3,cache_creation=3.75")
    ap.add_argument("--keep", action="store_true", help="保留工作区，便于看失败原因")
    args = ap.parse_args(argv)

    if args.aggregate:
        runs = [[json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
                for p in args.aggregate]
        print(json.dumps(aggregate(runs, parse_prices(args.price)), ensure_ascii=False, indent=2))
        return 0
    if args.tasks is None:
        ap.error("--tasks is required unless --aggregate is given")

    agent_cmd = shlex.split(args.agent_cmd) if args.agent_cmd else [sys.executable, "-m", "cabbagecode"]
    agent_cmd += shlex.split(args.agent_args)
    tasks = (load_polyglot if args.suite == "polyglot" else load_tasks)(args.tasks)
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in tasks if t.task_id in wanted]
    tasks = tasks[: args.limit or None]
    if args.save_dir:
        args.save_dir.mkdir(parents=True, exist_ok=True)

    def one(task: Task) -> dict:
        row = run_task(task, agent_cmd, args.timeout, args.keep, args.save_dir)
        mark = "PASS" if row["passed"] else ("TIMEOUT" if row["timed_out"] else "FAIL")
        print(f"[{mark}] {task.task_id} attempts={row['attempts']} {row['seconds']}s turns={row['turns']}",
              file=sys.stderr, flush=True)
        return row

    with ThreadPoolExecutor(max_workers=max(args.jobs, 1)) as pool:
        rows = list(pool.map(one, tasks))
    with args.out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(summarize(rows, parse_prices(args.price)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
