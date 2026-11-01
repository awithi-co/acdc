"""Release and runtime identity shared by the independently installed plugins."""

import hashlib
from pathlib import Path

VERSION = "0.6.0"
PROTOCOL_VERSION = 2


def _runtime_id():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


RUNTIME_ID = _runtime_id()
