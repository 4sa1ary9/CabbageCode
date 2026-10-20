
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from cabbagecode.filehistory.shadow import ShadowRepo

MAX_SNAPSHOTS = 100


@dataclass
class Backup:
    backup_path: str
    version: int
    timestamp: float


@dataclass
class Snapshot:
    message_index: int
    user_text: str
    backups: dict[str, Backup] = field(default_factory=dict)
    timestamp: float = 0.0
    # 影子 git 里整个工作树的提交号；有它时 rewind 恢复整棵树（含 Bash 改动）
    shadow_commit: str | None = None


class FileHistory:

    def __init__(self, base_dir: str, session_id: str, shadow: bool = False) -> None:
        self._session_dir = Path(base_dir) / ".cabbagecode" / "file-history" / session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._tracked: dict[str, int] = {}
        self._snapshots: list[Snapshot] = []
        self._lock = threading.Lock()
        self._shadow: ShadowRepo | None = None
        if shadow:
            repo = ShadowRepo(self._session_dir / "shadow.git", Path(base_dir))
            if repo.ok:
                self._shadow = repo
                # 基线：会话开始时的工作树，能回退到第一轮改动之前
                sha = repo.snapshot("session start")
                if sha:
                    self._snapshots.append(Snapshot(message_index=0, user_text="(session start)",
                                                    timestamp=time.time(), shadow_commit=sha))

    def _backup_name(self, file_path: str, version: int) -> str:
        h = hashlib.sha256(file_path.encode()).hexdigest()[:16]
        return f"{h}@v{version}"

    def track_edit(self, path: str) -> None:
        with self._lock:
            abs_path = str(Path(path).resolve())
            ver = self._tracked.get(abs_path, 0)
            new_ver = ver + 1

            try:
                data = Path(abs_path).read_bytes()
                bp = self._session_dir / self._backup_name(abs_path, new_ver)
                bp.write_bytes(data)
            except FileNotFoundError:
                pass

            self._tracked[abs_path] = new_ver

    def make_snapshot(self, msg_index: int, user_text: str) -> None:
        with self._lock:
            backups: dict[str, Backup] = {}
            for path, ver in self._tracked.items():
                bp = self._session_dir / self._backup_name(path, ver)
                if not bp.exists():
                    try:
                        data = Path(path).read_bytes()
                        bp.write_bytes(data)
                    except (FileNotFoundError, OSError):
                        pass
                backups[path] = Backup(
                    backup_path=str(bp), version=ver, timestamp=time.time(),
                )

            shadow_commit = self._shadow.snapshot(user_text[:60]) if self._shadow else None
            self._snapshots.append(Snapshot(
                message_index=msg_index,
                user_text=user_text,
                backups=backups,
                timestamp=time.time(),
                shadow_commit=shadow_commit,
            ))
            if len(self._snapshots) > MAX_SNAPSHOTS:
                self._snapshots = self._snapshots[-MAX_SNAPSHOTS:]

    def get_snapshots(self) -> list[Snapshot]:
        with self._lock:
            return list(self._snapshots)

    def has_snapshots(self) -> bool:
        with self._lock:
            return len(self._snapshots) > 0

    def rewind(self, snapshot_index: int) -> list[str]:
        with self._lock:
            if snapshot_index < 0 or snapshot_index >= len(self._snapshots):
                return []

            target = self._snapshots[snapshot_index]
            changed: list[str] = []

            if target.shadow_commit and self._shadow is not None:
                changed = self._shadow.restore(target.shadow_commit)
                self._snapshots = self._snapshots[: snapshot_index + 1]
                for file_path, backup in target.backups.items():
                    self._tracked[file_path] = backup.version
                return changed

            for file_path, backup in target.backups.items():
                bp = Path(backup.backup_path)
                try:
                    backup_data = bp.read_bytes()
                except FileNotFoundError:
                    fp = Path(file_path)
                    if fp.exists():
                        fp.unlink()
                        changed.append(file_path)
                    continue

                fp = Path(file_path)
                try:
                    current_data = fp.read_bytes()
                except FileNotFoundError:
                    current_data = b""

                if current_data != backup_data:
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_bytes(backup_data)
                    changed.append(file_path)

            self._snapshots = self._snapshots[: snapshot_index + 1]
            for file_path, backup in target.backups.items():
                self._tracked[file_path] = backup.version

            return changed
