import argparse
import json
import platform
import sys
import traceback
from pathlib import Path
from typing import Dict


SCHEMA_VERSION = 'visual-inspection-bridge/1'
MAX_REQUEST_BYTES = 1024 * 1024


def _dispatch(request: Dict, run_directory: Path) -> Dict:
    if set(request) != {'schema_version', 'request_id', 'operation', 'parameters'}:
        raise ValueError("Invalid request fields.")
    if request['schema_version'] != SCHEMA_VERSION:
        raise ValueError("Unsupported bridge schema.")
    if not isinstance(request['request_id'], str) or not request['request_id']:
        raise ValueError("Missing request ID.")
    if request['operation'] == 'preflight':
        from tools.visual_inspection import run_preflight

        return run_preflight(request['parameters'])

    if request['operation'] == 'inspect':
        from tools.visual_inspection import run_board_inspection

        return run_board_inspection(request['parameters'], run_directory)

    if request['operation'] == 'read_result':
        from tools.visual_inspection import read_inspection_result

        return read_inspection_result(request['parameters'], run_directory)

    if request['operation'] != 'ping':
        raise ValueError("Unsupported worker operation.")
    if request['parameters'] != {}:
        raise ValueError("Ping requires an empty parameter object.")

    return {
        'python_version': platform.python_version(),
        'python_executable': sys.executable,
        'python_prefix': sys.prefix,
        'repository': str(Path.cwd()),
        'operations': ['ping', 'preflight', 'inspect', 'read_result']
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute one HEPTAPOD visual-inspection bridge request.")
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--response', type=Path, required=True)
    args = parser.parse_args()
    request_id = None
    try:
        with args.request.open('rb') as handle:
            raw = handle.read(MAX_REQUEST_BYTES + 1)

        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("Request exceeds the size limit.")

        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("Request must be an object.")

        request_id = request.get('request_id')
        result = _dispatch(request, args.response.parent)
        response = {'status': 'ok', 'result': result}
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        code = getattr(error, 'code', 'missing_dependency' if isinstance(error, ImportError) else 'worker_error')
        response = {'status': 'error', 'error': {'code': code, 'message': str(error)}}

    response.update({'schema_version': SCHEMA_VERSION, 'request_id': request_id})
    # The host waits for exit before reading; exclusive creation avoids clobbering.
    with args.response.open('x', encoding='utf-8') as handle:
        json.dump(response, handle, allow_nan=False)


if __name__ == '__main__':
    main()
