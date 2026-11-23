"""Stable absolute-path entry point for MCP hosts; no working-directory assumption."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from pcb_mcp.server import main

if __name__=='__main__':
    main()
