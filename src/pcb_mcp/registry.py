"""Process-local catalog context and immutable extension assets (Python 3.9)."""
import contextlib
import contextvars
import hashlib
import json
from pathlib import Path
import re
from .config import data_dir

_root = contextvars.ContextVar('pcb_catalog_data_root', default=None)
NAME = r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}'


def name(value):
    if not isinstance(value, str) or not re.fullmatch(NAME, value) or value in ('.', '..'):
        raise ValueError('INVALID_LIBRARY_NAME')
    return value


def root():
    return Path(_root.get() or data_dir()).resolve()


@contextlib.contextmanager
def using(folder):
    token = _root.set(Path(folder).resolve())
    try:
        yield
    finally:
        _root.reset(token)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bundles():
    result = []
    for folder in sorted((root() / 'catalog_packages').glob('*')):
        if not re.fullmatch('[a-f0-9]{64}', folder.name):
            continue  # Incomplete imports stay in a separate staging directory.
        path = folder / 'manifest.json'
        manifest = json.loads(path.read_text())
        if sha(canonical(manifest)) != folder.name:
            raise ValueError('CATALOG_PACKAGE_CHANGED: ' + folder.name)
        for asset in manifest['assets']:
            relative = Path(asset['path'])
            file = folder / relative
            if relative.is_absolute() or '..' in relative.parts or file.is_symlink() or not file.resolve().is_relative_to(folder.resolve()):
                raise ValueError('INVALID_PACKAGE_ASSET_PATH')
            if not file.is_file() or sha(file.read_bytes()) != asset['sha256']:
                raise ValueError('CATALOG_ASSET_CHANGED: ' + asset['path'])
        result.append((manifest, folder))
    return result


def library_paths(kind):
    result = {}
    for manifest, folder in bundles():
        for asset in manifest['assets']:
            path = folder / asset['path']
            if kind == 'symbol' and asset['kind'] == 'symbol':
                key, target = name(path.stem), path
            elif kind == 'footprint' and asset['kind'] == 'footprint':
                key, target = name(path.parent.name.removesuffix('.pretty')), path.parent
            else:
                continue
            if key in result and result[key] != target:
                raise ValueError('CATALOG_LIBRARY_CONFLICT: ' + key)
            result[key] = target
    return result


def selected_libraries():
    path = root() / 'selected_symbol_libraries.json'
    return [name(x) for x in json.loads(path.read_text())] if path.is_file() else []


def referenced_libraries():
    return sorted({name(p['symbol_id'].split(':', 1)[0]) for manifest, _ in bundles() for p in manifest['parts']})
