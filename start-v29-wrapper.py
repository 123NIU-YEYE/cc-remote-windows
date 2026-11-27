"""Compatibility entry point; use the checkout's .venv Python to run this file."""
from pathlib import Path
import os
import runpy
import sys

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    runpy.run_module("cc_remote.wrapper", run_name="__main__", alter_sys=True)
