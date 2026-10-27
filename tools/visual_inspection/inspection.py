import hashlib
import json
import os
import stat
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from . import (
    BridgeError,
    load_upstream_config,
    resolve_read_path,
    source_revision,
    validate_result_schema,
    validate_sidecar,
    write_json_atomic
)


@contextmanager
def board_lock(board: Path) -> Iterator[Path]:
    import fcntl

    path = board.with_suffix('.inspection.lock')
    if path.is_symlink():
        raise BridgeError('invalid_lock', f"Board lock must not be a symlink: {path}")

    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BridgeError('invalid_lock', f"Board lock must be a regular file: {path}")

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BridgeError('board_busy', f"Another harness inspection owns the board lock: {board}") from error

        yield path
    finally:
        # Keep the inode on disk: unlinking it can let two callers lock different
        # inodes for the same board. Closing releases ownership even after failure.
        os.close(descriptor)


def _sidecar_state(path: Path) -> Optional[Tuple]:
    if not path.exists():
        return None

    digest = hashlib.sha256()
    with path.open('rb') as handle:
        before = os.fstat(handle.fileno())
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)

        after = os.fstat(handle.fileno())

    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise BridgeError('result_changed', "Sidecar changed while its state was being checked.")

    return (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, digest.hexdigest())


def _scope_inputs(config: object, read_roots: List[Path], data_roots: List[Path]) -> None:
    for value in (config.checkpoint_path, config.skipped_segments_path):
        resolve_read_path(value, read_roots)

    resolve_read_path(config.baseline_path, data_roots)
    for name in ('manifest.json', 'ae_threshold.npy', 'pw_threshold.npy'):
        resolve_read_path(str(Path(config.calibration_dir) / name), read_roots)


def run_board_inspection(parameters: Dict, run_directory: Path) -> Dict:
    repository = Path.cwd().resolve()
    data_roots = [Path(value).resolve() for value in parameters['data_roots']]
    writable_roots = [Path(value).resolve() for value in parameters['writable_data_roots']]
    read_roots = [repository, *data_roots]
    context = {
        'schema_version': 'visual-inspection-context/1',
        'source': source_revision(repository),
        'config_path': str(repository / parameters.get('config_path', 'configs/inspection.yaml')),
        'force_requested': parameters.get('force', False),
        'stage': 'configuration'
    }
    context_path = run_directory / 'inspection_context.json'
    write_json_atomic(context_path, context)
    try:
        config_path = resolve_read_path(context['config_path'], read_roots)
        config = load_upstream_config(config_path)
        context['resolved_config'] = asdict(config)
        context['stage'] = 'path_policy'
        write_json_atomic(context_path, context)
        board = resolve_read_path(str(repository / parameters.get('board_path', config.board_path)), data_roots)
        if board.suffix.lower() != '.npy':
            raise BridgeError('invalid_board_path', "Inspection input must be a processed .npy board.")

        sidecar = Path(validate_sidecar(board, writable_roots)['path'])
        context.update(board_path=str(board), sidecar_path=str(sidecar), lock_path=str(board.with_suffix('.inspection.lock')))
        context['stage'] = 'input_scope'
        write_json_atomic(context_path, context)
        _scope_inputs(config, read_roots, data_roots)

        import torch

        from src.inspection import inspect_board
        from src.utils import InspectionResults

        if config.device is None:
            config.device = 'cuda' if torch.cuda.is_available() else 'cpu'

        context.update(resolved_config=asdict(config), device=config.device, mode='legacy', stage='lock')
        write_json_atomic(context_path, context)
        with board_lock(board):
            validate_sidecar(board, writable_roots)
            before = _sidecar_state(sidecar)
            context['stage'] = 'upstream_inspection'
            write_json_atomic(context_path, context)
            result = inspect_board(config, board_path=str(board), force=context['force_requested'])
            context['stage'] = 'result_validation'
            write_json_atomic(context_path, context)
            validate_sidecar(board, writable_roots)
            after = _sidecar_state(sidecar)
            if after is None:
                raise BridgeError('missing_sidecar', "Upstream inspection did not persist its result.")

            validate_result_schema(json.loads(sidecar.read_text()))
            persisted = InspectionResults.load_json(str(sidecar))
            payload = persisted.to_dict()
            # JSON round-tripping can convert coordinate tuples to lists.
            expected = json.loads(json.dumps(result.to_dict(), allow_nan=False))
            if payload != expected or after != _sidecar_state(sidecar):
                raise BridgeError('result_changed', "Persisted result differs from this inspection; another writer may be active.")
            if Path(payload['inspected_path']).resolve() != board:
                raise BridgeError('invalid_result', "Upstream result identifies a different board.")

            result_path = run_directory / 'inspection.json'
            write_json_atomic(result_path, payload)
            observation = 'created' if before is None else 'unchanged' if before == after else 'changed'
            compact = {
                'schema_version': 'visual-inspection-execution/1',
                'upstream_schema_version': payload.get('schema_version', 1),
                'mode': context['mode'],
                'summary': persisted.summary(),
                'pw_flags': payload['pw_flags'],
                'ae_flags': payload['ae_flags'],
                'hybrid_flags': payload['hybrid_flags'],
                'baseline_path': payload['baseline_path'],
                'inspected_path': payload['inspected_path'],
                'sidecar_path': str(sidecar),
                'result_path': str(result_path),
                'force_requested': context['force_requested'],
                'sidecar_observation': observation,
                'cache_status': 'reused' if observation == 'unchanged' and not context['force_requested'] else 'refreshed' if observation != 'unchanged' else 'unknown'
            }
            context['stage'] = 'complete'
            write_json_atomic(context_path, context)
            return compact
    except BridgeError:
        raise
    except ImportError as error:
        raise BridgeError('missing_dependency', str(error)) from error
    except FileNotFoundError as error:
        raise BridgeError('missing_file', str(error)) from error
    except Exception as error:
        raise BridgeError('inspection_failed', f"{context['stage']}: {type(error).__name__}: {error}") from error
