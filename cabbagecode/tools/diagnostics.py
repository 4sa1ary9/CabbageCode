"""编辑后诊断：改完 .py 文件就查语法，把错误回灌给模型。

思路来自 SWE-agent 的 lint 守卫（编辑引入新错误就撤销）和 Aider 的 auto-lint。
设 CABBAGECODE_EDIT_GUARD=0 可关闭，便于评测时做对照组。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import warnings
from pathlib import Path


def enabled() -> bool:
    return os.environ.get("CABBAGECODE_EDIT_GUARD", "1") != "0"


def syntax_error(source: str, filename: str) -> str | None:
    """返回语法错误描述；不是 .py 文件或没有错误时返回 None。"""
    if not filename.endswith(".py"):
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(source, filename, "exec")
    except SyntaxError as e:
        where = f"line {e.lineno}" + (f", column {e.offset}" if e.offset else "")
        line = (e.text or "").rstrip()
        return f"SyntaxError at {where}: {e.msg}" + (f"\n    {line}" if line else "")
    except ValueError as e:  # 例如源码里有空字节
        return f"SyntaxError: {e}"
    return None


def lint(path: Path) -> str:
    """装了 ruff 时只查致命问题（语法、未定义名字等）；没装就跳过。"""
    ruff = shutil.which("ruff")
    if not ruff or path.suffix != ".py":
        return ""
    try:
        proc = subprocess.run(
            [ruff, "check", "--select", "E9,F63,F7,F82", "--output-format", "concise",
             "--no-cache", "--quiet", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip()
