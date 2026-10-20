from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from cabbagecode.tools import diagnostics
from cabbagecode.tools.base import Tool, ToolResult
from cabbagecode.tools.diff import build_diff

if TYPE_CHECKING:
    from cabbagecode.tools.file_state_cache import FileStateCache


# 精确匹配失败时逐级放宽，思路同 Codex apply_patch 的 seek_sequence 与 Gemini CLI 的编辑回退；
# 每一级都要求唯一命中才写入，多处命中就让模型补上下文。
_PUNCT = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u2026": "...",
})
_LEVELS = (
    ("ignoring trailing whitespace", lambda s: s.rstrip()),
    ("ignoring leading/trailing whitespace", lambda s: s.strip()),
    ("ignoring whitespace and Unicode punctuation", lambda s: s.translate(_PUNCT).strip()),
)


def _split(text: str) -> list[str]:
    lines = text.split("\n")
    return lines[:-1] if len(lines) > 1 and lines[-1] == "" else lines


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _tolerant_match(lines: list[str], old_lines: list[str]) -> tuple[int | None, str, str]:
    """返回 (起始行号, 命中的级别, 错误信息)；起始行号和错误信息至多一个有值。"""
    k = len(old_lines)
    for label, norm in _LEVELS:
        target = [norm(s) for s in old_lines]
        normed = [norm(s) for s in lines]
        hits = [i for i in range(len(lines) - k + 1) if normed[i:i + k] == target]
        if len(hits) == 1:
            return hits[0], label, ""
        if hits:
            return None, label, (f"Error: old_string matches {len(hits)} locations ({label}); "
                                 "include more surrounding lines so it is unique")
    return None, "", ""


def _reindent(file_lines: list[str], old_lines: list[str], new_lines: list[str]) -> list[str]:
    """模型写的 old_string 少了或多了一层缩进时，new_string 按同样的差值调整（Aider 的做法）。"""
    f = next((s for s in file_lines if s.strip()), None)
    o = next((s for s in old_lines if s.strip()), None)
    if f is None or o is None or _indent(f) == _indent(o):
        return new_lines
    fi, oi = _indent(f), _indent(o)
    if fi.endswith(oi):
        extra = fi[: len(fi) - len(oi)]
        return [extra + s if s.strip() else s for s in new_lines]
    if oi.endswith(fi):
        cut = len(oi) - len(fi)
        return [s[cut:] if not s[:cut].strip() else s for s in new_lines]
    return new_lines


def _closest(lines: list[str], old_lines: list[str]) -> str:
    """找不到时给出文件里最像的那几行和行号，模型下一次就能照着写准。"""
    k = max(len(old_lines), 1)
    target = "\n".join(s.strip() for s in old_lines)
    best, best_i = 0.0, 0
    for i in range(max(len(lines) - k + 1, 1)):
        ratio = difflib.SequenceMatcher(None, target, "\n".join(s.strip() for s in lines[i:i + k])).ratio()
        if ratio > best:
            best, best_i = ratio, i
    if best < 0.5:
        return ""
    end = min(best_i + k, len(lines))
    snippet = "\n".join(f"{n + 1:>6}\t{lines[n]}" for n in range(best_i, end))
    return f"\nMost similar lines in the file ({best:.0%} similar):\n{snippet}"


class Params(BaseModel):
    file_path: str = Field(description="Path to the file to edit")
    old_string: str = Field(description="The text to replace; should match exactly once in the file")
    new_string: str = Field(description="The replacement text")
    replace_all: bool = Field(default=False, description="Replace every exact occurrence of old_string")


class EditFile(Tool):
    name = "EditFile"
    description = (
        "Replace text in a file. old_string should appear exactly once; small differences in whitespace, "
        "indentation or quote characters are tolerated when the match is still unique. "
        "Set replace_all to replace every exact occurrence.\n"
        "You MUST read the file with ReadFile before editing. This tool will fail otherwise."
    )
    params_model = Params
    category = "write"


    def __init__(self, file_history: Any = None, file_state_cache: FileStateCache | None = None) -> None:
        self.file_history = file_history
        self._state_cache = file_state_cache


    async def execute(self, params: Params) -> ToolResult:
        if self.file_history is not None:
            self.file_history.track_edit(params.file_path)

        path = Path(params.file_path)
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {params.file_path}", is_error=True)

        if self._state_cache:
            resolved = str(path.resolve())
            ok, err_msg = self._state_cache.check(resolved)
            if not ok:
                return ToolResult(output=err_msg, is_error=True)

        try:
            # newline="" 保留原始换行；否则 Windows 上写回时会把 LF 文件整体改成 CRLF
            with open(path, encoding="utf-8", newline="") as f:
                raw = f.read()
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        crlf = "\r\n" in raw
        content = raw.replace("\r\n", "\n")
        old = params.old_string.replace("\r\n", "\n")
        new = params.new_string.replace("\r\n", "\n")
        if not old:
            return ToolResult(output="Error: old_string must not be empty", is_error=True)

        note = ""
        count = content.count(old)
        if count == 1 or (count > 1 and params.replace_all):
            new_content = content.replace(old, new) if params.replace_all else content.replace(old, new, 1)
        elif count > 1:
            return ToolResult(
                output=f"Error: old_string found {count} times; add surrounding context or set replace_all=true",
                is_error=True,
            )
        elif os.environ.get("CABBAGECODE_EDIT_TOLERANCE", "1") == "0":  # 对照组：只做精确匹配
            return ToolResult(output="Error: old_string not found in file", is_error=True)
        else:
            lines, old_lines, new_lines = content.split("\n"), _split(old), _split(new)
            start, label, err = _tolerant_match(lines, old_lines)
            if err:
                return ToolResult(output=err, is_error=True)
            if start is None:
                return ToolResult(output="Error: old_string not found in file" + _closest(lines, old_lines),
                                  is_error=True)
            k = len(old_lines)
            if label != _LEVELS[0][0]:
                new_lines = _reindent(lines[start:start + k], old_lines, new_lines)
            new_content = "\n".join(lines[:start] + new_lines + lines[start + k:])
            note = f" (matched after {label})"

        broken_after = diagnostics.syntax_error(new_content, str(path)) if diagnostics.enabled() else None
        if broken_after and diagnostics.syntax_error(content, str(path)) is None:
            # 原本能编译、这次编辑改坏了：撤销，让模型照着错误位置重写
            return ToolResult(
                output=(f"Error: this edit would introduce a syntax error, so {params.file_path} was not changed.\n"
                        f"{broken_after}\nFix new_string and try again."),
                is_error=True,
            )

        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new_content.replace("\n", "\r\n") if crlf else new_content)
            if self._state_cache:
                self._state_cache.update(str(path.resolve()))
        except Exception as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)

        diff = build_diff(content, new_content)
        addition_word = "addition" if diff.additions == 1 else "additions"
        removal_word = "removal" if diff.removals == 1 else "removals"
        summary = (
            f"Updated {params.file_path} with {diff.additions} {addition_word} "
            f"and {diff.removals} {removal_word}{note}"
        )
        warnings_text = ""
        if broken_after:
            warnings_text += f"\nWarning: the file still has a syntax error:\n{broken_after}"
        if diagnostics.enabled() and (findings := diagnostics.lint(path)):
            warnings_text += f"\nLint:\n{findings}"
        return ToolResult(output=f"{summary}{warnings_text}\n{diff.text}")
