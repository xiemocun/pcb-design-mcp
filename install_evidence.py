"""Source-checkout wrapper for the installed ``pcb-evidence`` entry point.

The implementation lives in ``pcb_mcp.evidence_install`` so a wheel install
restores evidence into the asset root it actually reads. Wheel users run
``pcb-evidence`` directly; this wrapper only exists for checkout convenience.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from pcb_mcp.evidence_install import main

if __name__ == '__main__':
    raise SystemExit(main())
