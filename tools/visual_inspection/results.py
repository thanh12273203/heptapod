import hashlib
import json
import math
import os
import stat
from pathlib import Path
from typing import Dict, List, Tuple

from . import BridgeError, resolve_read_path, source_revision, validate_result_schema


MAX_RESULT_BYTES = 32 * 1024 * 1024
EVIDENCE_BYTES = 128 * 1024


def _object(pairs: List[Tuple[str, object]]) -> Dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")

        result[key] = value

    return result


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number.")

    return number


def _coordinates(grid: List, value: object) -> List:
    return [[row, col] for row, values in enumerate(grid) for col, item in enumerate(values) if item == value]


def _evidence(payload: Dict) -> Dict:
    available = {'metadata': payload['metadata']}
    included = {}
    omitted = []
    remaining = EVIDENCE_BYTES
    for key, value in available.items():
        size = len(json.dumps({key: value}, allow_nan=False).encode('utf-8'))
        if size <= remaining:
            included[key] = value
            remaining -= size
        else:
            omitted.append(key)

    return {
        'fields': included,
        'segments': [],
        'omitted_fields': omitted,
        'omitted_segment_coordinates': [],
        'complete': not omitted,
        'full_evidence': 'result_path contains the complete saved payload, including any omitted evidence.'
    }


def read_inspection_result(parameters: Dict, run_directory: Path) -> Dict:
    try:
        source = resolve_read_path(parameters['result_path'], [Path(root) for root in parameters['result_roots']])
        descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise BridgeError('invalid_result', "Saved result must be a regular file.")
            if before.st_size > MAX_RESULT_BYTES:
                raise BridgeError('result_too_large', "Saved result exceeds the 32 MiB input limit.")

            raw = handle.read(MAX_RESULT_BYTES + 1)
            after = os.fstat(handle.fileno())

        if len(raw) > MAX_RESULT_BYTES:
            raise BridgeError('result_too_large', "Saved result exceeds the 32 MiB input limit.")
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise BridgeError('result_changed', "Saved result changed during reading; retry with a stable file.")

        payload = json.loads(raw, object_pairs_hook=_object, parse_float=_number, parse_constant=_number)
        if not isinstance(payload, dict) or payload.get('metadata', {}).get('board_shape') != [8, 5]:
            raise ValueError("Expected a saved result with source grid shape `[8, 5]`.")
        validate_result_schema(payload)

        for key in ('baseline_path', 'inspected_path'):
            if not isinstance(payload.get(key), str) or not payload[key] or len(payload[key]) > 8192:
                raise ValueError(f"Invalid {key} in saved result.")

        snapshot = run_directory / 'inspection.json'
        with snapshot.open('xb') as handle:
            handle.write(raw)

        from src.utils import InspectionResults

        persisted = InspectionResults.load_json(str(snapshot))
        coordinates = {key: _coordinates(payload[key], 1) for key in ('pw_flags', 'ae_flags', 'hybrid_flags')}
        coordinates['skipped'] = _coordinates(payload['pw_flags'], -1)

        revision = source_revision(Path.cwd())
        revision['tracked_changes'] = bool(revision['tracked_changes']) if revision['tracked_changes'] is not None else None
        response = {
            'schema_version': 'visual-inspection-result/1',
            'upstream_schema_version': payload.get('schema_version', 1),
            'mode': 'legacy',
            'validation': {'status': 'valid', 'scope': 'Saved result structure validated by upstream InspectionResults.load_json; detection accuracy is not verified.'},
            'freshness': {'status': 'unverified', 'reason': 'No current configuration, board, checkpoint, or calibration was checked. A valid saved result is not a verified current inspection.'},
            'source_path': str(source),
            'result_path': str(snapshot),
            'source_sha256': hashlib.sha256(raw).hexdigest(),
            'reader_revision': revision,
            'summary': persisted.summary(),
            'coordinates': coordinates,
            'coordinate_system': 'Zero-based (row, col) in stored source order; not reversed UI order.',
            'baseline_path': payload['baseline_path'],
            'inspected_path': payload['inspected_path'],
            'pw_flags': payload['pw_flags'],
            'ae_flags': payload['ae_flags'],
            'hybrid_flags': payload['hybrid_flags'],
            'evidence': _evidence(payload)
        }
        return response
    except BridgeError:
        raise
    except ImportError as error:
        raise BridgeError('missing_dependency', f"Cannot load the upstream result reader: {error}") from error
    except FileNotFoundError as error:
        raise BridgeError('missing_file', f"Cannot read saved result: {error}") from error
    except OSError as error:
        raise BridgeError('result_io_error', f"Cannot read or preserve saved result: {error}") from error
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as error:
        raise BridgeError('invalid_result', f"Saved result validation failed: {error}") from error
