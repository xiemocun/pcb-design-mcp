"""Source-linked nominal resistance and explicit body-temperature stimuli.

Prescribing body temperature does not model thermal lag or self-heating.
"""
from typing import Annotated
from pydantic import Field,model_validator
from .models import Strict
import math

Finite=Annotated[float,Field(allow_inf_nan=False)]
Pair=Annotated[list[Finite],Field(min_length=2,max_length=2)]


class TemperatureProfile(Strict):
    points:Annotated[list[Pair],Field(min_length=2,max_length=100)]

    @model_validator(mode='after')
    def ordered(self):
        if self.points[0][0]!=0 or any(a[0]>=b[0] for a,b in zip(self.points,self.points[1:])):
            raise ValueError('BODY_TEMPERATURE_TIME_ORDER_INVALID')
        return self


class TemperatureResistance(Strict):
    pin_a:str
    pin_b:str
    nominal_points:Annotated[list[Pair],Field(min_length=2,max_length=100)]
    interpolation:Annotated[str,Field(pattern=r'^piecewise_linear$')]='piecewise_linear'

    @model_validator(mode='after')
    def ordered(self):
        if self.pin_a==self.pin_b:raise ValueError('TEMPERATURE_RESISTANCE_PIN_ROLES_INVALID')
        if any(p[1]<=0 for p in self.nominal_points):raise ValueError('TEMPERATURE_RESISTANCE_NOT_POSITIVE')
        if any(a[0]>=b[0] for a,b in zip(self.nominal_points,self.nominal_points[1:])):
            raise ValueError('TEMPERATURE_RESISTANCE_TEMPERATURE_ORDER_INVALID')
        return self

    def at(self,temperature):
        if not math.isfinite(temperature) or not self.nominal_points[0][0]<=temperature<=self.nominal_points[-1][0]:
            raise ValueError('TEMPERATURE_RESISTANCE_OUTSIDE_SCOPE')
        for (t,a),(u,b) in zip(self.nominal_points,self.nominal_points[1:]):
            if t<=temperature<=u:return a+(b-a)*(temperature-t)/(u-t)
        raise ValueError('TEMPERATURE_RESISTANCE_OUTSIDE_SCOPE')

    def validate_scope(self,pins,span):
        if set(pins)!={self.pin_a,self.pin_b}:raise ValueError('TEMPERATURE_RESISTANCE_PIN_ROLES_INVALID')
        for t in span:self.at(t)


def expression(points,variable,dialect='native'):
    """Numeric-only linear segments with endpoint holds inside validated scope."""
    value=f'{points[-1][1]:.15g}'
    def choose(condition,a,b):
        return f'if({condition},{a},{b})' if dialect=='ltspice' else f'({condition} ? {a} : {b})'
    for (t,a),(u,b) in reversed(list(zip(points,points[1:]))):
        segment=f'({a:.15g}+({variable}-{t:.15g})*{(b-a)/(u-t):.15g})'
        value=choose(f'{variable}<={u:.15g}',segment,value)
    return choose(f'{variable}<={points[0][0]:.15g}',f'{points[0][1]:.15g}',value)


def emit(model,index,pins,temperature,profile,dialect='native'):
    data=TemperatureResistance.model_validate(model['temperature_resistance'])
    data.validate_scope(model['pins'],model['temperature_range_c'])
    mapping=dict(zip(model['pins'],pins));p=mapping[data.pin_a];n=mapping[data.pin_b]
    if profile is None:
        if not model['temperature_range_c'][0]<=temperature<=model['temperature_range_c'][1]:
            raise ValueError('BODY_TEMPERATURE_MODEL_OUTSIDE_SCOPE')
        return [f'Rthermal{index} {p} {n} {data.at(temperature):.15g}']
    for _,t in profile.points:
        if not model['temperature_range_c'][0]<=t<=model['temperature_range_c'][1]:
            raise ValueError('BODY_TEMPERATURE_MODEL_OUTSIDE_SCOPE')
        data.at(t)
    return [f'.func mcp_body_{index}(t) {{{expression(profile.points,"t",dialect)}}}',
        f'.func mcp_resistance_{index}(tc) {{{expression(data.nominal_points,"tc",dialect)}}}',
        f'Bthermal{index} {p} {n} I=V({p},{n})/mcp_resistance_{index}(mcp_body_{index}(time))']
