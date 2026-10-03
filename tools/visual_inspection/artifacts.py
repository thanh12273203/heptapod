import json
import os
import tempfile
from pathlib import Path
from typing import Dict


def write_json_atomic(path: Path, document: Dict) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(document, handle, allow_nan=False, indent=2)

        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
