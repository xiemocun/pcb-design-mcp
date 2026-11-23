"""Typed circuit plan. It selects returned part identities, never Python code."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

class Strict(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
Ref=Annotated[str,Field(pattern=r'^[A-Z][A-Z0-9]{0,7}$')]
NetName=Annotated[str,Field(pattern=r'^[A-Z][A-Z0-9_]{0,39}$')]
class Endpoint(Strict):
 ref:Ref
 number:Annotated[str,Field(min_length=1,max_length=40)]
class Component(Strict):
 ref:Ref
 mpn:Annotated[str,Field(min_length=1,max_length=80)]
 part_definition_hash:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
 value:Annotated[str,Field(min_length=1,max_length=40)]|None=None
class Net(Strict):
 name:NetName
 kind:Literal['power','ground','signal']
 pins:Annotated[list[Endpoint],Field(min_length=2,max_length=200)]
class NC(Strict):
 pin:Endpoint
 reason:Annotated[str,Field(min_length=5,max_length=500)]
SETTING_TOOLS=('requirements_set','power_source_set','power_envelope_set','passive_ratings_set','power_budget_set','board_constraints_set','pcb_net_rules_set','constraint_set')
class Setting(Strict):
 tool:Literal['requirements_set','power_source_set','power_envelope_set','passive_ratings_set','power_budget_set','board_constraints_set','pcb_net_rules_set','constraint_set']
 args:dict
 @model_validator(mode='after')
 def reserved(self):
  if set(self.args)&{'project_id','expected_revision','idempotency_key'}:raise ValueError('RESERVED_PROJECT_FIELDS')
  return self
class Plan(Strict):
 version:Literal[1]=1
 routing_attempt_limit:Annotated[int,Field(ge=1,le=4)]=2
 routing_max_passes:Annotated[int,Field(ge=1,le=100)]=20
 name:Annotated[str,Field(min_length=1,max_length=100)]
 requirement_sha256:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
 components:Annotated[list[Component],Field(min_length=1,max_length=100)]
 nets:Annotated[list[Net],Field(min_length=1,max_length=100)]
 no_connects:Annotated[list[NC],Field(max_length=200)]=[]
 settings:Annotated[list[Setting],Field(max_length=100)]
 @model_validator(mode='after')
 def graph(self):
  refs=[x.ref for x in self.components];names=[x.name for x in self.nets]
  if len(set(refs))!=len(refs):raise ValueError('DUPLICATE_COMPONENT_REFERENCE')
  if len(set(names))!=len(names):raise ValueError('DUPLICATE_NET_NAME')
  seen=set()
  for p in [p for n in self.nets for p in n.pins]+[x.pin for x in self.no_connects]:
   key=(p.ref,p.number)
   if p.ref not in refs:raise ValueError('UNKNOWN_COMPONENT_REFERENCE')
   if key in seen:raise ValueError('PIN_ASSIGNED_TWICE')
   seen.add(key)
  if sum(x.tool=='board_constraints_set' for x in self.settings)!=1:raise ValueError('ONE_BOARD_CONSTRAINT_REQUIRED')
  return self

def resolve(value,components,nets):
 """Only explicit reference objects are substituted; no eval or string expansion."""
 if isinstance(value,list):return [resolve(x,components,nets) for x in value]
 if not isinstance(value,dict):return value
 special=set(value)&{'$pin','$component','$net'}
 if special:
  if len(value)!=1:raise ValueError('MIXED_REFERENCE_OBJECT')
  key=next(iter(special));alias=value[key]
  if key=='$net':return nets[alias]
  if key=='$component':return components[alias]['component_id']
  if not isinstance(alias,list) or len(alias)!=2:raise ValueError('INVALID_PIN_REFERENCE')
  c=components[alias[0]];pins=[p for p in c['pins'] if p['number']==alias[1]]
  if len(pins)!=1:raise ValueError('PIN_NOT_FOUND_OR_AMBIGUOUS: '+str(alias))
  return dict(component_id=c['component_id'],pin_id=pins[0]['pin_id'])
 if any(k.startswith('$') for k in value):raise ValueError('UNKNOWN_REFERENCE_OPERATOR')
 return {k:resolve(v,components,nets) for k,v in value.items()}

def check_pin_coverage(plan,components,attachments=()):
 expected={(ref,p['number']) for ref,c in components.items() for p in c['pins']}
 actual={(p.ref,p.number) for n in [*plan.nets,*attachments] for p in n.pins}|{(x.pin.ref,x.pin.number) for x in plan.no_connects}
 if actual-expected:raise ValueError('UNKNOWN_REAL_PIN: '+str(sorted(actual-expected)))
 if expected-actual:raise ValueError('UNACCOUNTED_REAL_PIN: '+str(sorted(expected-actual)))


def select_part(candidates, mpn, definition_hash):
 """MPNs shared by manufacturers are resolved by the already-selected source hash."""
 matches=[p for p in candidates if p['mpn']==mpn]
 pinned=[p for p in matches if p['definition_hash']==definition_hash]
 if len(pinned)==1:return pinned[0]
 if matches and not pinned:raise ValueError('PART_EVIDENCE_CHANGED: '+mpn)
 raise ValueError('EXACT_PART_NOT_UNIQUE: '+mpn)
