import os
import subprocess
from pathlib import Path
from typing import Dict, List

from . import BridgeError


def resolve_read_path(value: str, roots: List[Path], require_file: bool = True) -> Path:
    path = Path(value).resolve()
    if not any(path.is_relative_to(root) for root in roots):
        raise BridgeError('path_outside_root', f"Path is outside configured read roots: {path}")
    if require_file and not path.is_file():
        raise FileNotFoundError(f"Required file does not exist: {path}")

    return path


def source_revision(repository: Path) -> Dict:
    try:
        top = subprocess.run(
            ['git', '--no-optional-locks', '-C', str(repository), 'rev-parse', '--show-toplevel'],
            capture_output=True,
            text=True,
            timeout=3,
            check=False
        )
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != repository.resolve():
            return {'commit': None, 'tracked_changes': None}
    except (OSError, subprocess.TimeoutExpired):
        return {'commit': None, 'tracked_changes': None}

    result = {}
    for name, arguments in (
        ('commit', ['rev-parse', 'HEAD']),
        ('tracked_changes', ['status', '--porcelain', '--untracked-files=no'])
    ):
        try:
            completed = subprocess.run(
                ['git', '--no-optional-locks', '-C', str(repository), *arguments],
                capture_output=True,
                text=True,
                timeout=3,
                check=False
            )
            result[name] = completed.stdout.strip() if completed.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            result[name] = None

    return result


def validate_sidecar(board: Path, writable_roots: List[Path]) -> Dict:
    destination = board.with_suffix('.inspection.json')
    lock = board.with_suffix('.inspection.lock')
    if destination.is_symlink() or not any(destination.resolve().is_relative_to(root) for root in writable_roots):
        raise BridgeError('sidecar_not_authorized', f"Inspection would write outside configured writable data roots: {destination}")
    if destination.exists() and not destination.is_file():
        raise BridgeError('invalid_sidecar', f"Sidecar destination is not a regular file: {destination}")
    if not os.access(destination.parent, os.W_OK | os.X_OK):
        raise BridgeError('sidecar_not_writable', f"Sidecar directory is not writable: {destination.parent}")
    if lock.is_symlink() or (lock.exists() and not lock.is_file()):
        raise BridgeError('invalid_lock', f"Board lock must be a regular non-symlink file: {lock}")

    return {'path': str(destination), 'lock_path': str(lock), 'authorized': True, 'directory_access': True}
