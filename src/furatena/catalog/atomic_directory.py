"""Last-known-good directory transactions for freeze and static deployment output."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path


class AtomicDirectoryTransaction:
    """Build in a sibling tree and atomically promote only completed output."""

    def __init__(self, target: Path, *, operation: str) -> None:
        self.target = target.expanduser().resolve()
        self.operation = operation
        token = uuid.uuid4().hex
        self.staging = self.target.parent / f".{self.target.name}.{operation}.pending.{token}"
        self.backup = self.target.parent / f".{self.target.name}.{operation}.backup.{token}"
        self._committed = False

    def prepare(self) -> Path:
        self.reconcile(self.target, operation=self.operation)
        self.target.parent.mkdir(parents=True, exist_ok=True)
        if self.target.is_dir():
            shutil.copytree(self.target, self.staging, copy_function=shutil.copy2)
        else:
            self.staging.mkdir(parents=True)
        return self.staging

    def commit(self) -> None:
        if not self.staging.is_dir():
            raise RuntimeError(f"{self.operation} staging directory is missing: {self.staging}")
        if self.target.exists():
            os.replace(self.target, self.backup)
        try:
            os.replace(self.staging, self.target)
        except BaseException:
            if self.backup.exists() and not self.target.exists():
                os.replace(self.backup, self.target)
            raise
        shutil.rmtree(self.backup, ignore_errors=True)
        self._committed = True

    def cleanup(self) -> None:
        shutil.rmtree(self.staging, ignore_errors=True)
        if self._committed:
            shutil.rmtree(self.backup, ignore_errors=True)

    @classmethod
    def reconcile(cls, target: Path, *, operation: str) -> bool:
        """Restore an orphaned backup after a worker died during promotion."""
        target = target.expanduser().resolve()
        parent = target.parent
        if not parent.is_dir():
            return False
        backups = sorted(
            parent.glob(f".{target.name}.{operation}.backup.*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        restored = False
        if not target.exists() and backups:
            os.replace(backups.pop(0), target)
            restored = True
        for path in backups:
            shutil.rmtree(path, ignore_errors=True)
        for path in parent.glob(f".{target.name}.{operation}.pending.*"):
            shutil.rmtree(path, ignore_errors=True)
        return restored
