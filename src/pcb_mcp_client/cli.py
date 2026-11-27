"""Installed entry points for environment checks, MCP serving and plan execution."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ENV_KEYS = {
    'data_dir': 'PCB_MCP_DATA_DIR', 'kicad_cli': 'PCB_MCP_KICAD_CLI',
    'device_library': 'PCB_MCP_DEVICE_LIBRARY', 'footprint_root': 'PCB_MCP_FOOTPRINT_ROOT',
    'pcb_python': 'PCB_MCP_PCB_PYTHON', 'java': 'PCB_MCP_JAVA', 'router': 'PCB_MCP_ROUTER',
    'asset_root': 'PCB_MCP_ASSET_ROOT', 'reviewer': 'PCB_MCP_REVIEWER',
    'approval_authority': 'PCB_MCP_APPROVAL_AUTHORITY',
    'ltspice_root': 'PCB_MCP_LTSPICE_ROOT',
}


def configure(path=None, data=None):
    if path:
        path = Path(path).expanduser().resolve()
        settings = tomllib.loads(path.read_text())
        if set(settings) - {'runtime'} or not isinstance(settings.get('runtime'), dict):
            raise ValueError('CONFIG_REQUIRES_RUNTIME_TABLE')
        for key, value in settings['runtime'].items():
            if key not in ENV_KEYS or not isinstance(value, str) or not value:
                raise ValueError('INVALID_RUNTIME_SETTING: ' + key)
            target = Path(value).expanduser()
            if not target.is_absolute():
                target = path.parent / target
            os.environ[ENV_KEYS[key]] = str(target.resolve())
    if data:
        os.environ['PCB_MCP_DATA_DIR'] = str(Path(data).expanduser().resolve())


def probe(command):
    try:
        result = subprocess.run([str(x) for x in command], capture_output=True, text=True, timeout=15)
        return result.returncode == 0, (result.stdout + result.stderr).strip()[-2000:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def doctor():
    from pcb_mcp import __version__, config
    from pcb_mcp import backend, footprints
    checks = []
    def add(name, good, detail, remedy):
        checks.append(dict(check=name, status='pass' if good else 'fail', detail=detail,
                           remedy=None if good else remedy))
    add('python', sys.version_info >= (3, 12), sys.version.split()[0], 'Use Python 3.12 or newer.')
    add('platform', sys.platform == 'darwin', sys.platform, 'This release is validated on macOS with KiCad 10.')
    if os.environ.get('PCB_MCP_LTSPICE_ROOT'):
        try:
            from pcb_mcp import ltspice
            root,_,_,_,_=ltspice.paths()
            add('optional_ltspice',True,str(root),'')
        except (OSError,ValueError) as error:
            add('optional_ltspice',False,str(error),'Configure the official Mac LTspice runtime and its isolated user Documents directory; vendor binaries remain external.')
    for package in ('mcp', 'skidl', 'schematizer', 'cryptography', 'jsonschema', 'numpy', 'pydantic'):
        try:
            version = importlib.metadata.version(package)
            add(package, True, version, '')
        except importlib.metadata.PackageNotFoundError:
            add(package, False, 'missing', 'Install the package dependencies.')
    if any(c['status'] == 'fail' for c in checks):
        return dict(version=__version__, ready=False, checks=checks)
    from pcb_mcp import pcb_service
    ok, version = probe([backend.CLI, 'version'])
    add('kicad_cli', ok and version.startswith('10.'), version, 'Set runtime.kicad_cli to KiCad 10 kicad-cli.')
    ok, version = probe([pcb_service.PYTHON, '-c', 'import pcbnew; print(pcbnew.GetBuildVersion())'])
    add('pcbnew', ok and version.startswith('10.'), version, 'Set runtime.pcb_python to KiCad 10 bundled Python.')
    add('symbol_libraries', all((backend.LIB if n == 'Device' else backend.LIB.parent / (n + '.kicad_sym')).is_file()
                               for n in backend.LIBRARIES if n != 'PCB_MCP'), str(backend.LIB.parent),
        'Install KiCad symbol libraries; configure runtime.device_library.')
    add('footprint_libraries', all((footprints.LIB / (n + '.pretty')).is_dir()
                                  for n in footprints.LIBRARIES if not n.endswith('_PCB_MCP')),
        str(footprints.LIB), 'Install KiCad footprints; configure runtime.footprint_root.')
    ok, version = probe([pcb_service.JAVA, '-version'])
    match = re.search(r'(?:openjdk|java) version "(\d+)', version)
    add('java', bool(ok and match and int(match[1]) >= 25), version, 'Install Java 25+ and set runtime.java to its executable.')
    jar = pcb_service.ROUTER
    good = jar.is_file() and hashlib.sha256(jar.read_bytes()).hexdigest() == config.ROUTER_SHA256
    add('freerouting', good, str(jar), 'Run pcb-router to install the pinned Freerouting 2.4.1 JAR (see DEPENDENCIES.md).')
    try:
        from pcb_mcp import parts
        records = parts.catalog()
        unavailable = [p['mpn'] for p in records if p['evidence_status'] != 'available']
        add('catalog_evidence', bool(records) and not unavailable,
            dict(parts=len(records), unavailable=unavailable),
            'Run pcb-evidence to restore the pinned manufacturer evidence; do not silently repin sources.')
    except Exception as exc:
        add('catalog_evidence', False, str(exc),
            'Reinstall the distribution, then run pcb-evidence and pcb-router (see DEPENDENCIES.md).')
    return dict(version=__version__, ready=all(c['status'] == 'pass' for c in checks),
                checks=checks, data_dir=str(config.data_dir()), assets=str(config.asset_root()),
                optional=dict(reviewer_available=config.reviewer_binary().is_file(),
                              note='Independent human approval is required only for fabrication export, not CAD software validation.'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--data-dir', type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    sub.add_parser('serve')
    sub.add_parser('host-config')
    sub.add_parser('build-reviewer')
    importing = sub.add_parser('catalog-import')
    importing.add_argument('package', type=Path)
    sub.add_parser('library-inventory')
    listing=sub.add_parser('tools')
    listing.add_argument('--output',type=Path)
    calling=sub.add_parser('call')
    calling.add_argument('tool')
    calling.add_argument('--request',type=Path)
    calling.add_argument('--output',type=Path)
    calling.add_argument('--timeout',type=int,default=240)
    packing=sub.add_parser('pack-assets')
    packing.add_argument('kind',choices=['catalog','model'])
    packing.add_argument('specification',type=Path)
    packing.add_argument('--output',type=Path,required=True)
    run = sub.add_parser('run', add_help=False)
    run.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        configure(args.config, args.data_dir)
        if args.command=='pack-assets':
            from .packages import materialize
            from .journal import save
            if args.output.exists():raise ValueError('PACKAGE_OUTPUT_ALREADY_EXISTS')
            result=materialize(args.specification,args.kind)
            args.output.parent.mkdir(parents=True,exist_ok=True);save(args.output,result)
            print(json.dumps(dict(output=str(args.output.resolve()),kind=args.kind,assets=len(result['assets']))))
            return 0
        if args.command in ('tools','call'):
            import asyncio
            from .rpc import invoke
            from .journal import save
            if args.output and args.output.exists():raise ValueError('RPC_OUTPUT_ALREADY_EXISTS')
            request=json.loads(args.request.read_text()) if args.command=='call' and args.request else None
            result=asyncio.run(invoke(args.tool if args.command=='call' else None,request,getattr(args,'timeout',240)))
            if args.output:
                args.output.parent.mkdir(parents=True,exist_ok=True);save(args.output,result)
                print(json.dumps(dict(output=str(args.output.resolve()),tool=getattr(args,'tool',None),schema_sha256=result['schema_sha256'])))
            else:print(json.dumps(result,ensure_ascii=False,indent=2))
            return 1 if result.get('response',{}).get('isError') else 0
        if args.command in ('catalog-import', 'library-inventory'):
            from pcb_mcp.service import Service
            from pcb_mcp.config import data_dir
            service = Service(data_dir())
            if args.command == 'catalog-import':
                from pcb_mcp.catalog_import import CatalogPackage
                result = service.catalog_import(CatalogPackage.model_validate_json(args.package.read_text()))
            else:
                result = service.library_inventory()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'doctor':
            result = doctor()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['ready'] else 1
        if args.command == 'serve':
            from pcb_mcp.server import main as serve
            serve()
            return 0
        if args.command == 'run':
            from .run_plan import cli
            return cli(args.arguments)
        if args.command == 'host-config':
            env = {name: os.environ[name] for name in ENV_KEYS.values() if name in os.environ}
            print(json.dumps({'mcpServers': {'pcb-design-lab': {'command': sys.executable,
                  'args': ['-m', 'pcb_mcp_client.cli', 'serve'], 'env': env}}}, indent=2))
            return 0
        if args.command == 'build-reviewer':
            from pcb_mcp.config import asset_root, reviewer_binary
            target = reviewer_binary()
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['xcrun', 'swiftc', '-parse-as-library', str(asset_root() / 'reviewer/Reviewer.swift'),
                            '-o', str(target)], check=True, timeout=120)
            print(target)
            return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status='failed', error=str(exc)), ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
