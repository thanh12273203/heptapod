import importlib
from pathlib import Path
from typing import Dict

from . import BridgeError


_API_LOCATIONS = {
    'InspectionConfig': ('src.configs', 'src.configs.inspection_config'),
    'validate_calibration': ('src.inspection', 'src.inspection.run'),
    'load_inspection_model': ('src.inspection', 'src.inspection.model'),
    'open_board': ('src.utils.data', 'src.utils.data.board_contract'),
    'GRID_SHAPE': ('src.utils.data', 'src.utils.data.board_contract'),
    'load_skipped_segments': ('src.utils.data', None)
}


def resolve_upstream(name: str) -> object:
    package, legacy_module = _API_LOCATIONS[name]
    module = importlib.import_module(package)
    if hasattr(module, name):
        return getattr(module, name)
    if legacy_module is not None:
        # Only an absent export permits fallback; dependency errors propagate.
        module = importlib.import_module(legacy_module)
        if hasattr(module, name):
            return getattr(module, name)

    raise BridgeError('unsupported_capability', f"Upstream does not export {name} through a supported API location.")


def load_upstream_config(path: Path) -> object:
    import yaml

    with path.open(encoding='utf-8') as handle:
        document = yaml.safe_load(handle)

    if not isinstance(document, dict) or not isinstance(document.get('inspection', {}), dict):
        raise ValueError("Inspection YAML must contain a configuration mapping.")

    settings = document.get('inspection', {})
    for key in ('local_calibration_path', 'local_scoring_path', 'annotation_dir'):
        if settings.get(key) not in (None, '', {}, []):
            raise BridgeError('unsupported_capability', f"HEPTAPOD supports whole-segment inspection only; remove {key} from this configuration.")

    config = resolve_upstream('InspectionConfig').from_yaml(str(path))
    if getattr(config, 'local_calibration_path', None):
        raise BridgeError('unsupported_capability', "HEPTAPOD supports whole-segment inspection only.")

    return config


def validate_result_schema(payload: Dict) -> None:
    version = payload.get('schema_version', 1)
    if type(version) is not int or version != 1 or payload.get('local') is not None:
        raise BridgeError('invalid_result', "Only whole-segment result schema 1 (or an omitted version) is supported.")
