import json
import sys
import textwrap

from cabbagecode.evals import load_polyglot, load_tasks, parse_prices, parse_stream, run_task, summarize

NL = chr(10)


def _ev(**kw) -> str:
    return json.dumps(kw)


def test_parse_stream_counts_edits_errors_and_last_usage():
    lines = [
        _ev(type="tool_use", tool_name="EditFile", tool_id="1", args={}),
        _ev(type="tool_result", tool_name="EditFile", tool_id="1", output="Error: old_string not found",
            is_error=True, elapsed=0.1),
        _ev(type="tool_result", tool_name="EditFile", tool_id="2",
            output="Updated a.py with 1 addition and 1 removal (matched after ignoring trailing whitespace)",
            is_error=False, elapsed=0.1),
        _ev(type="tool_result", tool_name="Bash", tool_id="3", output="boom", is_error=True, elapsed=0.1),
        _ev(type="usage", input_tokens=10, output_tokens=5, cache_read=0, cache_creation=40),
        _ev(type="usage", input_tokens=20, output_tokens=9, cache_read=80, cache_creation=40),
        "not json",
        _ev(type="result", result="done", num_turns=3),
    ]
    s = parse_stream(lines)
    assert (s["tool_calls"], s["tool_errors"]) == (3, 2)
    assert (s["edit_calls"], s["edit_errors"], s["edit_fuzzy"]) == (2, 1, 1)
    assert (s["input_tokens"], s["output_tokens"], s["cache_read"], s["cache_creation"]) == (20, 9, 80, 40)
    assert s["turns"] == 3 and s["result"] == "done"


def test_summarize_rates_and_cost():
    base = dict(turns=2, tool_calls=4, seconds=10.0, timed_out=False, edit_calls=2,
                input_tokens=100, output_tokens=50, cache_read=300, cache_creation=100)
    rows = [dict(base, passed=True, edit_errors=0), dict(base, passed=False, edit_errors=1)]
    s = summarize(rows, parse_prices("input=3,output=15,cache_read=0.3,cache_creation=3.75"))
    assert s["pass_rate"] == 0.5
    assert s["edit_error_rate"] == 0.25
    assert s["cache_hit_rate"] == 0.6  # 600 / (200 + 600 + 200)
    assert s["cost_usd"] == 0.003  # (200*3 + 100*15 + 600*0.3 + 200*3.75) / 1e6


FAKE_AGENT = textwrap.dedent('''
    import json, pathlib, sys
    ws = pathlib.Path.cwd()
    if (ws / ".meta").exists() or (ws / "task.json").exists():
        sys.exit(3)  # 隐藏文件没删掉：故意不写答案，让测试失败
    nl = chr(10)
    (ws / "add.py").write_text("def add(a, b):" + nl + "    return a + b" + nl, encoding="utf-8")
    for ev in [
        {"type": "tool_use", "tool_name": "WriteFile", "tool_id": "w1", "args": {}},
        {"type": "tool_result", "tool_name": "WriteFile", "tool_id": "w1", "output": "ok",
         "is_error": False, "elapsed": 0.01},
        {"type": "usage", "input_tokens": 7, "output_tokens": 3, "cache_read": 11, "cache_creation": 2},
        {"type": "result", "result": "implemented add", "num_turns": 2},
    ]:
        print(json.dumps(ev), flush=True)
''')


def test_run_task_end_to_end_with_fake_agent(tmp_path):
    task_dir = tmp_path / "add"
    (task_dir / ".meta").mkdir(parents=True)
    (task_dir / ".meta" / "solution.py").write_text("def add(a, b): return a + b", encoding="utf-8")
    (task_dir / "test_add.py").write_text(
        "from add import add" + NL + NL + "def test_add():" + NL + "    assert add(1, 2) == 3" + NL,
        encoding="utf-8",
    )
    (task_dir / "task.json").write_text(json.dumps({
        "prompt": "Implement add() in add.py",
        "check": ["{python}", "-m", "pytest", "-q", "test_add.py"],
        "hide": [".meta"],
    }), encoding="utf-8")
    agent = tmp_path / "fake_agent.py"
    agent.write_text(FAKE_AGENT, encoding="utf-8")

    [task] = load_tasks(tmp_path)
    row = run_task(task, [sys.executable, str(agent)], timeout=60)

    assert row["passed"], row["check_tail"]
    assert (row["edit_calls"], row["edit_errors"]) == (1, 0)
    assert (row["cache_read"], row["turns"]) == (11, 2)


