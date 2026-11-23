"""Immutable, source-linked simulation models for exact part definitions."""
import base64
import json
from pathlib import Path
import tempfile
import shutil
from typing import Annotated,Literal
from pydantic import Field,model_serializer,model_validator
from .models import Strict
from .catalog_import import Asset,Evidence,Hash
from . import registry,parts,backend
from .dc_power_tree import DCPowerTree
from .dc_limits import DCLimits,validate_scope
from .temperature_resistance import TemperatureResistance


class ModelVoltageLimit(Strict):
    positive:str
    negative:str
    minimum_v:Annotated[float,Field(allow_inf_nan=False)]
    maximum_v:Annotated[float,Field(allow_inf_nan=False)]


class FeedbackVoltage(Strict):
    input_pin:str
    output_pin:str
    feedback_pin:str
    ground_pin:str
    reference_nominal_v:Annotated[float,Field(gt=0,le=5,allow_inf_nan=False)]
    output_min_v:Annotated[float,Field(gt=0,le=24,allow_inf_nan=False)]
    output_max_v:Annotated[float,Field(gt=0,le=24,allow_inf_nan=False)]


class ExternalLTspiceModel(Strict):
    model_relative_path:str
    model_sha256:Hash
    symbol_relative_path:str
    symbol_sha256:Hash


class StaticSupplyCurrent(Strict):
    maximum_a:Annotated[float,Field(gt=0,allow_inf_nan=False)]
    input_test_levels_v:Annotated[list[Annotated[float,Field(ge=0,allow_inf_nan=False)]],Field(min_length=2,max_length=32)]
    output_condition:Literal['unloaded']
    lower_bound_method:Literal['nonnegative_static_dissipation']
    evidence:Annotated[list[Evidence],Field(min_length=1)]
    conditions:Annotated[str,Field(min_length=30,max_length=3000)]

    @model_validator(mode='after')
    def levels(self):
        if len(set(self.input_test_levels_v))!=len(self.input_test_levels_v):raise ValueError('STATIC_SUPPLY_TEST_LEVEL_DUPLICATED')
        return self


class SpiceNoiseScope(Strict):
    frequency_hz:Annotated[list[Annotated[float,Field(gt=0,allow_inf_nan=False)]],Field(min_length=2,max_length=2)]
    conditions:Annotated[str,Field(min_length=30,max_length=3000)]
    evidence:Annotated[list[Evidence],Field(min_length=1)]

    @model_validator(mode='after')
    def band(self):
        if self.frequency_hz[0]>=self.frequency_hz[1]:raise ValueError('MODEL_NOISE_BAND_INVALID')
        return self


