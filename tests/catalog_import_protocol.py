"""Actual MCP import/bind/SKiDL/ERC acceptance; not a transistor function test."""
import argparse
import asyncio
import base64
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


async def run(output, document, custom=False):
    output.mkdir(parents=True, exist_ok=False)
    parameters = StdioServerParameters(command=sys.executable, args=['-m', 'pcb_mcp.server'],
                                       env={**os.environ, 'PCB_MCP_DATA_DIR': str(output / 'data')})
    transcript = []
    async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        async def call(tool, request=None, error=None):
            response = await session.call_tool(tool, {} if request is None else {'request': request}, read_timeout_seconds=timedelta(seconds=180))
            # Document bytes belong to the package artifact; avoid repeating them in every transcript.
            record = dict(tool=tool, arguments={'package_sha256': hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()} if tool == 'catalog_import' else request,
                          response=response.model_dump(mode='json'))
            transcript.append(record)
            write(output / 'transcript.json', transcript)
            if error:
                assert response.isError and error in str(response), str(response)
                return None
            assert not response.isError, str(response)
            return response.structuredContent['data']
        inventory = await call('library_inventory')
        assert 'Transistor_FET' in inventory['symbol_libraries']
        symbol = (await call('library_search', dict(query='Transistor_FET:2N7002', library='Transistor_FET')))['candidates'][0]
        symbol = await call('library_get', dict(candidate_id=symbol['candidate_id']))
        fp = (await call('footprint_search', dict(query='Package_TO_SOT_SMD:SOT-23', library='Package_TO_SOT_SMD')))['candidates'][0]
        fp = await call('footprint_get', dict(candidate_id=fp['candidate_id']))
        data = document.read_bytes()
        assert data.startswith(b'%PDF'), 'Retained primary-source PDF required'
        source = 'https://www.onsemi.com/download/data-sheet/pdf/nds7002a-d.pdf'
        def evidence(locator, interpretation):
            return [dict(asset='documents/nds7002a-d.pdf', locator=locator, interpretation=interpretation)]
        package = dict(name='EXT_ONSEMI_FET', description='onsemi 2N7002 source identity and bounded source table records; March 2025 Rev11.',
                       assets=[dict(path='documents/nds7002a-d.pdf', kind='document', source_url=source,
                                    sha256=hashlib.sha256(data).hexdigest(), content_base64=base64.b64encode(data).decode())],
                       parts=[dict(manufacturer='onsemi', mpn='2N7002', kind='mosfet', symbol_id=symbol['library_id'], footprint_id=fp['footprint_id'],
                                   symbol_definition_hash=symbol['definition_hash'], footprint_definition_hash=fp['definition_hash'],
                                   pin_mapping={'1': 'G', '2': 'S', '3': 'D'},
                                   facts=dict(channel='N', absolute_maximum=dict(vds_v=60.0, vgs_v=20.0, continuous_drain_current_a=0.115, case_temperature_c=25.0),
                                              rds_on_table=dict(max_ohm=7.5, vgs_v=5.0, drain_current_a=0.05, case_temperature_c=25.0, pulse_width_max_us=300.0, duty_max_fraction=0.02)),
                                   evidence=dict(identity=evidence('Pages 1 and 7, ordering information', '2N7002 is listed as an onsemi SOT-23 three-lead device.'),
                                                 package=evidence('Pages 1 and 7, package identification', 'SOT-23 3L identity; this is not a manufacturer land-pattern or assembly qualification.'),
                                                 pin_mapping=evidence('Page 1, SOT-23 pin numbering', 'Pin 1 gate, pin 2 source, pin 3 drain; checked against the real KiCad symbol and pad numbers.'),
                                                 channel=evidence('Page 1, description', 'N-channel enhancement-mode device.'),
                                                 absolute_maximum=evidence('Page 2, absolute maximum ratings, 2N7002 column', 'Listed absolute limits at the stated 25 degree C case condition; not recommended operating points.'),
                                                 rds_on_table=evidence('Page 3, 2N7002 ON CHARACTERISTICS; page 4 note 1', 'Use only the stated test point and pulsed-test conditions; no unlisted gate-voltage interpolation.')))])
        if custom:
            # Package actual flattened KiCad data; no invented symbol pins or pads.
            from pcb_mcp import backend
            raw = next(c for c in backend.catalog(['Transistor_FET']) if c['library_id'] == symbol['library_id'])
            library = ('(kicad_symbol_lib (version 20250114) (generator "pcb_mcp_import_test") ' + raw['definition'] + ')').encode()
            footprint = Path(fp['source']).read_bytes()
            for name, content, kind in [('symbols/EXT_ONSEMI_FET.kicad_sym', library, 'symbol'), ('footprints/EXT_ONSEMI_FET.pretty/SOT-23.kicad_mod', footprint, 'footprint')]:
                package['assets'].append(dict(path=name, kind=kind, source_url='https://gitlab.com/kicad/libraries/' + ('kicad-symbols' if kind == 'symbol' else 'kicad-footprints'),
                                              sha256=hashlib.sha256(content).hexdigest(), content_base64=base64.b64encode(content).decode()))
            package['parts'][0]['symbol_id'] = 'EXT_ONSEMI_FET:2N7002'
            package['parts'][0]['footprint_id'] = 'EXT_ONSEMI_FET:SOT-23'
        write(output / 'catalog-package.json', package)
        imported = await call('catalog_import', package)
        assert imported['installed']
        assert not (await call('catalog_import', package))['installed']
        bad = json.loads(json.dumps(package))
        bad['assets'][0]['sha256'] = '0' * 64
        await call('catalog_import', bad, error='IMPORT_ASSET_HASH_MISMATCH')
        record = (await call('parts_search', dict(query='2N7002', kind='mosfet')))['candidates'][0]
        record = await call('part_get', dict(candidate_id=record['candidate_id']))
        if custom:
            symbol = (await call('library_search', dict(query=record['symbol_id'])))['candidates'][0]
            symbol = await call('library_get', dict(candidate_id=symbol['candidate_id']))
            fp = (await call('footprint_search', dict(query=record['footprint_id'])))['candidates'][0]
            fp = await call('footprint_get', dict(candidate_id=fp['candidate_id']))
        header = (await call('parts_search', dict(query='61300211121')))['candidates'][0]
        header = await call('part_get', dict(candidate_id=header['candidate_id']))
        hs = (await call('library_search', dict(query=header['symbol_id'])))['candidates'][0]
        hs = await call('library_get', dict(candidate_id=hs['candidate_id']))
        hf = (await call('footprint_search', dict(query=header['footprint_id'])))['candidates'][0]
        hf = await call('footprint_get', dict(candidate_id=hf['candidate_id']))
        created = await call('project_create', dict(name='Imported MOSFET physical pin breakout, identity acceptance only'))
        pid, revision = created['project_id'], created['revision']
        async def mutate(tool, **kwargs):
            nonlocal revision
            result = await call(tool, dict(project_id=pid, expected_revision=revision, idempotency_key=uuid.uuid4().hex, **kwargs))
            revision = result['revision']
            return result
        components = {}
        for ref, sym, footprint, part in [('Q1', symbol, fp, record), ('J1', hs, hf, header), ('J2', hs, hf, header)]:
            component = await mutate('component_add', candidate_id=sym['candidate_id'], ref=ref, value=sym['symbol'])
            components[ref] = component
            await mutate('footprint_bind', component_id=component['component_id'], candidate_id=footprint['candidate_id'], reason='Exact source-linked package and physical pad mapping')
            await mutate('part_bind', component_id=component['component_id'], candidate_id=part['candidate_id'])
        def pin(ref, number):
            c = components[ref]
            return dict(component_id=c['component_id'], pin_id=next(p['pin_id'] for p in c['pins'] if p['number'] == number))
        for name, pins in [('GATE', [('Q1', '1'), ('J1', '1')]), ('DRAIN', [('Q1', '3'), ('J2', '1')]), ('SOURCE', [('Q1', '2'), ('J1', '2'), ('J2', '2')])]:
            net = await mutate('net_create', name=name, kind='signal')
            await mutate('net_members_set', net_id=net['net_id'], pins=[pin(*p) for p in pins])
        check = await call('spec_validate', dict(project_id=pid))
        assert check['verdict'] == 'pass', check
        bom = await call('bom_export', dict(project_id=pid))
        assert next(r for r in bom['rows'] if r['reference'] == 'Q1')['mpn'] == '2N7002'
        sch = await mutate('schematic_build')
        target = dict(project_id=pid, build_id=sch['build_id'])
        verify = await call('schematic_verify', target)
        erc = await call('erc_run', target)
        assert verify['verdict'] == 'pass' and erc['verdict'] == 'pass', (verify, erc)
        await call('schematic_preview', target)
        exported = await call('schematic_export', target)
        write(output / 'verification.json', dict(passed=True, package=imported, project_id=pid, revision=revision,
                schematic_build_id=sch['build_id'], bom=bom, exported=exported, source_url=source,
                physical_verified=False, function_qualified=False, packaged_custom_libraries=custom,
                purpose='new-part import identity, pin mapping, actual netlist and ERC acceptance'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--document', type=Path, required=True)
    parser.add_argument('--custom', action='store_true')
    args = parser.parse_args()
    asyncio.run(run(args.output.resolve(), args.document.resolve(), args.custom))
