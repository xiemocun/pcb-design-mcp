"""Source-bound catalog packages. Importing documents never executes model code."""
import base64
import binascii
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Annotated, Literal
from urllib.parse import urlsplit
from pydantic import Field, model_validator
from .models import Strict
from . import registry
from .common import require

Label = Annotated[str, Field(min_length=1, max_length=200)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}:[^:/\\\x00-\x1f]{1,150}$')]


class Asset(Strict):
    path: Annotated[str, Field(min_length=1, max_length=240)]
    kind: Literal['document', 'symbol', 'footprint', 'spice_model']
    source_url: Annotated[str, Field(min_length=8, max_length=2000)]
    sha256: Hash
    content_base64: Annotated[str, Field(min_length=1, max_length=28_000_000)]

    @model_validator(mode='after')
    def validate_asset(self):
        path = PurePosixPath(self.path)
        if path.is_absolute() or '..' in path.parts or '\\' in self.path or str(path) != self.path or any(ord(c) < 32 for c in self.path):
            raise ValueError('INVALID_ASSET_PATH')
        if self.path == 'manifest.json':
            raise ValueError('RESERVED_ASSET_PATH')
        url = urlsplit(self.source_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('HTTPS_SOURCE_URL_REQUIRED')
        return self


class Evidence(Strict):
    asset: str
    locator: Annotated[str, Field(min_length=3, max_length=1000)]
    interpretation: Annotated[str, Field(min_length=5, max_length=2000)]


class ImportedPart(Strict):
    manufacturer: Label
    mpn: Annotated[str, Field(min_length=1, max_length=80)]
    kind: Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
    symbol_id: Identifier
    footprint_id: Identifier
    symbol_definition_hash: Hash
    footprint_definition_hash: Hash
    pin_mapping: Annotated[dict[str, str], Field(min_length=1, max_length=200)]
    facts: dict
    evidence: dict[str, list[Evidence]]
    footprint_variant_of: Hash | None = None

    @model_validator(mode='after')
    def validate_facts(self):
        reserved = {'footprint_definition_hash', 'symbol_definition_hash', 'pin_mapping', 'import_record'}
        if reserved & set(self.facts):
            raise ValueError('RESERVED_FACT_NAME')
        # Each input fact has a traceable location, including identity and pinout.
        needed = set(self.facts) | {'identity', 'pin_mapping', 'package'}
        if set(self.evidence) != needed or any(not refs for refs in self.evidence.values()):
            raise ValueError('EACH_FACT_REQUIRES_EVIDENCE')
        def inspect(value, depth=0):
            if depth > 10:
                raise ValueError('FACT_NESTING_LIMIT')
            if isinstance(value, dict):
                if len(value) > 200 or any(not isinstance(k, str) or len(k) > 200 for k in value):
                    raise ValueError('INVALID_FACT_OBJECT')
                for item in value.values(): inspect(item, depth + 1)
            elif isinstance(value, list):
                if len(value) > 200: raise ValueError('FACT_LIST_LIMIT')
                for item in value: inspect(item, depth + 1)
            elif type(value) is float:
                if not math.isfinite(value): raise ValueError('NONFINITE_FACT')
            elif value is not None and type(value) not in (str, int, bool):
                raise ValueError('INVALID_FACT_VALUE')
        inspect(self.facts)
        key = {'resistor': 'value_ohm', 'capacitor': 'capacitance_f'}.get(self.kind)
        if key and (type(self.facts.get(key)) not in (int, float) or not self.facts[key] > 0):
            raise ValueError('PASSIVE_VALUE_REQUIRED')
        if any(not k or not v for k, v in self.pin_mapping.items()):
            raise ValueError('EMPTY_PIN_MAPPING')
        return self


class CatalogPackage(Strict):
    format: Literal['pcb-catalog-package-v1'] = 'pcb-catalog-package-v1'
    name: Annotated[str, Field(pattern=r'^EXT_[A-Za-z0-9_]{1,60}$')]
    description: Annotated[str, Field(min_length=5, max_length=2000)]
    assets: Annotated[list[Asset], Field(min_length=1, max_length=32)]
    parts: Annotated[list[ImportedPart], Field(min_length=1, max_length=100)]

    @model_validator(mode='after')
    def unique(self):
        paths = [a.path for a in self.assets]
        if len(set(paths)) != len(paths): raise ValueError('DUPLICATE_ASSET_PATH')
        mpns = [(p.manufacturer.casefold(),p.mpn.casefold()) for p in self.parts]
        if len(set(mpns)) != len(mpns): raise ValueError('DUPLICATE_MPN')
        assets = {a.path: a for a in self.assets}
        for part in self.parts:
            for refs in part.evidence.values():
                for ref in refs:
                    if ref.asset not in assets or assets[ref.asset].kind != 'document':
                        raise ValueError('FACT_REQUIRES_DOCUMENT_SOURCE')
        for asset in self.assets:
            path = PurePosixPath(asset.path)
            if asset.kind == 'symbol' and asset.path != 'symbols/' + self.name + '.kicad_sym':
                raise ValueError('SYMBOL_NAMESPACE_REQUIRED')
            if asset.kind == 'footprint' and (len(path.parts) != 3 or path.parts[0] != 'footprints' or path.parts[1] != self.name + '.pretty' or path.suffix != '.kicad_mod'):
                raise ValueError('FOOTPRINT_NAMESPACE_REQUIRED')
        return self


def validate_geometry(package, stage):
    from . import backend, footprints
    symbols = {}
    custom = stage / 'symbols' / (package.name + '.kicad_sym')
    if custom.exists():
        stat = custom.stat()
        symbols.update({c['library_id']: c for c in backend._catalog_library(str(custom), stat.st_mtime_ns, stat.st_size)})
    for part in package.parts:
        if part.symbol_id not in symbols:
            library = part.symbol_id.split(':', 1)[0]
            symbols.update({c['library_id']: c for c in backend.catalog([library])})
        symbol = symbols.get(part.symbol_id)
        require(symbol is not None and symbol['supported'], 'IMPORT_SYMBOL_UNSUPPORTED', part.symbol_id)
        require(symbol['definition_hash'] == part.symbol_definition_hash, 'IMPORT_SYMBOL_CHANGED', part.symbol_id)
        lib, fname = part.footprint_id.split(':', 1)
        if lib == package.name:
            file = stage / 'footprints' / (lib + '.pretty') / (fname + '.kicad_mod')
            require(file.is_file(), 'IMPORT_FOOTPRINT_MISSING', part.footprint_id)
            footprint = footprints.load(part.footprint_id, file)
        else:
            entries = footprints.entries(part.footprint_id, library=lib)
            require(len(entries) == 1, 'IMPORT_FOOTPRINT_MISSING', part.footprint_id)
            footprint = footprints.load(*entries[0])
        require(footprint['supported'] and footprint['definition_hash'] == part.footprint_definition_hash, 'IMPORT_FOOTPRINT_CHANGED', part.footprint_id)
        require(footprints.pins(symbol) == set(part.pin_mapping) == {p['number'] for p in footprint['pads']}, 'IMPORT_PIN_MAP_MISMATCH', part.mpn)
        for pin in symbol['pins']:
            allowed={pin['name'].casefold()}
            if pin['electrical_type']=='passive' and pin['name'] in ('~',''):
                allowed.add('passive')
            require(all(part.pin_mapping[n].casefold() in allowed for n in pin['physical_numbers']), 'IMPORT_PIN_ROLE_MISMATCH', part.mpn)
        require(footprints.binding_matches(symbol, footprint), 'IMPORT_PACKAGE_FILTER_MISMATCH', part.mpn)


def validate_variants(incoming, existing):
    """A land-pattern variant adds an identity; it cannot overwrite electrical facts."""
    reserved={'pin_mapping','footprint_definition_hash','symbol_definition_hash','import_record'}
    for p in incoming:
        same=[q for q in existing if (q['manufacturer'].casefold(),q['mpn'].casefold())==(p.manufacturer.casefold(),p.mpn.casefold())]
        if not p.footprint_variant_of:
            require(not same,'IMPORT_MPN_CONFLICT','同一厂家的已有型号不能通过另一资料包覆盖。')
            continue
        base=next((q for q in same if q['definition_hash']==p.footprint_variant_of),None)
        require(base is not None,'IMPORT_VARIANT_BASE_MISSING','封装变体必须绑定现有同厂同型号的完整身份哈希。')
        require(base['symbol_id']==p.symbol_id and base['kind']==p.kind and base['facts'].get('symbol_definition_hash')==p.symbol_definition_hash and base['facts'].get('pin_mapping')==p.pin_mapping and {k:v for k,v in base['facts'].items() if k not in reserved}==p.facts,'IMPORT_VARIANT_ELECTRICAL_CHANGE','封装变体不能更改符号、引脚或任何电气事实。')
        require(p.footprint_id!=base['footprint_id'] and p.footprint_definition_hash!=base['facts'].get('footprint_definition_hash'),'IMPORT_VARIANT_NOT_DISTINCT','变体必须新增独立封装身份，不能替换原封装。')


def install(package, data_root):
    """Caller holds Service.locked; only content-addressed, validated files commit."""
    from . import parts
    with registry.using(data_root):
        manifest = package.model_dump(mode='json')
        payload = {}
        total = 0
        for asset in manifest['assets']:
            try:
                content = base64.b64decode(asset.pop('content_base64'), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError('INVALID_BASE64_ASSET') from exc
            require(registry.sha(content) == asset['sha256'], 'IMPORT_ASSET_HASH_MISMATCH', asset['path'])
            total += len(content)
            require(total <= 40 * 1024 * 1024, 'IMPORT_SIZE_LIMIT', '资料包超过40MiB。')
            if asset['kind'] in ('symbol', 'footprint', 'spice_model'):
                content.decode('utf-8')
            payload[asset['path']] = content
        digest = registry.sha(registry.canonical(manifest))
        destination = Path(data_root) / 'catalog_packages' / digest
        if destination.exists():
            registry.bundles()  # Idempotence does not waive corruption checks.
            return dict(package_id=digest, installed=False, part_count=len(package.parts))
        existing = parts.catalog()
        validate_variants(package.parts, existing)
        require(not any(m['name'] == package.name for m, _ in registry.bundles()), 'IMPORT_NAMESPACE_CONFLICT', package.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=destination.parent))
        try:
            for relative, content in payload.items():
                file = stage / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content)
            validate_geometry(package, stage)
            (stage / 'manifest.json').write_bytes(registry.canonical(manifest))
            stage.rename(destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
        return dict(package_id=digest, installed=True, part_count=len(package.parts))


def records():
    from . import backend, footprints
    result = []
    for manifest, folder in registry.bundles():
        assets = {a['path']: a for a in manifest['assets']}
        for part in manifest['parts']:
            fp_entries = footprints.entries(part['footprint_id'], library=part['footprint_id'].split(':', 1)[0])
            good = len(fp_entries) == 1 and footprints.load(*fp_entries[0])['definition_hash'] == part['footprint_definition_hash']
            symbol = next((s for s in backend.catalog([part['symbol_id'].split(':', 1)[0]]) if s['library_id'] == part['symbol_id']), None)
            good = good and symbol is not None and symbol['definition_hash'] == part['symbol_definition_hash']
            sources = []
            for relative in sorted({ref['asset'] for refs in part['evidence'].values() for ref in refs}):
                asset = assets[relative]
                sources.append(dict(url=asset['source_url'], snapshot_sha256=asset['sha256'], package_asset=relative,
                                    evidence_kind='imported_document_snapshot', verification='source_linked_not_human_qualified'))
            facts = dict(part['facts'], pin_mapping=part['pin_mapping'], footprint_definition_hash=part['footprint_definition_hash'],
                         symbol_definition_hash=part['symbol_definition_hash'],
                         import_record=dict(package_id=folder.name, evidence=part['evidence'],
                                            semantic_verification='provided_source_interpretation_not_independently_proven'))
            definition = registry.sha(registry.canonical(dict(package_id=folder.name, part=part)))
            result.append(dict(candidate_id=definition[:32], manufacturer=part['manufacturer'], mpn=part['mpn'], kind=part['kind'],
                               symbol_id=part['symbol_id'], footprint_id=part['footprint_id'], definition_hash=definition,
                               facts=facts, sources=sources, skus=[], evidence_status='available' if good else 'missing_or_changed',
                               qualification='source_linked_candidate_not_human_qualified'))
    return result


class CatalogImportService:
    def catalog_import(self, request):
        with self.locked():
            return install(request, self.root)

    def library_inventory(self):
        from . import backend, footprints
        with self.locked():
            return dict(symbol_libraries=sorted(backend.library_paths()), footprint_libraries=sorted(footprints.library_paths()),
                        packages=[dict(package_id=p.name, name=m['name'], part_count=len(m['parts'])) for m, p in registry.bundles()])
