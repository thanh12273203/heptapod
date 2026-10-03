import json
import math
from typing import Dict, List, Tuple

from . import BridgeError


SCHEMA_VERSION = 'visual-inspection-bridge/1'
MAX_MESSAGE_BYTES = 1024 * 1024


def _unique_object(pairs: List[Tuple[str, object]]) -> Dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BridgeError('invalid_response', f"Duplicate JSON key: {key}")

        result[key] = value

    return result


def _reject_constant(value: str) -> None:
    raise BridgeError('invalid_response', f"Non-finite JSON value: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise BridgeError('invalid_response', f"Non-finite JSON number: {value}")

    return parsed


def validate_response(raw: bytes, request_id: str) -> Dict:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise BridgeError('invalid_response', "Worker response exceeds the size limit.")

    try:
        response = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float
        )
    except (ValueError, UnicodeError, RecursionError) as error:
        raise BridgeError('invalid_response', f"Worker response is not valid JSON: {error}") from error

    if not isinstance(response, dict):
        raise BridgeError('invalid_response', "Worker response must be an object.")
    if response.get('schema_version') != SCHEMA_VERSION or response.get('request_id') != request_id:
        raise BridgeError('invalid_response', "Worker response schema or request ID does not match.")

    status = response.get('status')
    expected = {'schema_version', 'request_id', 'status', 'result' if status == 'ok' else 'error'}
    if status not in ('ok', 'error') or set(response) != expected:
        raise BridgeError('invalid_response', "Worker response has invalid status or fields.")
    if status == 'ok' and not isinstance(response['result'], dict):
        raise BridgeError('invalid_response', "Worker result must be an object.")
    if status == 'error' and (
        not isinstance(response['error'], dict)
        or set(response['error']) != {'code', 'message'}
        or any(not isinstance(value, str) or not value for value in response['error'].values())
    ):
        raise BridgeError('invalid_response', "Worker error must contain a code and message.")

    return response
