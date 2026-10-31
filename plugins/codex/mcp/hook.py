"""Keep unsupported hosts and missing prerequisites out of CLI hook failures.

This entry point intentionally uses only older Python syntax and the standard
library so version/platform checks run before importing the Linux runtime.
"""

import runpy
from pathlib import Path
import shutil
import sys


def main():
    reason = None
    if sys.platform != "linux" or sys.version_info < (3, 11):
        reason = "CLI collaboration requires Linux and Python 3.11+; recall skills remain available."
    elif shutil.which("uv") is None:
        reason = "Install uv and reopen this session to enable CLI collaboration."
    if reason:
        if len(sys.argv) > 1 and sys.argv[1] == "start":
            print("ACDC: " + reason, file=sys.stderr)
        return 0
    runpy.run_path(str(Path(__file__).with_name("session.py")), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
