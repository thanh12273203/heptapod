import json
from pathlib import Path

from orchestral.tools.base.field_utils import RuntimeField

from . import VisualInspectionPreflightTool
from .. import BridgeConfig, BridgeError, InspectionBridge


class InspectBoardTool(VisualInspectionPreflightTool):
    force: bool = RuntimeField(default=False, description="Recompute instead of reusing a current sidecar. Does not bypass calibration validation.")

    def _setup(self) -> None:
        self.name = 'InspectBoardTool'
        self.description = "Inspect one processed board using the configured upstream pipeline. Requires an authorized writable board root; saves a sidecar, result snapshot, and run record. Preserves uncertain regional decisions."

    def _run(self) -> str:
        try:
            config = BridgeConfig(
                repository=Path(self.visual_inspection_repo),
                python=Path(self.visual_inspection_python),
                data_roots=tuple(Path(root) for root in self.visual_inspection_data_roots),
                artifact_root=Path(self.visual_inspection_artifact_root),
                timeout_seconds=self.visual_inspection_timeout,
                writable_data_roots=tuple(Path(root) for root in self.visual_inspection_writable_data_roots)
            )
            parameters = {'config_path': self.config_path, 'force': self.force}
            if self.board_path is not None:
                parameters['board_path'] = self.board_path

            response = InspectionBridge(config).execute('inspect', parameters)
        except (BridgeError, OSError, TypeError) as error:
            return self.format_error(error="Inspection configuration failed", reason=str(error))

        if response['status'] == 'error':
            return self.format_error(
                error=response['error']['code'],
                reason=response['error']['message'],
                context=json.dumps(response['execution']),
                suggestion="Read the run record and stderr log; resolve the cause before retrying. Do not bypass calibration."
            )

        return json.dumps(response, allow_nan=False)
