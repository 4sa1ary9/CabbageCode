"""影子 git 仓库：给检查点存整个工作树，连 Bash 命令造成的改动也能回退。

独立的 GIT_DIR 放在会话目录里，工作树指向项目根目录，不碰用户自己的 .git。
做法同 Gemini CLI 的 checkpointing、Cline 的影子 git 和 opencode 的 snapshot。
项目文件太多或没装 git 时不启用，FileHistory 退回逐文件备份。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

MAX_FILES = 20_000


class ShadowRepo:
    def __init__(self, git_dir: Path, work_tree: Path) -> None:
        self.git_dir = git_dir
        self.work_tree = work_tree
        self.ok = shutil.which("git") is not None and self._init()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(
            ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.work_tree}", *args],
            capture_output=True, timeout=120, check=True, env=env,
        )

    def _init(self) -> bool:
        try:
            if not (self.git_dir / "HEAD").exists():
                self.git_dir.mkdir(parents=True, exist_ok=True)
                self._git("init", "-q")
                no_hooks = self.git_dir / "no-hooks"
                no_hooks.mkdir(exist_ok=True)
                # 这是工具内部的存档仓库：固定身份、不签名、不跑用户全局配置的钩子、按字节存文件
                for key, value in (("user.name", "cabbagecode"), ("user.email", "checkpoint@cabbagecode.local"),
                                   ("commit.gpgsign", "false"), ("core.hooksPath", str(no_hooks)),
                                   ("core.autocrlf", "false"), ("core.quotepath", "false")):
                    self._git("config", key, value)
                (self.git_dir / "info").mkdir(exist_ok=True)
                (self.git_dir / "info" / "exclude").write_text(".cabbagecode/\n.git/\n", encoding="utf-8")
            listed = self._git("ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout
            return listed.count(b"\0") <= MAX_FILES
        except (OSError, subprocess.SubprocessError):
            return False

    def snapshot(self, message: str) -> str | None:
        """提交当前工作树（遵守项目的 .gitignore），返回提交号；失败返回 None。"""
        try:
            self._git("add", "-A")
            self._git("commit", "-q", "--allow-empty", "-m", message or "checkpoint")
            return self._git("rev-parse", "HEAD").stdout.decode().strip()
        except (OSError, subprocess.SubprocessError):
            return None

    def restore(self, commit: str) -> list[str]:
        """把工作树恢复到 commit，返回改动过的文件。恢复前先存一份当前状态，回退本身也能找回。"""
        current = self.snapshot("before rewind")
        if current is None:
            return []
        try:
            out = self._git("diff", "--name-status", "-z", "--no-renames", commit, current).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        parts = [p for p in out.decode("utf-8", "replace").split("\0") if p]
        changed = []
        for status, rel in zip(parts[0::2], parts[1::2]):
            target = self.work_tree / rel
            if status == "A":  # 快照之后才出现的文件
                target.unlink(missing_ok=True)
            changed.append(str(target))
        try:
            self._git("restore", f"--source={commit}", "--worktree", "--", ".")
        except (OSError, subprocess.SubprocessError):
            return []
        return changed
