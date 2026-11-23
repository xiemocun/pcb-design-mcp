"""Runtime paths shared by source checkouts and installed distributions.

Keep this module usable by KiCad's Python 3.9 worker as well as Python 3.12.
"""
import os
from pathlib import Path
import shutil
import sys

PACKAGE = Path(__file__).resolve().parent
CHECKOUT = PACKAGE.parents[1]
ROUTER_VERSION = '2.4.1'
ROUTER_SHA256 = '251101c3eeac22d7e7dfcf6796603279e5d1000283eb82d8f093780f7afc6aa9'


def asset_root():
    override = os.environ.get('PCB_MCP_ASSET_ROOT')
    if override:
        return Path(override).expanduser().resolve()
    if (PACKAGE / '_assets/catalog/parts.json').is_file():
        return PACKAGE / '_assets'
    return CHECKOUT


def user_root():
    if (CHECKOUT / 'src/pcb_mcp').resolve() == PACKAGE and (CHECKOUT / 'catalog').is_dir():
        return CHECKOUT
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/PCB Design MCP'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'pcb-design-mcp'


def data_dir():
    return Path(os.environ.get('PCB_MCP_DATA_DIR', str(user_root() / 'data'))).expanduser().resolve()


def java_path():
    if os.environ.get('PCB_MCP_JAVA'):
        return Path(os.environ['PCB_MCP_JAVA']).expanduser().resolve()
    local = CHECKOUT / 'runtime/java/bin/java'
    return local if local.is_file() else Path(shutil.which('java') or '/usr/bin/java')


def reviewer_binary():
    return Path(os.environ.get('PCB_MCP_REVIEWER', str(user_root() / 'reviewer/pcb-reviewer'))).expanduser().resolve()
