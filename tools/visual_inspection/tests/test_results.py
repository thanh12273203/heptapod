import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict
from unittest.mock import patch

from tools.visual_inspection import BridgeConfig, InspectionBridge


READER_SOURCE = '''
import json
from pathlib import Path

class InspectionResults:
    @classmethod
    def load_json(cls, path):
        payload = json.loads(Path(path).read_text())
        if payload.get('reject_by_upstream'):
            raise ValueError('Synthetic upstream rejection')
        result = cls()
        result.payload = payload
        return result

    def summary(self):
        if self.payload.get('schema_version') == 2:
            return self.payload['local']['counts']
        return {'pixel_flagged': 1, 'autoencoder_flagged': 1, 'hybrid_flagged': 1, 'skipped': 1}
'''


def saved_result(local: bool = False) -> Dict:
    grid = [[0 for _ in range(5)] for _ in range(8)]
    grid[0][0] = -1
    grid[1][2] = 1
    payload = {
        'pw_flags': grid, 'ae_flags': grid, 'hybrid_flags': grid,
        'baseline_path': '/unavailable/reference.npy',
        'inspected_path': '/unavailable/board.npy',
        'metadata': {'board_shape': [8, 5], 'signature': {'checkpoint': 'obsolete'}}
    }
    if local:
        decisions = [['ok' for _ in range(5)] for _ in range(8)]
        decisions[0][0] = 'skipped'
        decisions[1][2] = 'flagged'
        decisions[2][3] = 'uncertain'
        segments = [
            {'row': row, 'col': col, 'decision': decision, 'reasons': ['coverage_missing'] if decision == 'uncertain' else [], 'regions': [{'score': 0.4}]}
            for row, values in enumerate(decisions) for col, decision in enumerate(values) if decision != 'skipped'
        ]
        payload.update(schema_version=2, local={
            'schema_version': 1, 'decision_grid': decisions,
            'counts': {'ok': 37, 'flagged': 1, 'uncertain': 1, 'skipped': 1},
            'segments': segments, 'reference_identity': 'reference-v1'
        })

    return payload


class ResultReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix='reader tests ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        (self.repo / 'src' / 'inspection').mkdir(parents=True)
        (self.repo / 'src' / 'inspection' / '__init__.py').write_text('raise AssertionError("Inference package must not be imported")\n')
        (self.repo / 'src' / '__init__.py').write_text('')
        (self.repo / 'src' / 'utils').mkdir()
        (self.repo / 'src' / 'utils' / '__init__.py').write_text(READER_SOURCE)
        self.data = self.repo / 'data'
        self.data.mkdir()
        self.config = BridgeConfig(
            repository=self.repo,
            python=Path(sys.executable),
            data_roots=(self.data,),
            artifact_root=self.root / 'artifacts'
        )
        self.source = self.data / 'saved result.json'

    def read(self, payload: Dict) -> Dict:
        self.source.write_text(json.dumps(payload))
        return InspectionBridge(self.config).execute('read_result', {'result_path': str(self.source)})

    def test_legacy_read_without_board_or_write_authorization(self) -> None:
        response = self.read(saved_result())
        self.assertEqual(response['status'], 'ok', response)
        result = response['result']
        self.assertEqual(result['upstream_schema_version'], 1)
        self.assertEqual(result['coordinates']['pw_flags'], [[1, 2]])
        self.assertEqual(result['freshness']['status'], 'unverified')
        self.assertEqual(result['validation']['status'], 'valid')
        self.assertEqual(Path(result['result_path']).read_bytes(), self.source.read_bytes())
        self.assertEqual(list(self.data.iterdir()), [self.source])
        self.assertTrue(Path(response['execution']['record_path']).is_file())

    def test_regional_uncertainty_and_evidence(self) -> None:
        response = self.read(saved_result(True))
        self.assertEqual(response['status'], 'ok', response)
        result = response['result']
        self.assertEqual(result['summary']['uncertain'], 1)
        self.assertEqual(result['hybrid_flags'][2][3], 0)
        self.assertEqual(result['coordinates']['uncertain'], [[2, 3]])
        self.assertNotIn([2, 3], result['coordinates']['ok'])
        evidence = next(item for item in result['evidence']['segments'] if item['row'] == 2 and item['col'] == 3)
        self.assertEqual(evidence['reasons'], ['coverage_missing'])
        self.assertTrue(result['evidence']['complete'])

    def test_evidence_is_bounded_and_complete_snapshot_retained(self) -> None:
        payload = saved_result(True)
        payload['local']['segments'][0]['large_evidence'] = 'x' * 200000
        payload['metadata']['large_metadata'] = 'y' * 200000
        response = self.read(payload)
        self.assertEqual(response['status'], 'ok', response)
        result = response['result']
        self.assertFalse(result['evidence']['complete'])
        self.assertIn('metadata', result['evidence']['omitted_fields'])
        self.assertEqual(result['evidence']['omitted_segment_coordinates'], [[0, 1]])
        self.assertLess(len(json.dumps(response)), 180000)
        self.assertEqual(json.loads(Path(result['result_path']).read_text()), payload)

    def test_artifact_snapshot_can_be_read_again(self) -> None:
        first = self.read(saved_result(True))
        second = InspectionBridge(self.config).execute('read_result', {'result_path': first['result']['result_path']})
        self.assertEqual(second['status'], 'ok', second)
        self.assertEqual(first['result']['source_sha256'], second['result']['source_sha256'])

    def test_relative_path_and_nullable_legacy_local_field(self) -> None:
        payload = saved_result()
        payload['local'] = None
        self.source.write_text(json.dumps(payload))
        before = self.source.stat()
        response = InspectionBridge(self.config).execute('read_result', {'result_path': 'data/saved result.json'})
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(response['result']['mode'], 'legacy')
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)

    def test_strict_json_and_upstream_errors(self) -> None:
        for raw in ('{}', '{"a": 1, "a": 2}', '{"a": NaN}', '{"a": 1e999}', '[' * 2000):
            with self.subTest(raw=raw[:40]):
                self.source.write_text(raw)
                response = InspectionBridge(self.config).execute('read_result', {'result_path': str(self.source)})
                self.assertEqual(response['error']['code'], 'invalid_result')

        payload = saved_result()
        payload['reject_by_upstream'] = True
        response = self.read(payload)
        self.assertEqual(response['error']['code'], 'invalid_result')
        self.assertIn('Synthetic upstream rejection', Path(response['execution']['stderr_path']).read_text())

    def test_missing_outside_and_symlink_escape(self) -> None:
        outside = self.root / 'outside.json'
        outside.write_text(json.dumps(saved_result()))
        self.source.symlink_to(outside)
        for path in (outside, self.source, self.data / 'missing.json'):
            response = InspectionBridge(self.config).execute('read_result', {'result_path': str(path)})
            self.assertEqual(response['status'], 'error')

    def test_oversized_input_rejected(self) -> None:
        with self.source.open('wb') as handle:
            handle.truncate(32 * 1024 * 1024 + 1)

        response = InspectionBridge(self.config).execute('read_result', {'result_path': str(self.source)})
        self.assertEqual(response['error']['code'], 'result_too_large')

    def test_runtime_cannot_override_roots(self) -> None:
        for parameters in ({}, {'result_path': ''}, {'result_path': 3}, {'result_path': 'x', 'result_roots': ['/']}):
            response = InspectionBridge(self.config).execute('read_result', parameters)
            self.assertEqual(response['error']['code'], 'invalid_request')
            self.assertIsNone(response['execution']['run_directory'])

    @unittest.skipUnless(importlib.util.find_spec('orchestral'), 'Orchestral is optional')
    def test_agent_wrapper(self) -> None:
        from tools.visual_inspection.agent_tools import ReadInspectionResultTool

        tool = ReadInspectionResultTool(
            visual_inspection_repo=str(self.repo),
            visual_inspection_python=sys.executable,
            visual_inspection_data_roots=[str(self.data)],
            visual_inspection_artifact_root=str(self.config.artifact_root)
        )
        self.assertEqual(set(tool.get_input_schema()['properties']), {'result_path'})
        with patch('tools.visual_inspection.agent_tools.read_result.InspectionBridge') as bridge:
            bridge.return_value.execute.return_value = {'status': 'ok', 'result': {}}
            self.assertEqual(json.loads(tool.execute(result_path='data/result.json'))['status'], 'ok')
            bridge.return_value.execute.assert_called_once_with('read_result', {'result_path': 'data/result.json'})


@unittest.skipUnless(os.environ.get('VISUAL_INSPECTION_TEST_REPO'), 'Optional actual upstream loader integration')
class UpstreamReaderTests(unittest.TestCase):
    def test_actual_loader_legacy_local_and_malformed(self) -> None:
        repository = Path(os.environ['VISUAL_INSPECTION_TEST_REPO'])
        interpreter = Path(os.environ['VISUAL_INSPECTION_TEST_PYTHON'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = BridgeConfig(repository, interpreter, (root,), root / 'runs')
            source = root / 'result.json'
            for local in (False, True):
                payload = saved_result(local)
                source.write_text(json.dumps(payload))
                response = InspectionBridge(config).execute('read_result', {'result_path': str(source)})
                self.assertEqual(response['status'], 'ok', response)
                self.assertEqual(response['result']['freshness']['status'], 'unverified')
                if local:
                    self.assertEqual(response['result']['coordinates']['uncertain'], [[2, 3]])
                    payload['local']['counts']['uncertain'] = 0
                else:
                    payload['pw_flags'][0][1] = 7

                source.write_text(json.dumps(payload))
                response = InspectionBridge(config).execute('read_result', {'result_path': str(source)})
                self.assertEqual(response['error']['code'], 'invalid_result')
