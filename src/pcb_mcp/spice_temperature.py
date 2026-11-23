"""Explicit body-temperature stimulus for a vendor's behavioral TEMP variable.

Only behavioral expressions are adapted. Semiconductor/process temperature
remains the declared solver temperature. The immutable source asset is retained;
the generated deck records the instance-local adaptation.
"""
import re
from .temperature_resistance import expression


def adapt(lines,entry,index,profile,span,dialect):
    logical=[]
    for line in lines:
        if line.startswith('+'):
            if not logical:raise ValueError('BODY_TEMPERATURE_CONTINUATION_WITHOUT_CARD')
            logical[-1]+=' '+line[1:].strip()
        else:logical.append(line)
    declarations={}
    for line in logical:
        cells=line.split()
        if cells[0].lower()=='.subckt':
            key=cells[1].lower()
            if key in declarations:raise ValueError('BODY_TEMPERATURE_SUBCIRCUIT_NOT_UNIQUE')
            declarations[key]=f'mcp_bt_{index}_{len(declarations)}'
    if entry.lower() not in declarations:raise ValueError('BODY_TEMPERATURE_ENTRY_MISSING')
    node=f'mcp_body_{index}';port='mcp_body_temperature';changed=0;result=[]
    for line in logical:
        cells=line.split();token=cells[0].lower()
        if re.search(r'\b'+port+r'\b',line,re.I):raise ValueError('BODY_TEMPERATURE_RESERVED_NAME')
        if token=='.subckt':
            cells[1]=declarations[cells[1].lower()]
            pos=next((i for i,c in enumerate(cells) if c.lower()=='params:'),len(cells))
            cells.insert(pos,port);line=' '.join(cells)
        elif token=='.ends':
            if len(cells)>1:cells[1]=declarations[cells[1].lower()]
            line=' '.join(cells)
        elif token.startswith('x'):
            pos=next((i for i,c in enumerate(cells) if c.lower()=='params:' or '=' in c),len(cells))-1
            if cells[pos].lower() not in declarations:raise ValueError('BODY_TEMPERATURE_EXTERNAL_SUBCIRCUIT_UNSUPPORTED')
            cells[pos]=declarations[cells[pos].lower()];cells.insert(pos,port);line=' '.join(cells)
        if re.search(r'\bTEMP\b',line,re.I):
            # Do not reinterpret diode/MOS process temperature, TC parameters,
            # primitive values, node names, or a caller-supplied control card.
            if token[0] not in 'beg' or '{' not in line or '}' not in line:
                raise ValueError('BODY_TEMPERATURE_NONBEHAVIORAL_TEMP_UNSUPPORTED')
            prefix,body=line.split('{',1);body,suffix=body.rsplit('}',1)
            if re.search(r'\bTEMP\b',prefix+' '+suffix,re.I):raise ValueError('BODY_TEMPERATURE_TEMP_OUTSIDE_EXPRESSION')
            body,count=re.subn(r'\bTEMP\b',f'V({port})',body,flags=re.I);changed+=count
            line=prefix+'{'+body+'}'+suffix
        result.append(line)
    if not changed:raise ValueError('BODY_TEMPERATURE_VARIABLE_UNUSED')
    if any(not span[0]<=t<=span[1] for _,t in profile.points):raise ValueError('BODY_TEMPERATURE_MODEL_OUTSIDE_SCOPE')
    source=f'Bbody{index} {node} 0 V={{{expression(profile.points,"time",dialect)}}}'
    return result,declarations[entry.lower()],node,source,changed
