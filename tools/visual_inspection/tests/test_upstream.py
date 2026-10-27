import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from tools.visual_inspection import BridgeError, load_upstream_config, resolve_upstream


class UpstreamTests(unittest.TestCase):
    def test_old_main_exports_resolve_from_explicit_locations(self) -> None:
        locations = {
            'InspectionConfig': ('src.configs', 'src.configs.inspection_config'),
            'validate_calibration': ('src.inspection', 'src.inspection.run'),
            'load_inspection_model': ('src.inspection', 'src.inspection.model'),
            'open_board': ('src.utils.data', 'src.utils.data.board_contract'),
            'GRID_SHAPE': ('src.utils.data', 'src.utils.data.board_contract')
        }
        for name, (package, implementation) in locations.items():
            with self.subTest(name=name):
                exported = object()
                modules = {package: ModuleType(package), implementation: SimpleNamespace(**{name: exported})}
                with patch('tools.visual_inspection.upstream.importlib.import_module', side_effect=modules.__getitem__):
                    self.assertIs(resolve_upstream(name), exported)
                    setattr(modules[package], name, 'preferred')
                    self.assertEqual(resolve_upstream(name), 'preferred')

    def test_dependency_failure_does_not_trigger_fallback(self) -> None:
        with patch('tools.visual_inspection.upstream.importlib.import_module', side_effect=ImportError('broken dependency')) as importer:
            with self.assertRaisesRegex(ImportError, 'broken dependency'):
                resolve_upstream('InspectionConfig')
            importer.assert_called_once_with('src.configs')

    def test_unsupported_options_are_rejected_before_old_config_loader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inspection.yaml'
            loader = Mock(return_value=SimpleNamespace())
            with patch('tools.visual_inspection.upstream.resolve_upstream', return_value=SimpleNamespace(from_yaml=loader)):
                for key in ('local_calibration_path', 'local_scoring_path', 'annotation_dir'):
                    path.write_text(f'inspection:\n  {key}: /unsupported\n')
                    with self.assertRaises(BridgeError):
                        load_upstream_config(path)
                loader.assert_not_called()
                path.write_text('inspection: {}\n')
                self.assertIs(load_upstream_config(path), loader.return_value)
