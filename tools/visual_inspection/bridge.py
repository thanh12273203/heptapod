import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from . import BridgeConfig, BridgeError, MAX_MESSAGE_BYTES, SCHEMA_VERSION, validate_response, write_json_atomic


WORKER_PATH = Path(__file__).resolve().parents[2] / 'scripts' / 'visual_inspection_worker.py'
BOOTSTRAP = "import runpy, sys; runpy.run_path(sys.argv.pop(1), run_name='__main__')"


def _stop_process_group(process: subprocess.Popen) -> None:
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()

    process.wait()


class InspectionBridge:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config

    def execute(self, operation: str = 'ping', parameters: Optional[Dict] = None) -> Dict:
        request_id = uuid.uuid4().hex
        execution = {'operation': operation, 'returncode': None, 'run_directory': None}
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            response = self._execute(request_id, operation, parameters, execution)
        except BridgeError as error:
            response = {
                'schema_version': SCHEMA_VERSION,
                'request_id': request_id,
                'status': 'error',
                'error': error.to_dict()
            }
        except OSError as error:
            response = {
                'schema_version': SCHEMA_VERSION,
                'request_id': request_id,
                'status': 'error',
                'error': {'code': 'io_error', 'message': str(error)}
            }

        execution['elapsed_seconds'] = time.monotonic() - started
        response['execution'] = execution
        if execution['run_directory'] is not None:
            record_path = Path(execution['run_directory']) / 'run.json'
            execution['record_path'] = str(record_path)
            record = {
                'schema_version': 'visual-inspection-run/1',
                'started_at': started_at,
                'finished_at': datetime.now(timezone.utc).isoformat(),
                'response': response
            }
            context_path = Path(execution['run_directory']) / 'inspection_context.json'
            if context_path.is_file() and not context_path.is_symlink():
                try:
                    with context_path.open('rb') as handle:
                        raw = handle.read(MAX_MESSAGE_BYTES + 1)

                    if len(raw) > MAX_MESSAGE_BYTES:
                        raise ValueError("Inspection context exceeds the size limit.")

                    record['inspection_context'] = json.loads(raw)
                except (OSError, ValueError, RecursionError) as error:
                    record['context_error'] = str(error)

            try:
                write_json_atomic(record_path, record)
            except (OSError, ValueError) as error:
                response.pop('result', None)
                response.update(status='error', error={'code': 'run_record_failed', 'message': str(error)})

        return response

    def _execute(
        self,
        request_id: str,
        operation: str,
        parameters: Optional[Dict],
        execution: Dict
    ) -> Dict:
        if os.name != 'posix':
            raise BridgeError('unsupported_platform', "The bridge currently requires POSIX process-group cleanup.")
        if operation not in ('ping', 'preflight', 'inspect', 'read_result'):
            raise BridgeError('unsupported_operation', f"Unsupported bridge operation: {operation}")
        if parameters is not None and not isinstance(parameters, dict):
            raise BridgeError('invalid_request', "Parameters must be an object.")
        if operation == 'ping' and parameters:
            raise BridgeError('invalid_request', "The ping operation accepts an empty parameter object only.")

        parameters = dict(parameters or {})
        if operation == 'read_result':
            if set(parameters) != {'result_path'} or not isinstance(parameters['result_path'], str) or not parameters['result_path'].strip():
                raise BridgeError('invalid_request', "read_result requires only a nonempty result_path string.")

            parameters['result_roots'] = [str(path) for path in (*self.config.data_roots, self.config.artifact_root)]

        if operation in ('preflight', 'inspect'):
            allowed = {'config_path', 'board_path', 'force'} if operation == 'inspect' else {'config_path', 'board_path'}
            if set(parameters) - allowed:
                raise BridgeError('invalid_request', f"Unsupported parameters for {operation}.")

            for key, value in parameters.items():
                if key == 'force':
                    if type(value) is not bool:
                        raise BridgeError('invalid_request', "force must be a Boolean.")
                elif not isinstance(value, str) or not value.strip():
                    raise BridgeError('invalid_request', f"{key} must be a nonempty path string.")

            parameters['data_roots'] = [str(path) for path in self.config.data_roots]
            parameters['writable_data_roots'] = [str(path) for path in self.config.writable_data_roots]
        if not WORKER_PATH.is_file():
            raise BridgeError('missing_worker', f"Worker entry point is missing: {WORKER_PATH}")

        request = {
            'schema_version': SCHEMA_VERSION,
            'request_id': request_id,
            'operation': operation,
            'parameters': parameters
        }
        # Recheck containment immediately before creating artifacts.
        root = self.config.artifact_root
        if root.resolve() != root:
            raise BridgeError('path_outside_root', "Artifact root changed through a symlink.")

        root.mkdir(parents=True, exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix=f'{request_id}-', dir=root))
        request_path = self.config.output_path(run / 'request.json')
        response_path = self.config.output_path(run / 'response.json')
        stdout_path = self.config.output_path(run / 'stdout.log')
        stderr_path = self.config.output_path(run / 'stderr.log')
        execution.update({
            'run_directory': str(run),
            'request_path': str(request_path),
            'response_path': str(response_path),
            'stdout_path': str(stdout_path),
            'stderr_path': str(stderr_path),
            'repository': str(self.config.repository),
            'python': str(self.config.python)
        })
        request_path.write_text(json.dumps(request, allow_nan=False), encoding='utf-8')
        command = [
            str(self.config.python), '-s', '-B', '-u', '-c', BOOTSTRAP,
            str(WORKER_PATH), '--request', str(request_path), '--response', str(response_path)
        ]
        # Supply only the known HEPTAPOD source root, retaining the sibling cwd
        # for its src package. Never inherit the caller's Python import overrides.
        environment = {key: value for key, value in os.environ.items() if not key.startswith('PYTHON')}
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
        environment['MPLCONFIGDIR'] = str(run / 'cache' / 'matplotlib')
        environment['XDG_CACHE_HOME'] = str(run / 'cache')
        environment['TORCH_HOME'] = str(run / 'cache' / 'torch')
        with stdout_path.open('xb') as stdout, stderr_path.open('xb') as stderr:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.config.repository,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                    start_new_session=True
                )
            except OSError as error:
                raise BridgeError('launch_failed', f"Cannot launch the configured interpreter: {error}") from error

            try:
                process.wait(timeout=self.config.timeout_seconds)
            except subprocess.TimeoutExpired as error:
                raise BridgeError('timeout', f"Worker exceeded {self.config.timeout_seconds} seconds. A sidecar may already have been committed; inspect artifacts before retrying.") from error
            finally:
                # Also remove descendants left behind by an exited worker.
                _stop_process_group(process)
                execution['returncode'] = process.returncode

        if process.returncode != 0:
            raise BridgeError('worker_failed', f"Worker exited with code {process.returncode}; see stderr_path.")
        if not response_path.is_file() or response_path.is_symlink():
            raise BridgeError('invalid_response', "Worker did not produce a regular response file.")

        with response_path.open('rb') as handle:
            raw = handle.read(MAX_MESSAGE_BYTES + 1)

        return validate_response(raw, request_id)
