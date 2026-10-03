import json
from pathlib import Path

from orchestral.tools.base.field_utils import RuntimeField

from . import VisualInspectionToolState
from .. import BridgeConfig, BridgeError, InspectionBridge


class ReadInspectionResultTool(VisualInspectionToolState):
    result_path: str = RuntimeField(description="Saved inspection JSON inside configured data or artifact roots. Relative paths resolve against the upstream repository. Reading does not verify freshness.")

    def _setup(self) -> None:
        self.name = 'ReadInspectionResultTool'
        self.description = "Read a saved inspection result with upstream structural validation, source coordinates, and bounded evidence. Preserve regional uncertainty. Freshness is unverified; full evidence is saved in a per-run snapshot."

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
            response = InspectionBridge(config).execute('read_result', {'result_path': self.result_path})
        except (BridgeError, OSError, TypeError) as error:
            return self.format_error(error="Result reader configuration failed", reason=str(error))

        if response['status'] == 'error':
            return self.format_error(
                error=response['error']['code'],
                reason=response['error']['message'],
                context=json.dumps(response['execution']),
                suggestion="Check the configured environment and worker diagnostic logs."
            )

        return json.dumps(response, allow_nan=False)
