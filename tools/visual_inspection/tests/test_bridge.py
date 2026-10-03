import os
import subprocess
import sys
import tempfile
import time
import unittest
import venv
from pathlib import Path
from typing import Dict
from unittest.mock import patch

from tools.visual_inspection import BridgeConfig, BridgeError, InspectionBridge, SCHEMA_VERSION, bridge


@unittest.skipUnless(os.name == 'posix', "Bridge execution currently requires POSIX.")
class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix='inspection bridge tests ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / 'visual inspection'
        package = self.repository / 'src' / 'inspection'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('raise RuntimeError("Must not import ML during ping")', encoding='utf-8')
        self.data = self.repository / 'data'
        self.data.mkdir()
        self.artifacts = self.root / 'artifacts'
        self.config = BridgeConfig(self.repository, Path(sys.executable), (self.data,), self.artifacts)

    def _worker(self, body: str) -> Path:
        worker = self.root / 'test worker.py'
        worker.write_text(
            'import json, sys\nfrom pathlib import Path\n'
            'request = json.loads(Path(sys.argv[sys.argv.index("--request") + 1]).read_text())\n'
            'destination = Path(sys.argv[sys.argv.index("--response") + 1])\n' + body,
            encoding='utf-8'
        )
        return worker

    def _execute_worker(self, body: str) -> Dict:
        with patch.object(bridge, 'WORKER_PATH', self._worker(body)):
            return InspectionBridge(self.config).execute()

    def test_ping_preserves_virtualenv_and_ignores_python_environment(self) -> None:
        environment = self.root / 'python environment'
        venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
        interpreter = environment / 'bin' / 'python'
        config = BridgeConfig(self.repository, interpreter, (self.data,), self.artifacts)
        self.assertEqual(config.python, interpreter)
        self.assertFalse(self.artifacts.exists())
        with patch.dict(os.environ, {'PYTHONHOME': '/missing/python', 'PYTHONPATH': '/untrusted/python'}):
            response = InspectionBridge(config).execute()

        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(response['schema_version'], SCHEMA_VERSION)
        self.assertEqual(response['result']['python_prefix'], str(environment))
        self.assertEqual(response['result']['repository'], str(self.repository))
        self.assertEqual(response['execution']['returncode'], 0)
        for key in ('request_path', 'response_path', 'stdout_path', 'stderr_path'):
            path = Path(response['execution'][key])
            self.assertTrue(path.is_relative_to(self.artifacts))
            self.assertTrue(path.is_file())

    def test_configuration_errors_do_not_create_outputs(self) -> None:
        cases = [
            ({'repository': self.root / 'missing'}, 'invalid_repository'),
            ({'python': self.root / 'missing'}, 'invalid_interpreter'),
            ({'repository': Path('relative')}, 'invalid_config'),
            ({'data_roots': ()}, 'invalid_data_roots'),
            ({'data_roots': (self.root / 'missing',)}, 'invalid_data_roots'),
            ({'writable_data_roots': (self.repository,)}, 'invalid_write_roots'),
            ({'timeout_seconds': 0}, 'invalid_timeout'),
            ({'timeout_seconds': float('nan')}, 'invalid_timeout'),
            ({'timeout_seconds': True}, 'invalid_timeout')
        ]
        for overrides, code in cases:
            with self.subTest(code=code, overrides=overrides):
                arguments = dict(vars(self.config))
                arguments.update(overrides)
                with self.assertRaises(BridgeError) as context:
                    BridgeConfig(**arguments)

                self.assertEqual(context.exception.code, code)

        self.assertFalse(self.artifacts.exists())

    def test_input_and_output_containment(self) -> None:
        board = self.data / 'board with spaces.npy'
        board.touch()
        outside = self.root / 'outside.npy'
        outside.touch()
        (self.data / 'escaped.npy').symlink_to(outside)
        self.assertEqual(self.config.input_path('data/board with spaces.npy'), board)
        for value in ('data/escaped.npy', '../outside.npy', str(outside), 'data/missing.npy'):
            with self.subTest(value=value), self.assertRaises(BridgeError):
                self.config.input_path(value)

        self.artifacts.mkdir()
        (self.artifacts / 'escape').symlink_to(self.root, target_is_directory=True)
        for value in ('../outside.json', 'escape/outside.json', str(outside), '.'):
            with self.subTest(value=value), self.assertRaises(BridgeError):
                self.config.output_path(value)

        self.assertEqual(self.config.output_path('run/result.json'), self.artifacts / 'run' / 'result.json')

    def test_artifact_root_symlink_replacement_is_rejected(self) -> None:
        self.artifacts.symlink_to(self.data, target_is_directory=True)
        result = InspectionBridge(self.config).execute()
        self.assertEqual(result['error']['code'], 'path_outside_root')
        self.assertEqual(list(self.data.iterdir()), [])

    def test_unknown_operations_and_parameters_are_rejected_without_writes(self) -> None:
        for operation, parameters in (('train', {}), ('ping', {'code': 'anything'}), ('ping', [])):
            response = InspectionBridge(self.config).execute(operation, parameters)
            self.assertEqual(response['status'], 'error')

        self.assertFalse(self.artifacts.exists())

    def test_logs_are_separate_from_results(self) -> None:
        response = self._execute_worker(
            'print("diagnostic output")\nprint("diagnostic error", file=sys.stderr)\n'
            'destination.write_text(json.dumps({"schema_version": request["schema_version"], '
            '"request_id": request["request_id"], "status": "ok", "result": {"answer": 42}}))\n'
        )
        self.assertEqual(response['result'], {'answer': 42})
        self.assertIn('diagnostic output', Path(response['execution']['stdout_path']).read_text())
        self.assertIn('diagnostic error', Path(response['execution']['stderr_path']).read_text())

    def test_preflight_missing_config_crosses_worker_boundary_without_ml(self) -> None:
        response = InspectionBridge(self.config).execute('preflight')
        self.assertEqual(response['status'], 'ok', response)
        self.assertFalse(response['result']['ready'])
        self.assertEqual(response['result']['checks'][0]['code'], 'missing_file')

    def test_preflight_cannot_override_state_roots(self) -> None:
        response = InspectionBridge(self.config).execute('preflight', {'data_roots': ['/']})
        self.assertEqual(response['error']['code'], 'invalid_request')
        self.assertFalse(self.artifacts.exists())

    def test_nonzero_exit_and_launch_errors(self) -> None:
        response = self._execute_worker('print("failure details", file=sys.stderr)\nsys.exit(7)\n')
        self.assertEqual(response['error']['code'], 'worker_failed')
        self.assertEqual(response['execution']['returncode'], 7)
        self.assertIn('failure details', Path(response['execution']['stderr_path']).read_text())
        with patch.object(bridge.subprocess, 'Popen', side_effect=OSError('executable failed')):
            response = InspectionBridge(self.config).execute()

        self.assertEqual(response['error']['code'], 'launch_failed')

    def test_missing_malformed_and_mismatched_responses(self) -> None:
        bodies = [
            'pass',
            'destination.write_text("not json")',
            'destination.write_text("[]")',
            'destination.write_text("{\\"a\\": 1, \\"a\\": 2}")',
            'destination.write_text("{\\"value\\": NaN}")',
            'destination.write_text(json.dumps({"schema_version": request["schema_version"], '
            '"request_id": request["request_id"], "status": "ok", "result": {"value": 123}}).replace("123", "1e999"))',
            'destination.write_text("[" * 10000 + "]" * 10000)',
            'destination.write_text("x" * (1024 * 1024 + 1))',
            'destination.write_text(json.dumps({"schema_version": "wrong", '
            '"request_id": request["request_id"], "status": "ok", "result": {}}))',
            'destination.write_text(json.dumps({"schema_version": request["schema_version"], '
            '"request_id": "wrong", "status": "ok", "result": {}}))',
            'destination.write_text(json.dumps({"schema_version": request["schema_version"], '
            '"request_id": request["request_id"], "status": "error", "error": {}}))',
            'destination.symlink_to(sys.argv[sys.argv.index("--request") + 1])'
        ]
        for body in bodies:
            with self.subTest(body=body):
                response = self._execute_worker(body)
                self.assertEqual(response['error']['code'], 'invalid_response', response)

    def test_structured_worker_failure(self) -> None:
        response = self._execute_worker(
            'destination.write_text(json.dumps({"schema_version": request["schema_version"], '
            '"request_id": request["request_id"], "status": "error", '
            '"error": {"code": "missing_dependency", "message": "Install the upstream dependencies."}}))'
        )
        self.assertEqual(response['error']['code'], 'missing_dependency')

    def test_timeout_kills_worker_and_descendant(self) -> None:
        self.config = BridgeConfig(self.repository, Path(sys.executable), (self.data,), self.artifacts, 1.0)
        response = self._execute_worker(
            'import subprocess, time\n'
            'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
            'print(child.pid, flush=True)\ntime.sleep(60)\n'
        )
        self.assertEqual(response['error']['code'], 'timeout')
        self.assertLess(response['execution']['elapsed_seconds'], 5)
        child_pid = int(Path(response['execution']['stdout_path']).read_text().strip())
        # A killed orphan may briefly remain a zombie until its parent reaps it.
        for _ in range(100):
            status = Path(f'/proc/{child_pid}/stat')
            if not status.exists() or status.read_text().split()[2] == 'Z':
                break

            time.sleep(0.01)
        else:
            self.fail('Worker descendant is still running after timeout.')

    def test_import_and_ping_need_no_site_packages(self) -> None:
        code = (
            'import sys; from tools.visual_inspection import InspectionBridge; '
            'assert "torch" not in sys.modules; assert "src.inspection" not in sys.modules'
        )
        completed = subprocess.run(
            [sys.executable, '-S', '-B', '-c', code],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
