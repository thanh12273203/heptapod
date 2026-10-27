import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Dict
from unittest.mock import patch

from tools.visual_inspection import BridgeConfig, InspectionBridge


CONFIG_SOURCE = '''
import json
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class InspectionConfig:
    board_path: str
    baseline_path: str
    checkpoint_path: str
    skipped_segments_path: str
    calibration_dir: str
    device: str = None
    model: dict = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path):
        return cls(**json.loads(Path(path).read_text())['inspection'])
'''

RESULT_SOURCE = '''
import json
from pathlib import Path

class InspectionResults:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return self.payload

    def summary(self):
        return self.payload['metadata']['counts']

    @classmethod
    def load_json(cls, path):
        payload = json.loads(Path(path).read_text())
        if 'pw_flags' not in payload:
            raise ValueError('Invalid synthetic sidecar')
        return cls(payload)
'''

INSPECTION_SOURCE = '''
import json
import os
import time
from pathlib import Path
from src.utils import InspectionResults

def inspect_board(config, board_path=None, force=False):
    board = Path(board_path or config.board_path)
    calibration = Path(config.calibration_dir)
    destination = board.with_suffix('.inspection.json')
    if (calibration / 'stale').exists():
        raise ValueError('Calibration source changed; recalibrate before inspection.')
    if not force and destination.exists():
        return InspectionResults.load_json(destination)
    (calibration / 'entered').touch()
    if (calibration / 'crash').exists():
        raise RuntimeError('Synthetic inference failure')
    if (calibration / 'delay').exists():
        time.sleep(float((calibration / 'delay').read_text()))
    flags = [[0 for col in range(5)] for row in range(8)]
    for row, col in ((0, 0), (0, 4), (1, 0), (1, 4), (6, 0), (6, 4), (7, 0), (7, 4)):
        flags[row][col] = -1
    flags[2][2] = 1
    payload = {
        'pw_flags': flags, 'ae_flags': flags, 'hybrid_flags': flags,
        'baseline_path': config.baseline_path, 'inspected_path': str(board),
        'metadata': {'board_shape': [8, 5], 'counts': {'pixel_flagged': 1, 'autoencoder_flagged': 1, 'hybrid_flagged': 1, 'skipped': 8}}
    }
    if (calibration / 'wrong_board').exists():
        payload['inspected_path'] = config.baseline_path
    if not (calibration / 'no_sidecar').exists():
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload))
        os.replace(temporary, destination)
    return InspectionResults(payload)
'''

class InspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='inspection execution ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'upstream'
        sources = {
            'src/__init__.py': '',
            'src/configs/__init__.py': CONFIG_SOURCE,
            'src/utils/__init__.py': RESULT_SOURCE,
            'src/utils/data/__init__.py': '',
            'src/inspection/__init__.py': INSPECTION_SOURCE,
            'torch.py': 'from types import SimpleNamespace\ncuda = SimpleNamespace(is_available=lambda: False)\n'
        }
        for relative, code in sources.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code)

        self.data = self.repo / 'data'
        self.calibration = self.repo / 'calibrations'
        self.data.mkdir()
        self.calibration.mkdir()
        self.board = self.data / 'board with spaces.npy'
        self.reference = self.data / 'reference.npy'
        self.sidecar = self.board.with_suffix('.inspection.json')
        self.lock = self.board.with_suffix('.inspection.lock')
        self.config_path = self.repo / 'config.json'
        self.settings = {
            'board_path': str(self.board),
            'baseline_path': str(self.reference),
            'checkpoint_path': str(self.repo / 'weights.pt'),
            'skipped_segments_path': str(self.calibration / 'skip.json'),
            'calibration_dir': str(self.calibration)
        }
        for path in (self.board, self.reference, Path(self.settings['checkpoint_path']), Path(self.settings['skipped_segments_path'])):
            path.touch()

        for name in ('manifest.json', 'ae_threshold.npy', 'pw_threshold.npy'):
            (self.calibration / name).write_text('{}')

        self._save_config()
        self.config = BridgeConfig(
            repository=self.repo,
            python=Path(sys.executable),
            data_roots=(self.data,),
            artifact_root=self.root / 'artifacts',
            timeout_seconds=15,
            writable_data_roots=(self.data,)
        )

    @unittest.skipUnless(importlib.util.find_spec('toolbase') and importlib.util.find_spec('mcp'), 'Served integration requires toolbase and MCP')
    def test_served_synthetic_workflow(self) -> None:
        import yaml

        from mcp import Client, StdioServerParameters
        from toolbase.serve.bundles import evaluate_bundles

        self._prepare_preflight_fixture()
        root = Path(__file__).resolve().parents[3]
        metadata = yaml.safe_load((root / 'toolkit.yaml').read_text())
        state = {
            'visual_inspection_repo': str(self.repo),
            'visual_inspection_python': sys.executable,
            'visual_inspection_data_roots': json.dumps([str(self.data)]),
            'visual_inspection_artifact_root': str(self.config.artifact_root),
            'visual_inspection_writable_data_roots': json.dumps([str(self.data)]),
            'visual_inspection_timeout': 15.0
        }
        availability = evaluate_bundles(metadata['bundles'], state)
        entries = [item for item in metadata['tools'] if item.get('bundle') == 'visual_inspection' and availability.is_bundle_available(item['bundle'])]
        server = StdioServerParameters(
            command=sys.executable,
            env={'TOOLBASE_HOST_LOG': str(self.root / 'mcp-host.log')},
            args=['-B', '-m', 'toolbase._toolkit_host', '--toolkit-dir', str(root), '--name', 'heptapod', '--state-config', json.dumps(state), '--tools-spec', json.dumps(entries)]
        )

        async def workflow() -> None:
            async with Client(server) as client:
                discovered = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(set(discovered), {'VisualInspectionPreflight', 'InspectBoard', 'ReadInspectionResult'})
                self.assertEqual(set(discovered['InspectBoard'].input_schema['properties']), {'config_path', 'board_path', 'force'})
                self.assertEqual(set(discovered['ReadInspectionResult'].input_schema['properties']), {'result_path'})

                async def call(name: str, arguments: Dict) -> Dict:
                    result = await client.call_tool(name, arguments)
                    self.assertFalse(result.is_error, result)
                    return json.loads(result.content[0].text)

                preflight = await call('VisualInspectionPreflight', {'config_path': str(self.config_path)})
                self.assertTrue(preflight['result']['ready'], preflight)
                self.assertFalse(self.sidecar.exists())
                inspected = await call('InspectBoard', {'config_path': str(self.config_path)})
                self.assertEqual(inspected['status'], 'ok', inspected)
                saved = await call('ReadInspectionResult', {'result_path': inspected['result']['result_path']})
                self.assertEqual(saved['result']['summary'], inspected['result']['summary'])
                self.assertEqual(saved['result']['freshness']['status'], 'unverified')
                cached = await call('InspectBoard', {'config_path': str(self.config_path)})
                self.assertEqual(cached['result']['cache_status'], 'reused')
                forced = await call('InspectBoard', {'config_path': str(self.config_path), 'force': True})
                self.assertEqual(forced['result']['cache_status'], 'refreshed')
                failure = await client.call_tool('ReadInspectionResult', {'result_path': str(self.root / 'outside.json')})
                self.assertIn('path_outside_root', failure.content[0].text)

        try:
            asyncio.run(asyncio.wait_for(workflow(), timeout=30))
        except BaseException:
            print((self.root / 'mcp-host.log').read_text(), file=sys.stderr)
            raise

    def _prepare_preflight_fixture(self) -> None:
        # Lightweight upstream doubles; the MCP transport and tool host are real.
        with (self.repo / 'src' / 'inspection' / '__init__.py').open('a') as handle:
            handle.write("\nfrom types import SimpleNamespace\ndef validate_calibration(config): pass\ndef load_inspection_model(*args): return SimpleNamespace(latent_dim=64, init_filters=128, layers=[2, 2, 2])\n")

        with (self.repo / 'src' / 'utils' / 'data' / '__init__.py').open('a') as handle:
            handle.write("\nfrom types import SimpleNamespace\nGRID_SHAPE = (8, 5)\ndef open_board(path): return SimpleNamespace(shape=(8, 5, 1060, 1882, 3), dtype='uint8')\ndef load_skipped_segments(path): return {(0, 0)}\n")

        with (self.repo / 'torch.py').open('a') as handle:
            handle.write("\ndef device(name): return SimpleNamespace(type=name, index=None)\n")

        (self.repo / 'numpy.py').write_text("from types import SimpleNamespace\ndef load(*args, **kwargs): return SimpleNamespace(shape=(8, 5))\ndef isfinite(array): return SimpleNamespace(all=lambda: True)\n")
    def test_documented_example_cli(self) -> None:
        self._prepare_preflight_fixture()
        root = Path(__file__).resolve().parents[3]
        settings = {
            'repository': str(self.repo), 'python': sys.executable,
            'data_roots': [str(self.data)], 'artifact_root': str(self.config.artifact_root),
            'writable_data_roots': [str(self.data)]
        }
        config_path = self.root / 'bridge.json'
        config_path.write_text(json.dumps(settings))
        command = [sys.executable, '-B', '-m', 'scripts.visual_inspection_example', '--bridge-config', str(config_path)]
        completed = subprocess.run(
            [*command, 'inspect', '--config-path', str(self.config_path)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual([step['execution']['operation'] for step in report['steps']], ['preflight', 'inspect', 'read_result'])
        saved_path = report['steps'][-1]['result']['result_path']
        completed = subprocess.run([*command, 'read-result', saved_path], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['steps'][0]['result']['freshness']['status'], 'unverified')
        before = self.sidecar.stat().st_mtime_ns
        settings['writable_data_roots'] = []
        config_path.write_text(json.dumps(settings))
        completed = subprocess.run(
            [*command, 'inspect', '--config-path', str(self.config_path), '--force'],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15
        )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertEqual(len(json.loads(completed.stdout)['steps']), 1)
        self.assertEqual(self.sidecar.stat().st_mtime_ns, before)

    def _save_config(self) -> None:
        self.config_path.write_text(json.dumps({'inspection': self.settings}))

    def _run(self, force: bool = False) -> Dict:
        return InspectionBridge(self.config).execute('inspect', {'config_path': str(self.config_path), 'force': force})

    def _record(self, response: Dict) -> Dict:
        record = json.loads(Path(response['execution']['record_path']).read_text())
        self.assertEqual(record['response'], response)
        return record

    def test_legacy_cache_force_and_durable_records(self) -> None:
        first = self._run()
        self.assertEqual(first['status'], 'ok', first)
        self.assertEqual(first['result']['sidecar_observation'], 'created')
        snapshot = Path(first['result']['result_path'])
        self.assertEqual(json.loads(snapshot.read_text()), json.loads(self.sidecar.read_text()))
        record = self._record(first)
        self.assertEqual(record['inspection_context']['resolved_config']['device'], 'cpu')
        self.assertEqual(record['inspection_context']['stage'], 'complete')
        self.assertIn('source', record['inspection_context'])
        second = self._run()
        self.assertEqual(second['result']['cache_status'], 'reused')
        third = self._run(force=True)
        self.assertEqual(third['result']['cache_status'], 'refreshed')
        self.assertTrue(third['result']['force_requested'])
        self.assertTrue(snapshot.exists())
        self._record(third)

    def test_unsupported_configuration_does_not_run_inference(self) -> None:
        self.settings['local_calibration_path'] = '/unsupported.json'
        self._save_config()
        response = self._run()
        self.assertEqual(response['error']['code'], 'unsupported_capability')
        self.assertFalse(self.sidecar.exists())
        self.assertFalse((self.calibration / 'entered').exists())

    def test_unsupported_cached_result_is_rejected(self) -> None:
        self.assertEqual(self._run()['status'], 'ok')
        payload = json.loads(self.sidecar.read_text())
        payload['schema_version'] = 2
        self.sidecar.write_text(json.dumps(payload))
        response = self._run()
        self.assertEqual(response['error']['code'], 'invalid_result')

    def test_stale_calibration_rejects_cached_and_forced_runs(self) -> None:
        self.assertEqual(self._run(force=True)['status'], 'ok')
        unchanged = self.sidecar.read_bytes()
        (self.calibration / 'stale').touch()
        for force in (False, True):
            response = self._run(force)
            self.assertEqual(response['error']['code'], 'inspection_failed')
            self.assertIn('recalibrate', response['error']['message'])
            self.assertEqual(self.sidecar.read_bytes(), unchanged)
            self.assertEqual(self._record(response)['inspection_context']['stage'], 'upstream_inspection')

    def test_no_write_root_and_symlink_sidecar_block_execution(self) -> None:
        self.config = replace(self.config, writable_data_roots=())
        response = self._run()
        self.assertEqual(response['error']['code'], 'sidecar_not_authorized')
        self.assertFalse(self.lock.exists())
        self.assertFalse(self.sidecar.exists())
        self.config = replace(self.config, writable_data_roots=(self.data,))
        self.sidecar.symlink_to(self.reference)
        response = self._run()
        self.assertEqual(response['error']['code'], 'sidecar_not_authorized')
        self.assertFalse((self.calibration / 'entered').exists())

    def test_symlink_lock_cannot_be_followed(self) -> None:
        self.lock.symlink_to(self.reference)
        response = self._run()
        self.assertEqual(response['error']['code'], 'invalid_lock')
        self.assertFalse((self.calibration / 'entered').exists())

    def test_concurrent_workers_with_different_artifact_roots_share_lock(self) -> None:
        (self.calibration / 'delay').write_text('3')
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(self._run)
            deadline = time.monotonic() + 10
            while not (self.calibration / 'entered').exists() and time.monotonic() < deadline:
                time.sleep(0.02)

            self.assertTrue((self.calibration / 'entered').exists())
            other = replace(self.config, artifact_root=self.root / 'other artifacts')
            second = InspectionBridge(other).execute('inspect', {'config_path': str(self.config_path)})
            self.assertEqual(second['error']['code'], 'board_busy', second)
            self._record(second)
            self.assertEqual(first.result(timeout=10)['status'], 'ok')

    def test_timeout_releases_lock_and_retains_context_and_logs(self) -> None:
        self.config = replace(self.config, timeout_seconds=2)
        (self.calibration / 'delay').write_text('20')
        response = self._run()
        self.assertEqual(response['error']['code'], 'timeout')
        self.assertEqual(self._record(response)['inspection_context']['stage'], 'upstream_inspection')
        self.assertTrue(Path(response['execution']['stderr_path']).is_file())
        (self.calibration / 'delay').unlink()
        self.config = replace(self.config, timeout_seconds=15)
        self.assertEqual(self._run()['status'], 'ok')

    def test_upstream_failure_missing_sidecar_and_wrong_identity(self) -> None:
        for marker, code in (('crash', 'inspection_failed'), ('no_sidecar', 'missing_sidecar'), ('wrong_board', 'invalid_result')):
            with self.subTest(marker=marker):
                (self.calibration / marker).touch()
                response = self._run(force=True)
                self.assertEqual(response['error']['code'], code, response)
                self.assertTrue(Path(response['execution']['stderr_path']).read_text())
                self._record(response)
                (self.calibration / marker).unlink()

    def test_runtime_cannot_change_policy_and_force_is_boolean(self) -> None:
        for values in ({'force': 'true'}, {'force': 1}, {'writable_data_roots': ['/']}):
            response = InspectionBridge(self.config).execute('inspect', values)
            self.assertEqual(response['error']['code'], 'invalid_request')

        self.assertFalse(self.lock.exists())


@unittest.skipUnless(importlib.util.find_spec('orchestral'), 'Agent wrapper requires Orchestral.')
class InspectionToolTests(unittest.TestCase):
    def test_wrapper_passes_force_and_resets_optional_inputs(self) -> None:
        from tools.visual_inspection.agent_tools import InspectBoardTool

        self.assertEqual(InspectBoardTool._get_runtime_fields(), ['config_path', 'board_path', 'force'])
        tool = InspectBoardTool(
            visual_inspection_repo='/repo',
            visual_inspection_python='/python',
            visual_inspection_data_roots=['/repo/data'],
            visual_inspection_artifact_root='/logs'
        )
        with patch('tools.visual_inspection.agent_tools.inspect.BridgeConfig'), patch('tools.visual_inspection.agent_tools.inspect.InspectionBridge') as factory:
            factory.return_value.execute.return_value = {'status': 'ok', 'result': {'summary': {'uncertain': 1}}}
            result = json.loads(tool.execute(board_path='data/board.npy', force=True))
            self.assertEqual(result['result']['summary']['uncertain'], 1)
            factory.return_value.execute.assert_called_with('inspect', {'config_path': 'configs/inspection.yaml', 'board_path': 'data/board.npy', 'force': True})
            tool.execute()
            factory.return_value.execute.assert_called_with('inspect', {'config_path': 'configs/inspection.yaml', 'force': False})


if __name__ == '__main__':
    unittest.main()
