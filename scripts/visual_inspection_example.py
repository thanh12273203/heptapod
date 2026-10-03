import argparse
import json
from pathlib import Path

from tools.visual_inspection import BridgeConfig, BridgeError, InspectionBridge


def main() -> int:
    parser = argparse.ArgumentParser(description="Run HEPTAPOD preflight, explicit inspection, or saved-result reading with a JSON bridge configuration.")
    parser.add_argument('--bridge-config', type=Path, required=True)
    commands = parser.add_subparsers(dest='operation', required=True)
    for name in ('preflight', 'inspect'):
        command = commands.add_parser(name)
        command.add_argument('--config-path', default='configs/inspection.yaml')
        command.add_argument('--board-path')
        if name == 'inspect':
            command.add_argument('--force', action='store_true')

    reader = commands.add_parser('read-result')
    reader.add_argument('result_path')
    args = parser.parse_args()
    report = {'schema_version': 'visual-inspection-example/1', 'status': 'error', 'steps': []}
    try:
        settings = json.loads(args.bridge_config.read_text())
        config = BridgeConfig(
            repository=Path(settings['repository']),
            python=Path(settings['python']),
            data_roots=tuple(Path(root) for root in settings['data_roots']),
            artifact_root=Path(settings['artifact_root']),
            writable_data_roots=tuple(Path(root) for root in settings.get('writable_data_roots', [])),
            timeout_seconds=settings.get('timeout_seconds', 60)
        )
        bridge = InspectionBridge(config)
        if args.operation == 'read-result':
            result = bridge.execute('read_result', {'result_path': args.result_path})
            report['steps'].append(result)
            report['status'] = result['status']
        else:
            parameters = {'config_path': args.config_path}
            if args.board_path:
                parameters['board_path'] = args.board_path

            preflight = bridge.execute('preflight', parameters)
            report['steps'].append(preflight)
            if preflight['status'] == 'ok':
                report['status'] = 'ok' if preflight['result']['ready'] else 'blocked'
            if args.operation == 'inspect' and report['status'] == 'ok':
                inspected = bridge.execute('inspect', {**parameters, 'force': args.force})
                report['steps'].append(inspected)
                report['status'] = inspected['status']
                if inspected['status'] == 'ok':
                    saved = bridge.execute('read_result', {'result_path': inspected['result']['result_path']})
                    report['steps'].append(saved)
                    report['status'] = saved['status']

    except (BridgeError, OSError, ValueError, TypeError, KeyError) as error:
        report['status'] = 'error'
        report['error'] = str(error)

    print(json.dumps(report, indent=2, allow_nan=False))
    return {'ok': 0, 'blocked': 2, 'error': 1}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
