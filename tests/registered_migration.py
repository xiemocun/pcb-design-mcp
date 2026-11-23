"""Compatibility entry point for current registered-client verification."""
import asyncio
from registered_v09 import main
if __name__=="__main__":asyncio.run(main())
