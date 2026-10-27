import importlib.metadata
import platform
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from . import (
    BridgeError,
    load_upstream_config,
    resolve_read_path,
    resolve_upstream,
    source_revision,
    validate_sidecar
)


def _check(report: Dict, name: str, operation: Callable, code: str = 'invalid_input') -> Tuple[bool, object]:
    try:
        value = operation()
    except Exception as error:
        if isinstance(error, BridgeError):
            code = error.code
        elif isinstance(error, ImportError):
            code = 'missing_dependency'
        elif isinstance(error, FileNotFoundError):
            code = 'missing_file'

        report['checks'].append({'name': name, 'status': 'blocked', 'code': code, 'message': str(error)})
        return False, None

    report['checks'].append({'name': name, 'status': 'passed'})
    return True, value


def _environment() -> Dict:
    versions = {}
    for package in ('numpy', 'torch', 'torchvision', 'PyYAML'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None

    return {'python': platform.python_version(), 'executable': sys.executable, 'packages': versions}


def _finish(report: Dict) -> Dict:
    report['ready'] = not any(item['status'] == 'blocked' for item in report['checks'])
    return report


def _device(torch: object, requested: Optional[str]) -> str:
    device = torch.device(requested or ('cuda' if torch.cuda.is_available() else 'cpu'))
    if device.type == 'cuda':
        if not torch.cuda.is_available() or (device.index is not None and device.index >= torch.cuda.device_count()):
            raise BridgeError('device_unavailable', f"Requested CUDA device is unavailable: {device}")
    elif device.type == 'mps':
        if not torch.backends.mps.is_available():
            raise BridgeError('device_unavailable', "Requested MPS device is unavailable.")
    elif device.type != 'cpu':
        raise BridgeError('unsupported_device', f"Preflight cannot validate device: {device}")

    return str(device)


def run_preflight(parameters: Dict) -> Dict:
    repository = Path.cwd().resolve()
    report = {
        'schema_version': 'visual-inspection-preflight/1',
        'ready': False,
        'mode': None,
        'checks': [],
        'environment': _environment(),
        'source': source_revision(repository),
        'planned_writes': [],
        'limitations': [
            "No inference, calibration, data mutation, or device-memory test is performed.",
            "Readiness is a point-in-time check, not detection accuracy or a guarantee of later access."
        ]
    }
    data_roots = [Path(path).resolve() for path in parameters['data_roots']]
    writable_roots = [Path(path).resolve() for path in parameters['writable_data_roots']]
    read_roots = [repository, *data_roots]
    config_path = str(repository / parameters.get('config_path', 'configs/inspection.yaml'))
    valid, config_path = _check(report, 'config_file', lambda: resolve_read_path(config_path, read_roots))
    if not valid:
        return _finish(report)

    # These imports are deliberately worker-only; the host needs no ML stack.
    try:
        import numpy as np

        import torch

        load_inspection_model = resolve_upstream('load_inspection_model')
        validate_calibration = resolve_upstream('validate_calibration')
        GRID_SHAPE = resolve_upstream('GRID_SHAPE')
        load_skipped_segments = resolve_upstream('load_skipped_segments')
        open_board = resolve_upstream('open_board')
    except ImportError as error:
        report['checks'].append({'name': 'upstream_imports', 'status': 'blocked', 'code': 'missing_dependency', 'message': str(error)})
        return _finish(report)
    except Exception as error:
        report['checks'].append({'name': 'upstream_imports', 'status': 'blocked', 'code': 'upstream_import_failed', 'message': str(error)})
        return _finish(report)

    valid, config = _check(report, 'configuration', lambda: load_upstream_config(config_path), 'invalid_config')
    if not valid:
        return _finish(report)

    report['resolved_config'] = asdict(config)
    report['mode'] = 'legacy'
    board_value = str(repository / parameters.get('board_path', config.board_path))
    valid_board, board = _check(report, 'board_file', lambda: resolve_read_path(board_value, data_roots))
    checkpoint_ok, _ = _check(report, 'checkpoint_file', lambda: resolve_read_path(config.checkpoint_path, read_roots))
    skips_ok, skip_path = _check(report, 'skip_file', lambda: resolve_read_path(config.skipped_segments_path, read_roots))
    if skips_ok:
        skips_ok, skipped = _check(report, 'skip_coordinates', lambda: load_skipped_segments(str(skip_path)))
        if skips_ok:
            report['skipped_segments'] = [list(item) for item in sorted(skipped)]

    _, resolved_device = _check(report, 'device', lambda: _device(torch, config.device))
    report['device'] = {'requested': config.device, 'resolved': resolved_device, 'checkpoint_validation': 'cpu'}
    board_array = None
    if valid_board:
        report['board_path'] = str(board)
        report['planned_writes'] = [
            {'path': str(board.with_suffix('.inspection.json')), 'authorized': False},
            {'path': str(board.with_suffix('.inspection.lock')), 'authorized': False}
        ]
        write_ok, destination = _check(report, 'sidecar_policy', lambda: validate_sidecar(board, writable_roots))
        if write_ok:
            report['planned_writes'] = [
                {'path': destination['path'], 'authorized': True, 'directory_access': True},
                {'path': destination['lock_path'], 'authorized': True, 'directory_access': True}
            ]

        _, board_array = _check(report, 'board_header', lambda: open_board(str(board)))

    _, reference_path = _check(report, 'reference_file', lambda: resolve_read_path(config.baseline_path, data_roots))

    reference_array = None
    if reference_path is not None:
        _, reference_array = _check(report, 'reference_header', lambda: open_board(str(reference_path)))

    if board_array is not None and reference_array is not None:
        report['board'] = {'shape': list(board_array.shape), 'dtype': str(board_array.dtype)}
        report['reference'] = {'path': str(reference_path), 'shape': list(reference_array.shape), 'dtype': str(reference_array.dtype)}
        if board_array.shape != reference_array.shape:
            report['checks'].append({'name': 'shape_agreement', 'status': 'blocked', 'code': 'shape_mismatch', 'message': "Board and reference dimensions differ."})

    for name in ('ae_threshold.npy', 'pw_threshold.npy'):
        def threshold_header() -> None:
            path = resolve_read_path(str(Path(config.calibration_dir) / name), read_roots)
            threshold = np.load(path, mmap_mode='r', allow_pickle=False)
            if threshold.shape != GRID_SHAPE or not np.isfinite(threshold).all():
                raise ValueError(f"Thresholds must be finite arrays with shape `{GRID_SHAPE}`.")

        _check(report, name, threshold_header, 'invalid_threshold')

    manifest_ok, _ = _check(report, 'calibration_manifest', lambda: resolve_read_path(str(Path(config.calibration_dir) / 'manifest.json'), read_roots))
    if manifest_ok and checkpoint_ok and skips_ok and reference_path is not None:
        _check(report, 'calibration_provenance', lambda: validate_calibration(config), 'incompatible_calibration')

    blockers = [item for item in report['checks'] if item['status'] == 'blocked' and item['name'] != 'sidecar_policy']
    if not blockers and board_array is not None:
        model_ok, model = _check(
            report,
            'checkpoint_compatibility',
            lambda: load_inspection_model(config.checkpoint_path, board_array.shape[2], board_array.shape[3], torch.device('cpu'), config.model),
            'incompatible_checkpoint'
        )
        if model_ok:
            report['model'] = {'class': type(model).__name__, 'latent_dim': model.latent_dim, 'init_filters': model.init_filters, 'layers': model.layers}
    else:
        report['checks'].append({'name': 'checkpoint_compatibility', 'status': 'skipped', 'message': "Resolve earlier blockers before loading weights."})

    return _finish(report)
