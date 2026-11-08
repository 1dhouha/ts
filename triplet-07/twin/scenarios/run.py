"""Launch one existing scenario through the shared Twin dashboard and MCP runtime."""

import argparse
from pathlib import Path
import runpy
import sys


ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    if not any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--scenario", required=True)
        parser.parse_known_args()
    runpy.run_path(str(ROOT / "tools" / "run_twin.py"), run_name="__main__")
