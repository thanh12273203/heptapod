import json
from pathlib import Path
from typing import Optional

from orchestral.tools.base.field_utils import RuntimeField

from . import VisualInspectionToolState
from .. import BridgeConfig, BridgeError, InspectionBridge


class VisualInspectionPreflightTool(VisualInspectionToolState):
    config_path: str = RuntimeField(
        default='configs/inspection.yaml',
        description="Inspection YAML path, relative to the visual-inspection repository or absolute inside its read roots."
    )
    board_path: Optional[str] = RuntimeField(
        default=None,
        description="Optional processed-board path override; defaults to the board in the YAML."
    )

    def _setup(self) -> None:
        self.name = 'VisualInspectionPreflightTool'
        self.description = "Check inspection inputs, calibration, checkpoint compatibility, and sidecar policy without inference or data changes. A successful check may report ready=false."

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
            parameters = {'config_path': self.config_path}
            if self.board_path is not None:
                parameters['board_path'] = self.board_path

            response = InspectionBridge(config).execute('preflight', parameters)
        except (BridgeError, OSError, TypeError) as error:
            return self.format_error(error="Preflight configuration failed", reason=str(error))

        if response['status'] == 'error':
            return self.format_error(
                error=response['error']['code'],
                reason=response['error']['message'],
                context=json.dumps(response['execution']),
                suggestion="Check the configured environment and worker diagnostic logs."
            )

        # A completed preflight can legitimately report ready=false with blockers.
        return json.dumps(response, allow_nan=False)
