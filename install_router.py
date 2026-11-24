"""Source-checkout wrapper for the installed ``pcb-router`` entry point.

The implementation lives in ``pcb_mcp.router_install`` so a wheel install
places the JAR where ``pcb_service`` actually reads it. Wheel users run
``pcb-router`` directly; this wrapper only exists for checkout convenience.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from pcb_mcp.router_install import main

if __name__ == '__main__':
    raise SystemExit(main())
