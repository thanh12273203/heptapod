import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('toolbase'), 'toolbase is optional')
class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        import yaml

        self.metadata = yaml.safe_load((Path(__file__).resolve().parents[3] / 'toolkit.yaml').read_text())

    def test_metadata_and_bundle_gating(self) -> None:
        from toolbase.serve.bundles import evaluate_bundles
        from toolbase.validation import ToolkitMetadata
        from toolbase.setup.schema import parse_config_block

        with patch('toolbase.validation.get_allowed_categories', return_value=['hep']):
            ToolkitMetadata(**self.metadata)

        parse_config_block(self.metadata['config'])
        bundle = self.metadata['bundles']['visual_inspection']
        self.assertFalse(bundle.get('deps'))
        config = {key: '/configured' for key in bundle['requires']}
        self.assertIn('visual_inspection', evaluate_bundles(self.metadata['bundles'], config).available_bundles)
        for key in bundle['requires']:
            incomplete = {name: value for name, value in config.items() if name != key}
            self.assertIn('visual_inspection', evaluate_bundles(self.metadata['bundles'], incomplete).dropped_bundles)
            self.assertIn('units', evaluate_bundles(self.metadata['bundles'], incomplete).available_bundles)

        entries = [entry for entry in self.metadata['tools'] if entry.get('bundle') == 'visual_inspection']
        self.assertEqual({entry['name'] for entry in entries}, {'VisualInspectionPreflightTool', 'InspectBoardTool', 'ReadInspectionResultTool'})

    def test_root_config_parsing_and_assignment(self) -> None:
        from tools.visual_inspection.agent_tools import InspectBoardTool, ReadInspectionResultTool, VisualInspectionPreflightTool

        for tool_class in (InspectBoardTool, ReadInspectionResultTool, VisualInspectionPreflightTool):
            tool = tool_class(
                visual_inspection_repo='/upstream',
                visual_inspection_python='/upstream/venv/bin/python',
                visual_inspection_data_roots=json.dumps(['/data with spaces']),
                visual_inspection_artifact_root='/runs'
            )
            self.assertEqual(tool.visual_inspection_data_roots, ['/data with spaces'])
            self.assertEqual(tool.visual_inspection_writable_data_roots, [])
            tool.visual_inspection_writable_data_roots = '["/data with spaces"]'
            self.assertEqual(tool.visual_inspection_writable_data_roots, ['/data with spaces'])
            self.assertFalse(set(tool.get_input_schema()['properties']) & set(tool._get_state_fields()))
            for bad in ('/data', '{}', '[2]', '[""]'):
                with self.assertRaises(ValueError):
                    tool.visual_inspection_data_roots = bad

    def test_skill_discovery_and_bundle_gating(self) -> None:
        from toolbase.skills import SkillTarget, discover_skills, parse_frontmatter, surface_skills

        root = Path(__file__).resolve().parents[3]
        self.assertTrue(any(source.slug == 'visual-inspection' for source in discover_skills(root)))
        with tempfile.TemporaryDirectory() as directory:
            target = SkillTarget('test', Path(directory), 'dir', True)
            absent = surface_skills('heptapod', root, target, available_bundles=set())
            self.assertNotIn('heptapod__visual-inspection', absent)
            present = surface_skills('heptapod', root, target, available_bundles={'visual_inspection'})
            self.assertIn('heptapod__visual-inspection', present)
            surfaced = Path(directory) / 'heptapod__visual-inspection' / 'SKILL.md'
            frontmatter, body = parse_frontmatter(surfaced.read_text())
            self.assertTrue(frontmatter.is_complete())
            self.assertIsNone(frontmatter.bundle)
            self.assertIn('ReadInspectionResult', body)
