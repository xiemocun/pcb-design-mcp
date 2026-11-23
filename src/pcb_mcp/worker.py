"""Isolated fixed SKiDL program; reads server-authored JSON, never model Python."""
import json
import sys
from pathlib import Path


def main():
    from skidl import Circuit, KICAD10, Part, Net, set_default_tool
    state = json.loads(Path(sys.argv[1]).read_text())
    set_default_tool(KICAD10)
    circuit = Circuit()
    parts = {}
    with circuit:
        for component in state['components'].values():
            # Use the actual library selected and hash-checked by the server.
            library = component['source']
            parts[component['id']] = Part(library, component['symbol'],
                                           ref=component['ref'], value=component['value'],footprint=state.get('footprint_bindings',{}).get(component['id'],{}).get('footprint_id',''))
        for net in state['nets'].values():
            sknet = Net(net['name'])
            for pin in net['pins']:
                component = state['components'][pin['component_id']]
                number = next(p['number'] for p in component['pins'] if p['pin_id'] == pin['pin_id'])
                # SKiDL string indexing interprets brackets as a regex. Resolve
                # grouped KiCad numbers literally, preserving the actual pin.
                matches=[p for p in parts[pin['component_id']].pins if p.num==number]
                if len(matches)!=1:raise ValueError('Exact library pin mapping is ambiguous: '+number)
                sknet += matches[0]
        for intent in state.get('pin_intents',{}).values():
            if intent['intent'] != 'no_connect':
                continue
            ref=intent['pin']; component=state['components'][ref['component_id']]
            number=next(p['number'] for p in component['pins'] if p['pin_id']==ref['pin_id'])
            # Use this circuit's own NC net; the global NC belongs to another circuit.
            matches=[p for p in parts[component['id']].pins if p.num==number]
            if len(matches)!=1:raise ValueError('Exact NC pin mapping is ambiguous: '+number)
            circuit.NC += matches[0]
    circuit.generate_netlist(file_='skidl.net')
    # backend.build validates this real SKiDL netlist, then emits the final
    # managed-symbol schematic through the existing deterministic adapter.
    # The former generated.* schematic was discarded by that adapter and its
    # automatic layout could exceed the worker deadline on larger circuits.


if __name__ == '__main__':
    main()
