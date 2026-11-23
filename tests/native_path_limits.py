"""Read-only rejection regression on an actual exported native contact graph."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from pcb_mcp.path_constraints import evaluate


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.output.exists():raise ValueError('OUTPUT_ALREADY_EXISTS')
    state=json.loads((args.build/'spec.json').read_text());actual=json.loads((args.build/'copper_geometry.json').read_text())
    originals={name:hashlib.sha256((args.build/name).read_bytes()).hexdigest() for name in ('spec.json','routed.kicad_pcb','copper_geometry.json')}
    original=evaluate(state,actual,'routed')
    assert original['measurements'] and not original['findings'],original
    rejections=[]
    for index,measurement in enumerate(original['measurements']):
        length=measurement['length_upper_bound_mm']
        if length<=0:continue
        changed=copy.deepcopy(state);changed['board_constraints']['path_constraints'][index]['max_length_mm']=length/2
        rejected=evaluate(changed,actual,'routed')
        assert any(row['index']==index and row['status']=='fail' for row in rejected['measurements'])
        assert any(row['code']=='DECLARED_COPPER_PATH_FAILED' for row in rejected['findings'])
        rejections.append(dict(index=index,counterfactual_limit_mm=length/2,result=rejected))
    assert rejections
    assert all(hashlib.sha256((args.build/name).read_bytes()).hexdigest()==sha for name,sha in originals.items())
    result=dict(passed=True,original=original,counterfactual_rejections=rejections,source_hashes=originals,
                scope='read-only declared path-limit regression on historical native contact evidence; no CAD/state mutation, independent task or physical claim')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
