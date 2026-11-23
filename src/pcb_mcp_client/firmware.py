"""Read-only binding of AM2302 firmware to an existing verified MCP board.

Prints a manifest; does not modify projects, flash hardware or approve fabrication.
"""
import argparse
import json
from pathlib import Path

from pcb_mcp import module_contracts, parts
from pcb_mcp.models import PCBRef
from pcb_mcp.service import Service
from .transient import sha, verified_circuit


def checked_files(folder, check, verdict, label):
    if check.get('verdict') != verdict or not check.get('files'):
        raise ValueError('FIRMWARE_EVIDENCE_REQUIRED: ' + label)
    for name, digest in check['files'].items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError('INVALID_EVIDENCE_PATH')
        if not path.is_file() or sha(path) != digest:
            raise ValueError('FIRMWARE_EVIDENCE_CHANGED: ' + name)


def interface_pins(report):
    if report.get('verdict') != 'pass' or any(f.get('severity') == 'error' for f in report.get('findings', [])):
        raise ValueError('FIRMWARE_INTERFACE_INVALID')
    rows = report.get('modules', [])
    if len(rows) != 1 or rows[0].get('kind') != 'split_open_drain' or rows[0].get('drive_release_level') != 1:
        raise ValueError('FIRMWARE_INTERFACE_UNSUPPORTED')
    row = rows[0]
    return dict(read_gpio=int(row['read_gpio'].removeprefix('IO')),
                drive_gpio=int(row['drive_gpio'].removeprefix('IO')),
                drive_release_level=1, interface=row)


def manifest(data_root, project_id, pcb_build_id):
    service = Service(data_root)
    with service.locked():
        state, pcb, folder = service.pcb_target(PCBRef(project_id=project_id, pcb_build_id=pcb_build_id))
        for name in ('verify_routed', 'drc', 'layout_routed'):
            checked_files(folder, pcb['checks'].get(name, {}), 'pass', name)
        copper = pcb['checks'].get('copper', {})
        if copper.get('verdict') not in ('pass', 'incomplete'):
            raise ValueError('FIRMWARE_COPPER_EVIDENCE_REQUIRED')
        checked_files(folder, copper, copper['verdict'], 'copper')
        report = json.loads((folder / 'copper_report.json').read_text())
        if report.get('geometry_verdict') != 'pass' or any(f.get('severity') == 'error' for f in report.get('findings', [])):
            raise ValueError('FIRMWARE_COPPER_GEOMETRY_INVALID')
        sch = service.project_path(project_id) / 'builds' / pcb['schematic_build_id']
        _, pins = verified_circuit(state, sch / 'actual.xml')
        binding = interface_pins(module_contracts.evaluate(state))
        evidence = {str(p.relative_to(service.root)): sha(p) for p in (
            sch / 'led.kicad_sch', sch / 'actual.xml', folder / 'routed.kicad_pcb')}
        return dict(project_id=state['id'], revision=state['revision'],
                    schematic_build_id=pcb['schematic_build_id'], pcb_build_id=pcb['id'],
                    **binding, source_evidence_hash=parts.rules_hash(),
                    connected_physical_pins=len(pins), artifact_hashes=evidence,
                    copper_verdict=report['verdict'], physical_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--pcb-build-id', required=True)
    args = parser.parse_args()
    print(json.dumps(manifest(args.data_root, args.project_id, args.pcb_build_id), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
