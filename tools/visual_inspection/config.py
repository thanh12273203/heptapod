import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, Union

from . import BridgeError


def _absolute(path: Union[str, Path]) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise BridgeError('invalid_config', f"Configured paths must be absolute: {candidate}")

    return Path(os.path.abspath(candidate))


def _contained(path: Path, roots: Tuple[Path, ...]) -> Path:
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise BridgeError('path_outside_root', f"Path is outside the configured roots: {path}")

    return resolved


@dataclass(frozen=True)
class BridgeConfig:
    repository: Path
    python: Path
    data_roots: Tuple[Path, ...]
    artifact_root: Path
    timeout_seconds: float = 60.0
    writable_data_roots: Tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        repository = _absolute(self.repository).resolve()
        # Preserve the venv executable symlink: resolving it loses pyvenv.cfg.
        python = _absolute(self.python)
        artifact_root = _absolute(self.artifact_root).resolve()
        data_roots = tuple(_absolute(root).resolve() for root in self.data_roots)
        writable_data_roots = tuple(_absolute(root).resolve() for root in self.writable_data_roots)
        if not repository.is_dir() or not (repository / 'src' / 'inspection' / '__init__.py').is_file():
            raise BridgeError('invalid_repository', f"Missing visual-inspection package under: {repository}")
        if not python.is_file() or not os.access(python, os.X_OK):
            raise BridgeError('invalid_interpreter', f"Python interpreter is missing or not executable: {python}")
        if not data_roots or any(not root.is_dir() for root in data_roots):
            raise BridgeError('invalid_data_roots', "Configure at least one existing data directory.")
        if any(not root.is_dir() or not any(root.is_relative_to(read) for read in data_roots) for root in writable_data_roots):
            raise BridgeError('invalid_write_roots', "Writable data roots must be existing directories inside allowed data roots.")
        if artifact_root.exists() and not artifact_root.is_dir():
            raise BridgeError('invalid_artifact_root', f"Artifact root is not a directory: {artifact_root}")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise BridgeError('invalid_timeout', "Timeout must be a finite positive number of seconds.")

        object.__setattr__(self, 'repository', repository)
        object.__setattr__(self, 'python', python)
        object.__setattr__(self, 'data_roots', data_roots)
        object.__setattr__(self, 'artifact_root', artifact_root)
        object.__setattr__(self, 'writable_data_roots', writable_data_roots)

    def input_path(self, value: Union[str, Path]) -> Path:
        path = _contained(self.repository / value, self.data_roots)
        if not path.is_file():
            raise BridgeError('missing_input', f"Input file does not exist: {path}")

        return path

    def output_path(self, value: Union[str, Path]) -> Path:
        path = _contained(self.artifact_root / value, (self.artifact_root,))
        if path == self.artifact_root:
            raise BridgeError('invalid_output', "Output must be below the artifact root.")

        return path
