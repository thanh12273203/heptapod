from typing import Dict


class BridgeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def to_dict(self) -> Dict[str, str]:
        return {'code': self.code, 'message': str(self)}
