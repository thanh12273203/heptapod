import json
from typing import List

from orchestral.tools.base.field_utils import StateField
from orchestral.tools.base.tool import BaseTool
from pydantic import field_validator


class VisualInspectionToolState(BaseTool):
    visual_inspection_repo: str = StateField(description="Absolute visual-inspection repository path.")
    visual_inspection_python: str = StateField(description="Absolute Python executable path in the visual-inspection environment.")
    visual_inspection_data_roots: List[str] = StateField(description="Absolute directories permitted for board reads.")
    visual_inspection_artifact_root: str = StateField(description="Absolute root for HEPTAPOD bridge logs and responses.")
    visual_inspection_writable_data_roots: List[str] = StateField(
        default_factory=list,
        description="Explicit writable board directories inside read roots; empty means no inspection sidecars are authorized."
    )
    visual_inspection_timeout: float = StateField(default=60.0, description="Maximum worker runtime in seconds.")

    @field_validator('visual_inspection_data_roots', 'visual_inspection_writable_data_roots', mode='before')
    @classmethod
    def _parse_roots(cls, value: object) -> List[str]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as error:
                raise ValueError("Root configuration must be a JSON array of absolute path strings.") from error

        if not isinstance(value, list) or any(not isinstance(root, str) or not root.strip() for root in value):
            raise ValueError("Root configuration must be a list of nonempty path strings.")

        return value
