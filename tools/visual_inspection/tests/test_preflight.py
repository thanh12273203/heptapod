import hashlib
import importlib.util
import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Dict, Optional
from unittest.mock import Mock, patch

from tools.visual_inspection import run_preflight


@dataclass
class _Settings:
    board_path: str
    baseline_path: str
    checkpoint_path: str
    skipped_segments_path: str
    calibration_dir: str
    device: Optional[str] = None
    model: Dict = field(default_factory=dict)


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='preflight contracts ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / 'data'
        self.calibration = self.root / 'calibrations'
        self.data.mkdir()
        self.calibration.mkdir()
        self.board = self.data / 'board.npy'
        self.reference = self.data / 'reference.npy'
        self.checkpoint = self.root / 'weights.pt'
        self.skip_path = self.calibration / 'skips.json'
        self.config_path = self.root / 'inspection.yaml'
        self.settings = _Settings(
            str(self.board),
            str(self.reference),
            str(self.checkpoint),
            str(self.skip_path),
            str(self.calibration)
        )
        for path in (self.board, self.reference, self.checkpoint, self.skip_path, self.config_path):
            path.write_text('{}')

        for name in ('manifest.json', 'ae_threshold.npy', 'pw_threshold.npy'):
            (self.calibration / name).write_text('{}')

        self.parameters = {
            'config_path': str(self.config_path),
            'data_roots': [str(self.data)],
            'writable_data_roots': [str(self.data)]
        }
        self.open_board = Mock(return_value=SimpleNamespace(shape=(8, 5, 1060, 1882, 3), dtype='uint8'))
        self.validate = Mock()
        self.load_model = Mock(return_value=SimpleNamespace(latent_dim=64, init_filters=128, layers=[2, 2, 2]))
        self.load_skips = Mock(return_value={(0, 0), (7, 4)})
        self.inspect = Mock(side_effect=AssertionError('Preflight must not run inference'))
        self.modules = {}
        for name in ('src', 'src.configs', 'src.inspection', 'src.utils', 'src.utils.data', 'numpy', 'torch', 'yaml'):
            self.modules[name] = ModuleType(name)

        self.modules['src.configs'].InspectionConfig = SimpleNamespace(from_yaml=Mock(return_value=self.settings))
        self.modules['src.inspection'].validate_calibration = self.validate
        self.modules['src.inspection'].load_inspection_model = self.load_model
        self.modules['src.inspection'].inspect_board = self.inspect
        self.modules['src.utils.data'].open_board = self.open_board
        self.modules['src.utils.data'].GRID_SHAPE = (8, 5)
        self.modules['src.utils.data'].load_skipped_segments = self.load_skips
        self.modules['src.utils.data'].read_document = lambda path: json.loads(path.read_text())
        self.modules['numpy'].load = Mock(return_value=SimpleNamespace(shape=(8, 5)))
        self.modules['numpy'].isfinite = lambda array: SimpleNamespace(all=lambda: True)
        self.modules['torch'].device = lambda name: SimpleNamespace(type=name.split(':')[0], index=None)
        self.modules['torch'].cuda = SimpleNamespace(is_available=lambda: False)
        self.modules['yaml'].safe_load = lambda handle: json.load(handle)

    def _run(self) -> Dict:
        with patch.dict('sys.modules', self.modules), patch('pathlib.Path.cwd', return_value=self.root):
            return run_preflight(self.parameters)

    def _checks(self, report: Dict) -> Dict:
        return {item['name']: item for item in report['checks']}

    def _snapshot(self) -> Dict:
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.rglob('*') if path.is_file()}

    def test_ready_legacy_is_read_only_and_validates_model_on_cpu(self) -> None:
        before = self._snapshot()
        report = self._run()
        self.assertTrue(report['ready'], report)
        self.assertEqual(report['mode'], 'legacy')
        self.assertTrue(report['planned_writes'][0]['authorized'])
        self.assertEqual(report['planned_writes'][1]['path'], str(self.board.with_suffix('.inspection.lock')))
        self.assertFalse(self.board.with_suffix('.inspection.lock').exists())
        self.validate.assert_called_once_with(self.settings)
        self.assertEqual(self.load_model.call_args.args[3].type, 'cpu')
        self.inspect.assert_not_called()
        self.assertEqual(before, self._snapshot())

    def test_stale_calibration_blocks_weights_and_does_not_repair(self) -> None:
        self.validate.side_effect = ValueError('Dependencies changed; recalibrate.')
        before = self._snapshot()
        report = self._run()
        self.assertFalse(report['ready'])
        self.assertEqual(self._checks(report)['calibration_provenance']['code'], 'incompatible_calibration')
        self.assertEqual(self._checks(report)['checkpoint_compatibility']['status'], 'skipped')
        self.load_model.assert_not_called()
        self.assertEqual(before, self._snapshot())

    def test_missing_inputs_are_collected_before_loading_weights(self) -> None:
        self.board.unlink()
        self.checkpoint.unlink()
        (self.calibration / 'ae_threshold.npy').unlink()
        report = self._run()
        checks = self._checks(report)
        for name in ('board_file', 'checkpoint_file', 'ae_threshold.npy'):
            self.assertEqual(checks[name]['code'], 'missing_file')

        self.load_model.assert_not_called()

    def test_missing_config_and_dependency_are_distinct(self) -> None:
        self.parameters['config_path'] = str(self.root / 'absent.yaml')
        report = self._run()
        self.assertEqual(self._checks(report)['config_file']['code'], 'missing_file')
        self.parameters['config_path'] = str(self.config_path)
        self.modules['src.configs'] = None
        report = self._run()
        self.assertEqual(self._checks(report)['configuration']['code'], 'missing_dependency')

    def test_board_override_and_symlink_escape(self) -> None:
        alternate = self.data / 'alternate.npy'
        alternate.touch()
        self.parameters['board_path'] = str(alternate)
        self.assertEqual(self._run()['board_path'], str(alternate))
        alternate.unlink()
        alternate.symlink_to(self.checkpoint)
        report = self._run()
        self.assertEqual(self._checks(report)['board_file']['code'], 'path_outside_root')

    def test_explicit_sidecar_policy_is_required_without_probe_writes(self) -> None:
        self.parameters['writable_data_roots'] = []
        before = self._snapshot()
        report = self._run()
        self.assertFalse(report['ready'])
        self.assertEqual(self._checks(report)['sidecar_policy']['code'], 'sidecar_not_authorized')
        self.assertEqual(before, self._snapshot())
        self.load_model.assert_called_once()

    def test_symlink_sidecar_is_rejected(self) -> None:
        self.board.with_suffix('.inspection.json').symlink_to(self.skip_path)
        report = self._run()
        self.assertEqual(self._checks(report)['sidecar_policy']['code'], 'sidecar_not_authorized')

    def test_header_skip_and_threshold_failures_block_weights(self) -> None:
        self.open_board.side_effect = ValueError('Invalid board dtype')
        self.load_skips.side_effect = ValueError('Coordinate outside grid')
        self.modules['numpy'].load.return_value = SimpleNamespace(shape=(9, 4))
        report = self._run()
        checks = self._checks(report)
        for name in ('board_header', 'reference_header', 'skip_coordinates', 'ae_threshold.npy'):
            self.assertEqual(checks[name]['status'], 'blocked')

        self.load_model.assert_not_called()

    def test_checkpoint_mismatch_is_reported_without_inference(self) -> None:
        self.load_model.side_effect = ValueError('Latent dimension does not match checkpoint')
        report = self._run()
        self.assertEqual(self._checks(report)['checkpoint_compatibility']['code'], 'incompatible_checkpoint')
        self.assertFalse(report['ready'])
        self.inspect.assert_not_called()

    def test_unavailable_device_is_blocked(self) -> None:
        self.settings.device = 'cuda'
        self.assertEqual(self._checks(self._run())['device']['code'], 'device_unavailable')
        self.load_model.assert_not_called()

    def test_unsupported_configuration_stops_before_model_or_inference(self) -> None:
        self.config_path.write_text(json.dumps({'inspection': {'local_calibration_path': '/unsupported.json'}}))
        report = self._run()
        self.assertFalse(report['ready'])
        self.assertEqual(self._checks(report)['configuration']['code'], 'unsupported_capability')
        self.load_model.assert_not_called()
        self.inspect.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec('orchestral'), 'Orchestral is required only for the agent wrapper.')