class VerificationModel(Strict):
    format:Literal['pcb-verification-model-v1']='pcb-verification-model-v1'
    name:Annotated[str,Field(pattern=r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$',description='For SPICE text this must match the actual .SUBCKT or .MODEL entry name in the unchanged source asset, not a display label. Resolve the entry and physical pin order before importing.')]
    part_definition_hash:Hash
    engine:Literal['spice_subcircuit','spice_device','digital_truth','feedback_voltage','dc_power_tree','dc_limits','temperature_resistance']
    pins:Annotated[list[str],Field(min_length=1,max_length=100)]
    excluded_pins:dict[str,str]={}
    assets:Annotated[list[Asset],Field(min_length=1,max_length=16)]
    evidence:Annotated[list[Evidence],Field(min_length=1)]
    model_asset:str|None=None
    dependency_assets:Annotated[list[str],Field(max_length=15)]=[]
    spice_dialect:Literal['native','pspice','ltspice']='native'
    source_spice_dialect:Literal['pspice']|None=None
    external_ltspice:ExternalLTspiceModel|None=None
    # Physical package mapping for a vendor model representing one internal
    # unit (e.g. one comparator of a dual). No executable wrapper is supplied.
    spice_units:Annotated[list[Annotated[list[str],Field(min_length=1,max_length=100)]],Field(min_length=1,max_length=32)]|None=None
    behavioral_temperature:Literal['TEMP']|None=None
    device:Literal['D','M','Q','J']|None=None
    supply_range_v:Annotated[list[float],Field(min_length=2,max_length=2)]|None=None
    temperature_range_c:Annotated[list[float],Field(min_length=2,max_length=2)]
    conditions:Annotated[str,Field(min_length=10,max_length=3000)]
    truth:dict|None=None
    static_supply_current:StaticSupplyCurrent|None=None
    feedback_voltage:FeedbackVoltage|None=None
    dc_power_tree:DCPowerTree|None=None
    dc_limits:DCLimits|None=None
    temperature_resistance:TemperatureResistance|None=None
    voltage_limits:list[ModelVoltageLimit]=[]
    terminal_current_limits_a:dict[str,Annotated[float,Field(gt=0,allow_inf_nan=False)]]={}
    power_limit_w:Annotated[float,Field(gt=0,allow_inf_nan=False)]|None=None
    spice_noise_scope:SpiceNoiseScope|None=None

    @model_serializer(mode='wrap')
    def omit_absent_source_dialect(self,handler):
        result=handler(self)
        if self.source_spice_dialect is None:result.pop('source_spice_dialect',None)
        return result

    @model_validator(mode='after')
    def validate_content(self):
        import math
        if not self.engine.startswith('spice') and self.spice_dialect!='native':raise ValueError('SPICE_DIALECT_ON_NONSPICE_MODEL')
        if self.external_ltspice is not None and self.spice_dialect!='ltspice':raise ValueError('LTSPICE_REFERENCE_ON_OTHER_DIALECT')
        if self.source_spice_dialect is not None and (self.engine!='spice_subcircuit' or self.spice_dialect!='ltspice' or self.external_ltspice is not None):
            raise ValueError('SPICE_SOURCE_DIALECT_ADAPTER_REQUIRES_LTSPICE_TEXT_SUBCIRCUIT')
        for bounds in (self.temperature_range_c,self.supply_range_v):
            if bounds is not None and (not all(math.isfinite(x) for x in bounds) or bounds[0]>bounds[1]):raise ValueError('INVALID_MODEL_CONDITION_RANGE')
        if len(set(self.pins))!=len(self.pins) or set(self.pins)&set(self.excluded_pins):raise ValueError('DUPLICATE_MODEL_PIN')
        if self.behavioral_temperature is not None and (self.engine!='spice_subcircuit' or self.external_ltspice is not None):
            raise ValueError('BEHAVIORAL_TEMPERATURE_REQUIRES_TEXT_SUBCIRCUIT')
        if self.spice_units is not None:
            if self.engine!='spice_subcircuit':raise ValueError('SPICE_UNITS_REQUIRE_SUBCIRCUIT')
            if len({len(unit) for unit in self.spice_units})!=1:raise ValueError('SPICE_UNIT_ARITY_MISMATCH')
            if len({tuple(unit) for unit in self.spice_units})!=len(self.spice_units):raise ValueError('SPICE_UNIT_DUPLICATED')
            if {pin for unit in self.spice_units for pin in unit}!=set(self.pins)|set(self.excluded_pins):
                raise ValueError('SPICE_UNIT_PHYSICAL_PIN_COVERAGE')
        for limit in self.voltage_limits:
            if limit.positive not in self.pins or limit.negative not in self.pins or limit.minimum_v>limit.maximum_v:raise ValueError('INVALID_MODEL_VOLTAGE_LIMIT')
        if set(self.terminal_current_limits_a)-set(self.pins):raise ValueError('MODEL_CURRENT_LIMIT_PIN_UNKNOWN')
        assets={a.path:a for a in self.assets}
        if len(assets)!=len(self.assets):raise ValueError('DUPLICATE_MODEL_ASSET')
        if self.dependency_assets:
            if self.engine!='spice_subcircuit' or self.external_ltspice is not None:raise ValueError('SPICE_DEPENDENCY_REQUIRES_TEXT_SUBCIRCUIT')
            if len(set(self.dependency_assets))!=len(self.dependency_assets) or self.model_asset in self.dependency_assets:raise ValueError('SPICE_DEPENDENCY_DUPLICATE')
            if any(p not in assets or assets[p].kind!='spice_model' for p in self.dependency_assets):raise ValueError('SPICE_DEPENDENCY_ASSET_REQUIRED')
        if any(e.asset not in assets or assets[e.asset].kind!='document' for e in self.evidence):raise ValueError('MODEL_DOCUMENT_EVIDENCE_REQUIRED')
        if self.spice_noise_scope is not None:
            if not self.engine.startswith('spice') or self.spice_dialect=='ltspice':raise ValueError('MODEL_NOISE_SCOPE_REQUIRES_NGSPICE_MODEL')
            if any(e.asset not in assets or assets[e.asset].kind!='document' for e in self.spice_noise_scope.evidence):
                raise ValueError('MODEL_NOISE_DOCUMENT_EVIDENCE_REQUIRED')
        if self.static_supply_current is not None:
            if self.engine!='digital_truth':raise ValueError('STATIC_SUPPLY_REQUIRES_DIGITAL_TRUTH_MODEL')
            if any(e.asset not in assets or assets[e.asset].kind!='document' for e in self.static_supply_current.evidence):raise ValueError('STATIC_SUPPLY_DOCUMENT_EVIDENCE_REQUIRED')
        if self.dc_limits is not None and self.engine!='dc_limits':raise ValueError('DC_LIMITS_ENGINE_MISMATCH')
        if self.temperature_resistance is not None and self.engine!='temperature_resistance':raise ValueError('TEMPERATURE_RESISTANCE_ENGINE_MISMATCH')
        if self.engine.startswith('spice'):
            if self.external_ltspice is not None:
                if self.engine!='spice_subcircuit' or self.spice_dialect!='ltspice' or self.model_asset is not None:
                    raise ValueError('LTSPICE_EXTERNAL_SUBCIRCUIT_REQUIRED')
            elif self.model_asset not in assets or assets[self.model_asset].kind!='spice_model':raise ValueError('SPICE_MODEL_ASSET_REQUIRED')
            if self.engine=='spice_device' and self.device is None:raise ValueError('SPICE_DEVICE_REQUIRED')
        elif self.engine=='feedback_voltage':
            f=self.feedback_voltage
            if f is None:raise ValueError('FEEDBACK_VOLTAGE_DATA_REQUIRED')
            roles=[f.input_pin,f.output_pin,f.feedback_pin,f.ground_pin]
            if len(set(roles))!=4 or not set(roles)<=set(self.pins) or f.output_min_v>f.output_max_v:raise ValueError('INVALID_FEEDBACK_VOLTAGE_PORTS')
        elif self.engine=='dc_power_tree':
            if self.dc_power_tree is None:raise ValueError('DC_POWER_TREE_DATA_REQUIRED')
        elif self.engine=='dc_limits':
            if self.dc_limits is None:raise ValueError('DC_LIMITS_DATA_REQUIRED')
            validate_scope(self.dc_limits,self.pins,self.temperature_range_c)
        elif self.engine=='temperature_resistance':
            if self.temperature_resistance is None:raise ValueError('TEMPERATURE_RESISTANCE_DATA_REQUIRED')
            self.temperature_resistance.validate_scope(self.pins,self.temperature_range_c)
        elif self.truth is None:raise ValueError('DIGITAL_TRUTH_DATA_REQUIRED')
        return self


def packages():
    result={}
    for folder in (registry.root()/'verification_models').glob('*'):
        if len(folder.name)!=64:continue
        manifest=json.loads((folder/'manifest.json').read_text())
        if registry.sha(registry.canonical(manifest))!=folder.name:raise ValueError('VERIFICATION_MODEL_CHANGED')
        for asset in manifest['assets']:
            path=folder/asset['path']
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()) or registry.sha(path.read_bytes())!=asset['sha256']:raise ValueError('VERIFICATION_MODEL_ASSET_CHANGED')
        result[folder.name]=(manifest,folder)
    return result


def bound_model(available, ref, model_id, part_definition_hash):
    if model_id not in available:
        raise ValueError('FUNCTIONAL_MODEL_MISSING: '+ref)
    manifest, folder = available[model_id]
    if manifest['part_definition_hash'] != part_definition_hash:
        raise ValueError('FUNCTIONAL_MODEL_PART_MISMATCH: '+ref)
    return manifest, folder


def install(request):
    catalog=[p for p in parts.catalog() if p['definition_hash']==request.part_definition_hash and p['evidence_status']=='available']
    if len(catalog)!=1:raise ValueError('MODEL_PART_IDENTITY_UNAVAILABLE')
    part=catalog[0]
    if request.engine=='temperature_resistance' and part['kind'] not in ('thermistor','temperature_resistor'):
        raise ValueError('TEMPERATURE_RESISTANCE_PART_KIND_MISMATCH')
    if request.engine=='dc_limits':
        if request.dc_limits.kind=='led' and part['kind']!='led':raise ValueError('DC_LED_PART_KIND_MISMATCH')
        if request.dc_limits.kind=='pass_switch' and part['kind'] not in ('load_switch','analog_switch'):raise ValueError('DC_SWITCH_PART_KIND_MISMATCH')
        if request.dc_limits.kind=='led' and part['symbol_id']=='Device:LED' and (request.dc_limits.anode_pin,request.dc_limits.cathode_pin)!=('2','1'):
            raise ValueError('DC_LED_PHYSICAL_POLARITY_MISMATCH')
    symbol=next(c for c in backend.catalog([part['symbol_id'].split(':')[0]]) if c['library_id']==part['symbol_id'])
    actual={p['number'] for p in symbol['pins']}
    if set(request.pins)|set(request.excluded_pins)!=actual:raise ValueError('MODEL_PHYSICAL_PIN_COVERAGE')
    if any(len(reason)<10 for reason in request.excluded_pins.values()):raise ValueError('MODEL_EXCLUDED_PIN_REASON_REQUIRED')
    manifest=request.model_dump(mode='json');contents={}
    # An absent optional capability must not change already published identities.
    if manifest['static_supply_current'] is None:manifest.pop('static_supply_current')
    if manifest['dc_limits'] is None:manifest.pop('dc_limits')
    if manifest['temperature_resistance'] is None:manifest.pop('temperature_resistance')
    if manifest['spice_units'] is None:manifest.pop('spice_units')
    if manifest['behavioral_temperature'] is None:manifest.pop('behavioral_temperature')
    if manifest['spice_noise_scope'] is None:manifest.pop('spice_noise_scope')
    if not manifest['dependency_assets']:manifest.pop('dependency_assets')
    if manifest.get('dc_limits',{}).get('discharge_pin','absent') is None:manifest['dc_limits'].pop('discharge_pin')
    for asset in manifest['assets']:
        raw=base64.b64decode(asset.pop('content_base64'),validate=True)
        if registry.sha(raw)!=asset['sha256']:raise ValueError('MODEL_ASSET_HASH_MISMATCH')
        contents[asset['path']]=raw
    if sum(map(len,contents.values()))>40_000_000:raise ValueError('MODEL_PACKAGE_SIZE')
    if request.engine.startswith('spice'):
        if request.external_ltspice is not None:
            from . import ltspice
            for unit in request.spice_units or [request.pins]:
                ltspice.resolve_model(request.external_ltspice.model_dump(),request.name,unit)
        else:
            from .spice_bundle import source_cards,isolate
            lines=[]
            for name in [request.model_asset,*request.dependency_assets]:
                cards,_=source_cards(contents[name],manifest)
                lines.extend(cards)
            directive='.subckt' if request.engine=='spice_subcircuit' else '.model'
            matches=[line.split() for line in lines if line.lower().startswith(directive+' ') and line.split()[1].lower()==request.name.lower()]
            if len(matches)!=1:raise ValueError('MODEL_ENTRY_NOT_UNIQUE')
            if request.engine=='spice_subcircuit':
                isolate(lines,request.name,'validation')
                ports=[]
                for token in matches[0][2:]:
                    if token.lower()=='params:':break
                    ports.append(token)
                if any(len(ports)!=len(unit) for unit in request.spice_units or [request.pins]):raise ValueError('MODEL_SUBCIRCUIT_PIN_COUNT')
            if request.behavioral_temperature is not None:
                from .spice_temperature import adapt
                from .temperature_resistance import TemperatureProfile
                adapt(lines,request.name,1,TemperatureProfile(points=[[0.,request.temperature_range_c[0]],[1.,request.temperature_range_c[1]]]),request.temperature_range_c,request.spice_dialect)
    elif request.engine=='digital_truth':
        from .digital_truth import validate_model
        validate_model(request.model_dump())
    key=registry.sha(registry.canonical(manifest));root=registry.root()/'verification_models';root.mkdir(exist_ok=True)
    if key in packages():return dict(model_id=key,already_imported=True,part_definition_hash=part['definition_hash'])
    stage=Path(tempfile.mkdtemp(prefix='.staging-',dir=root))
    try:
        for name,raw in contents.items():
            path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        (stage/'manifest.json').write_bytes(registry.canonical(manifest));stage.rename(root/key)
    finally:
        if stage.exists():shutil.rmtree(stage)
    return dict(model_id=key,already_imported=False,part_definition_hash=part['definition_hash'],qualification='source_linked_model_not_physical_qualification')


class VerificationModelService:
    def verification_model_import(self,request):
        with self.locked():return install(request)
