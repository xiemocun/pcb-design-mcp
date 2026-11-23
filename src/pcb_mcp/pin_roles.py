"""Supply return names from source-bound facts; stdlib-only for native workers."""
def ground_names(state,component):
    facts=state.get('part_bindings',{}).get(component['id'],{}).get('facts',{})
    return facts.get('ground_pins',['GND'])
