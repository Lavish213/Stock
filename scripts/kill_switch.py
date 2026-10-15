"""Compatibility entry point for the documented command."""
import runpy
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
if __name__ == "__main__":
    runpy.run_path(str(root / "kill_switch.py"), run_name="__main__")
