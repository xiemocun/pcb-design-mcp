"""Compatibility entry point for the corrected ESP32 reference protocol."""
from esp32_reference_protocol import run
import asyncio
if __name__=="__main__":asyncio.run(run())