FAKE_POLYGLOT_AGENT = textwrap.dedent('''
    import json, pathlib, sys
    ws = pathlib.Path.cwd()
    prompt = sys.argv[sys.argv.index("-p") + 1]
    if (ws / "add_test.py").exists() or (ws / ".meta").exists():
        sys.exit(3)  # 测试或参考答案没藏起来
    op = "+" if "See the testing errors above" in prompt else "-"  # 首轮故意写错，拿到测试输出再改对
    nl = chr(10)
    (ws / "add.py").write_text("def add(a, b):" + nl + "    return a " + op + " b" + nl, encoding="utf-8")
    print(json.dumps({"type": "result", "result": "ok", "num_turns": 1}), flush=True)
''')


def test_polyglot_two_attempts_hide_tests_and_feed_failures(tmp_path):
    ex = tmp_path / "practice" / "add"
    (ex / ".docs").mkdir(parents=True)
    (ex / ".meta").mkdir()
    (ex / ".docs" / "instructions.md").write_text("Implement add(a, b)." + NL, encoding="utf-8")
    (ex / ".meta" / "config.json").write_text(json.dumps({"files": {
        "solution": ["add.py"], "test": ["add_test.py"], "example": [".meta/example.py"]}}), encoding="utf-8")
    (ex / ".meta" / "example.py").write_text("def add(a, b): return a + b", encoding="utf-8")
    (ex / "add.py").write_text("def add(a, b):" + NL + "    pass" + NL, encoding="utf-8")
    (ex / "add_test.py").write_text(NL.join([
        "import unittest", "from add import add", "", "class T(unittest.TestCase):",
        "    def test_add(self):", "        self.assertEqual(add(1, 2), 3)", ""]), encoding="utf-8")
    agent = tmp_path / "fake_agent.py"
    agent.write_text(FAKE_POLYGLOT_AGENT, encoding="utf-8")

    [task] = load_polyglot(tmp_path / "practice")
    assert "Use the above instructions to modify the supplied files: add.py" in task.prompt
    assert "Do not access the internet" in task.prompt
    row = run_task(task, [sys.executable, str(agent)], timeout=60)

    assert (row["passed_1"], row["passed"], row["attempts"]) == (False, True, 2), row["check_tail"]
    assert summarize([row])["pass_at_1"] == 0.0



def test_audit_flags_network_and_whole_disk_search_only():
    s = parse_stream([
        _ev(type="tool_use", tool_name="Bash", args={"command": "curl -s https://raw.githubusercontent.com/x/y_test.py"}),
        _ev(type="tool_use", tool_name="Bash", args={"command": 'find /c/Users/someone -iname "*paasio*"'}),
        _ev(type="tool_use", tool_name="Bash", args={"command": 'find "C:/Users/me/AppData/Local/Temp/eval-x-1/.docs" -type f'}),
        _ev(type="tool_use", tool_name="Bash", args={"command": "cd /tmp && python -c 'print(1)'"}),
        _ev(type="tool_use", tool_name="ReadFile", args={"file_path": "solution.py"}),
    ])
    assert len(s["suspicious"]) == 2
    assert s["suspicious"][0].startswith("Bash: curl")


def test_summarize_counts_contaminated_passes_as_failures():
    base = dict(turns=1, tool_calls=1, seconds=1.0, timed_out=False, edit_calls=0, edit_errors=0,
                input_tokens=0, output_tokens=0, cache_read=0, cache_creation=0)
    rows = [dict(base, passed=True, passed_1=True, contaminated=True),
            dict(base, passed=True, passed_1=False, contaminated=False)]
    s = summarize(rows)
    assert (s["pass_rate"], s["clean_pass_rate"], s["clean_pass_at_1"], s["contaminated"]) == (1.0, 0.5, 0.0, 1)


def test_aggregate_reports_mean_pass_k_and_flaky_tasks():
    from cabbagecode.evals import aggregate
    base = dict(turns=1, tool_calls=1, seconds=1.0, timed_out=False, edit_calls=0, edit_errors=0,
                input_tokens=0, output_tokens=0, cache_read=0, cache_creation=0, contaminated=False)
    run1 = [dict(base, task_id="a", passed=True, passed_1=True), dict(base, task_id="b", passed=True, passed_1=False)]
    run2 = [dict(base, task_id="a", passed=True, passed_1=False), dict(base, task_id="b", passed=False, passed_1=False)]
    agg = aggregate([run1, run2])
    assert (agg["pass_rate_mean"], agg["pass^2"], agg["pass_at_1^2"], agg["solved_in_any_run"]) == (0.75, 0.5, 0.0, 1.0)
    assert agg["flaky"] == ["b"] and agg["never_solved"] == []
