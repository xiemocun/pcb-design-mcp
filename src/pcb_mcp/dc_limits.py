"""Source-bound piecewise DC ratings, independent of nominal SPICE behavior.

Curves are explicit limits, never inferred from typical plots. Evaluation stays
inside their declared range and includes every interior breakpoint.
"""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .models import Strict
import math

Finite=Annotated[float,Field(allow_inf_nan=False)]
Positive=Annotated[float,Field(gt=0,allow_inf_nan=False)]


class TemperatureLimit(Strict):
    points:Annotated[list[Annotated[list[Finite],Field(min_length=2,max_length=2)]],Field(min_length=2,max_length=100)]

    @model_validator(mode='after')
    def ordered(self):
        if any(p[1]<0 for p in self.points):raise ValueError('DC_LIMIT_NEGATIVE_RATING')
        if any(a[0]>=b[0] for a,b in zip(self.points,self.points[1:])):
            raise ValueError('DC_LIMIT_TEMPERATURE_NOT_INCREASING')
        return self

    def minimum(self,temperature_range):
        lo,hi=temperature_range
        if not all(math.isfinite(t) for t in (lo,hi)) or lo>hi or lo<self.points[0][0] or hi>self.points[-1][0]:
            raise ValueError('DC_LIMIT_TEMPERATURE_OUTSIDE_SCOPE')
        def at(t):
            for (x,a),(y,b) in zip(self.points,self.points[1:]):
                if x<=t<=y:return a+(b-a)*(t-x)/(y-x)
            raise ValueError('DC_LIMIT_TEMPERATURE_OUTSIDE_SCOPE')
        return min(at(lo),at(hi),*(v for t,v in self.points if lo<t<hi))


class LEDLimits(Strict):
    kind:Literal['led']='led'
    anode_pin:str
    cathode_pin:str
    maximum_forward_current_a:TemperatureLimit
    maximum_power_w:TemperatureLimit
    # Forward DC qualification does not require inventing a full-temperature
    # reverse rating from a manufacturer's 25 C-only absolute maximum table.
    maximum_reverse_voltage_v:TemperatureLimit|None=None


class PassSwitchLimits(Strict):
    kind:Literal['pass_switch']='pass_switch'
    input_pin:str
    output_pin:str
    ground_pin:str
    enable_pin:str
    discharge_pin:str|None=None
    supply_range_v:Annotated[list[Positive],Field(min_length=2,max_length=2)]
    maximum_continuous_current_a:TemperatureLimit
    maximum_on_resistance_ohm:Positive
    # Ideal-diode paths may have qualified ON resistance without a qualified
    # forward OFF-current limit. Absence must never imply zero leakage.
    maximum_off_leakage_a:Annotated[float,Field(ge=0,allow_inf_nan=False)]|None=None
    conditions:Annotated[str,Field(min_length=30,max_length=2000)]


DCLimits=Annotated[LEDLimits|PassSwitchLimits,Field(discriminator='kind')]


def validate_scope(limits,pins,temperature_range):
    roles=[getattr(limits,k) for k in (('anode_pin','cathode_pin') if limits.kind=='led'
                                     else ('input_pin','output_pin','ground_pin','enable_pin'))]
    if limits.kind=='pass_switch' and limits.discharge_pin is not None:roles.append(limits.discharge_pin)
    if len(set(roles))!=len(roles) or not set(roles)<=set(pins):
        raise ValueError('DC_LIMIT_PIN_ROLES_INVALID')
    curves=[limits.maximum_forward_current_a,limits.maximum_power_w,limits.maximum_reverse_voltage_v] if limits.kind=='led' else [limits.maximum_continuous_current_a]
    for curve in curves:
        if curve is not None:curve.minimum(temperature_range)
    if limits.kind=='pass_switch' and limits.supply_range_v[0]>limits.supply_range_v[1]:
        raise ValueError('DC_SWITCH_SUPPLY_RANGE_REVERSED')


def bound_models(state):
    """Only unique exact-part models; never apply another identity's ratings."""
    from . import parts,verification_models
    available=verification_models.packages();current={p['candidate_id']:p for p in parts.catalog()}
    result={}
    for component in state['components'].values():
        part=state.get('part_bindings',{}).get(component['id'])
        if not part or current.get(part['candidate_id'])!=part or part['evidence_status']!='available' or not parts.binding_valid(state,component,part):continue
        matches=[(key,model) for key,(model,_) in available.items()
                 if model['engine']=='dc_limits' and model['part_definition_hash']==part['definition_hash']]
        if len(matches)>1:raise ValueError('DC_LIMIT_MODEL_AMBIGUOUS: '+component['ref'])
        if matches:
            key,model=matches[0]
            from pydantic import TypeAdapter
            limits=TypeAdapter(DCLimits).validate_python(model['dc_limits'])
            validate_scope(limits,model['pins'],model['temperature_range_c'])
            result[component['id']]=(key,model,limits)
    return result
