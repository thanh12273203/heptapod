import importlib.metadata
import platform
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import BridgeError, resolve_read_path, source_revision, validate_sidecar


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


def _regional_inventory(directory: Path) -> Dict:
    return {
        'directory': str(directory),
        'manifest_present': (directory / 'manifests' / 'boards.json').is_file(),
        'templates_present': any((directory / 'masks').glob('*/v*.json')),
        'annotations_present': any((directory / 'annotations').glob('*/*.json')),
        'thresholds_present': any((directory / 'thresholds').glob('*/local.json')),
        'note': "Presence alone does not establish reviewed data or calibration compatibility."
    }


def _regional_context(config: object, board: Path, read_roots: List[Path], data_roots: List[Path]) -> Dict:
    import yaml

    from src.configs import LocalScoreConfig, MaskingConfig
    from src.utils.data import RegionAnnotationStore

    directory = resolve_read_path(config.annotation_dir or config.calibration_dir, read_roots, require_file=False)
    store = RegionAnnotationStore(MaskingConfig(calibration_dir=str(directory)))
    manifest = store.load_manifest()
    for item in manifest['boards']:
        resolve_read_path(str(store.config.root / item['path']), data_roots)

    record = next((item for item in manifest['boards'] if (store.config.root / item['path']).resolve() == board), None)
    if record is None:
        raise BridgeError('unregistered_board', "Board has no regional manifest record.")
    if not record['template']:
        raise BridgeError('missing_template', "Board has no regional template assignment.")
    if record['appearance_group'] == 'unknown':
        raise BridgeError('unknown_appearance', "Regional reference routing needs explicit appearance metadata.")

    template = store.load_template(**record['template'])
    reference = next((item for item in manifest['boards'] if (item['board_id'], item['scan_id']) == (template['source']['board_id'], template['source']['scan_id'])), None)
    if reference is None or reference['split'] != 'reference' or reference['appearance_group'] != record['appearance_group']:
        raise BridgeError('incompatible_reference', "Template source is not an appearance-compatible reference-role board.")

    scoring_path = resolve_read_path(config.local_scoring_path, read_roots)
    with scoring_path.open(encoding='utf-8') as handle:
        settings = LocalScoreConfig(**yaml.safe_load(handle)['local_calibration'])

    return {
        'store': store,
        'settings': settings,
        'reference_path': resolve_read_path(str(store.config.root / reference['path']), data_roots),
        'summary': {
            'board_id': record['board_id'],
            'scan_id': record['scan_id'],
            'appearance_group': record['appearance_group'],
            'template': record['template'],
            'reference_board_id': reference['board_id']
        }
    }


def _local_calibration(config: object, context: Dict, skipped: object, roots: List[Path]) -> Dict:
    from src.inspection import load_local_calibration
    from src.utils.data import read_document

    path = resolve_read_path(config.local_calibration_path, roots)
    document = read_document(path)
    # Validate dependency scope before the upstream validator stats those paths.
    for dependency in document['dependencies']:
        resolve_read_path(dependency, roots)

    artifact = load_local_calibration(path, context['store'], context['settings'], Path(config.checkpoint_path), skipped)
    return {
        'path': str(path),
        'regional_thresholds': len(artifact['regional_thresholds']),
        'segment_thresholds': len(artifact['segment_thresholds']),
        'note': "Compatible provenance does not guarantee threshold support or coverage for every target."
    }


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

        from src.configs import InspectionConfig
        from src.inspection import load_inspection_model, validate_calibration
        from src.utils.data import GRID_SHAPE, load_skipped_segments, open_board
    except ImportError as error:
        report['checks'].append({'name': 'upstream_imports', 'status': 'blocked', 'code': 'missing_dependency', 'message': str(error)})
        return _finish(report)
    except Exception as error:
        report['checks'].append({'name': 'upstream_imports', 'status': 'blocked', 'code': 'upstream_import_failed', 'message': str(error)})
        return _finish(report)

    valid, config = _check(report, 'configuration', lambda: InspectionConfig.from_yaml(str(config_path)), 'invalid_config')
    if not valid:
        return _finish(report)

    report['resolved_config'] = asdict(config)
    report['mode'] = 'regional' if config.local_calibration_path else 'legacy'
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
    inventory_ok, inventory = _check(
        report,
        'regional_inventory',
        lambda: _regional_inventory(resolve_read_path(config.annotation_dir or config.calibration_dir, read_roots, require_file=False))
    )
    if inventory_ok:
        report['regional_inventory'] = inventory

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

    context = None
    if report['mode'] == 'regional':
        if valid_board:
            _, context = _check(report, 'regional_documents', lambda: _regional_context(config, board, read_roots, data_roots), 'invalid_regional_documents')

        reference_path = context['reference_path'] if context else None
    else:
        _, reference_path = _check(report, 'reference_file', lambda: resolve_read_path(config.baseline_path, data_roots))

    reference_array = None
    if reference_path is not None:
        _, reference_array = _check(report, 'reference_header', lambda: open_board(str(reference_path)))

    if board_array is not None and reference_array is not None:
        report['board'] = {'shape': list(board_array.shape), 'dtype': str(board_array.dtype)}
        report['reference'] = {'path': str(reference_path), 'shape': list(reference_array.shape), 'dtype': str(reference_array.dtype)}
        if board_array.shape != reference_array.shape:
            report['checks'].append({'name': 'shape_agreement', 'status': 'blocked', 'code': 'shape_mismatch', 'message': "Board and reference dimensions differ."})

    if report['mode'] == 'legacy':
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
    else:
        local_file_ok, _ = _check(report, 'local_calibration_file', lambda: resolve_read_path(config.local_calibration_path, read_roots))
        if context and local_file_ok and checkpoint_ok and skips_ok:
            calibration_ok, summary = _check(report, 'calibration_provenance', lambda: _local_calibration(config, context, skipped, read_roots), 'incompatible_calibration')
            if calibration_ok:
                report['local_calibration'] = summary
                report['regional_context'] = context['summary']

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