class AgentToolTests(unittest.TestCase):
    def test_wrapper_preserves_blockers_and_separates_runtime_state(self) -> None:
        from tools.visual_inspection.agent_tools import VisualInspectionPreflightTool

        self.assertEqual(VisualInspectionPreflightTool._get_runtime_fields(), ['config_path', 'board_path'])
        response = {'status': 'ok', 'result': {'ready': False, 'checks': [{'code': 'incompatible_calibration'}]}}
        tool = VisualInspectionPreflightTool(
            visual_inspection_repo='/repo',
            visual_inspection_python='/python',
            visual_inspection_data_roots=['/repo/data'],
            visual_inspection_artifact_root='/artifacts'
        )
        with patch('tools.visual_inspection.agent_tools.preflight.BridgeConfig'), patch('tools.visual_inspection.agent_tools.preflight.InspectionBridge') as factory:
            factory.return_value.execute.return_value = response
            actual = json.loads(tool.execute(board_path='data/alternate.npy'))
            factory.return_value.execute.assert_called_with('preflight', {'config_path': 'configs/inspection.yaml', 'board_path': 'data/alternate.npy'})
            tool.execute()
            factory.return_value.execute.assert_called_with('preflight', {'config_path': 'configs/inspection.yaml'})

        self.assertEqual(actual, response)


if __name__ == '__main__':
    unittest.main()
