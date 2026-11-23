"""Frozen functional contracts executed against current real project nets."""
import copy
import json
import math
from pathlib import Path
from typing import Annotated,Literal
import uuid
import numpy as np
from pydantic import Field,model_validator,model_serializer
from .models import Strict,Read,ReplacementGroup,ReplacementTarget
from .catalog_import import Hash
from . import parts,registry,spice,linear_passive,verification_models,transient_recovery
from .editing import spec_hash
from .common import file_snapshot, require
from .temperature_resistance import TemperatureProfile

Number=Annotated[float,Field(allow_inf_nan=False)]
Key=Annotated[str,Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')]


class Terminal(Strict):
    ref:str
    number:str


class I2CPullups(Strict):
    id: Key
    sda: Terminal
    scl: Terminal
    supply: Terminal
    sda_ohm: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    scl_ohm: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class Sinusoid(Strict):
    offset:Number
    amplitude:Annotated[float,Field(gt=0,allow_inf_nan=False)]
    frequency_hz:Annotated[float,Field(gt=0,allow_inf_nan=False)]
    delay_s:Annotated[float,Field(ge=0,allow_inf_nan=False)]=0.
    phase_deg:Number=0.


class NonSinkingSupply(Strict):
    """Explicit source-only regulator fixture; zero setpoint disconnects it."""
    current_limit_a:Annotated[float,Field(gt=0,le=100,allow_inf_nan=False)]
    output_resistance_ohm:Annotated[float,Field(ge=1e-4,le=1000,allow_inf_nan=False)]


class OpenDrain(Strict):
    on_resistance_ohm:Annotated[float,Field(gt=0,allow_inf_nan=False)]
    off_resistance_ohm:Annotated[float,Field(gt=0,le=1e15,allow_inf_nan=False)]
    control_pwl:Annotated[list[Annotated[list[Number],Field(min_length=2,max_length=2)]],Field(min_length=2,max_length=2000)]

    @model_validator(mode='after')
    def control(self):
        points=self.control_pwl
        if points[0][0]!=0 or any(t1>=t2 for (t1,_),(t2,_) in zip(points,points[1:])) or any(v not in (0.,1.) for _,v in points):
            raise ValueError('OPEN_DRAIN_REQUIRES_ORDERED_BINARY_CONTROL_PWL')
        if self.off_resistance_ohm<=self.on_resistance_ohm:raise ValueError('OPEN_DRAIN_RESISTANCE_ORDER_INVALID')
        return self


class SeriesConnection(Strict):
    """A switched positive lead with explicit finite off-state resistance."""
    off_resistance_ohm:Annotated[float,Field(gt=0,le=1e15,allow_inf_nan=False)]
    control_pwl:Annotated[list[Annotated[list[Number],Field(min_length=2,max_length=2)]],Field(min_length=2,max_length=2000)]

    @model_validator(mode='after')
    def control(self):
        points=self.control_pwl
        if points[0][0]!=0 or any(a[0]>=b[0] for a,b in zip(points,points[1:])) or any(value not in (0.,1.) for _,value in points):
            raise ValueError('SERIES_CONNECTION_REQUIRES_ORDERED_BINARY_CONTROL_PWL')
        return self


class Drive(Strict):
    positive:Terminal
    negative:Terminal
    kind:Literal['voltage','current','open_drain']='voltage'
    dc_v:Number|None=None
    dc_a:Number|None=None
    pulse:Annotated[list[Number],Field(min_length=7,max_length=7)]|None=None
    pwl:Annotated[list[Annotated[list[Number],Field(min_length=2,max_length=2)]],Field(min_length=2,max_length=2000)]|None=None
    sine:Sinusoid|None=None
    ac_v:Annotated[float,Field(gt=0,allow_inf_nan=False)]|None=None
    ac_a:Annotated[float,Field(gt=0,allow_inf_nan=False)]|None=None
    non_sinking_supply:NonSinkingSupply|None=None
    open_drain:OpenDrain|None=None
    series_resistance_ohm:Annotated[float,Field(ge=1e-6,le=1e9,allow_inf_nan=False)]|None=None
    series_connection:SeriesConnection|None=None

    @model_serializer(mode='wrap')
    def omit_absent_fixture_models(self,handler):
        result=handler(self)
        if self.non_sinking_supply is None:result.pop('non_sinking_supply',None)
        if self.open_drain is None:result.pop('open_drain',None)
        if self.series_resistance_ohm is None:result.pop('series_resistance_ohm',None)
        if self.series_connection is None:result.pop('series_connection',None)
        return result

    @model_validator(mode='after')
    def timing(self):
        if (self.kind=='open_drain')!=(self.open_drain is not None):raise ValueError('OPEN_DRAIN_DRIVE_CONTROL_REQUIRED')
        if self.open_drain and any(x is not None for x in (self.dc_v,self.dc_a,self.pulse,self.pwl,self.sine,self.ac_v,self.ac_a,self.non_sinking_supply)):
            raise ValueError('OPEN_DRAIN_USES_ONLY_SWITCH_CONTROL')
        if self.kind=='voltage' and (self.dc_v is None or self.dc_a is not None or self.ac_a is not None):raise ValueError('VOLTAGE_DRIVE_REQUIRES_VOLTS_ONLY')
        if self.kind=='current' and (self.dc_a is None or self.dc_v is not None or self.ac_v is not None):raise ValueError('CURRENT_DRIVE_REQUIRES_AMPERES_ONLY')
        if sum(x is not None for x in [self.pulse,self.pwl,self.sine])>1:raise ValueError('ONE_TRANSIENT_WAVEFORM_REQUIRED')
        if self.pwl and (self.pwl[0][0]!=0 or any(a[0]>=b[0] for a,b in zip(self.pwl,self.pwl[1:]))):raise ValueError('PWL_TIME_AXIS_INVALID')
        if self.pulse:
            low,high,delay,rise,fall,width,period=self.pulse
            if delay<0 or min(rise,fall,width,period)<=0 or rise+fall+width>period:raise ValueError('INVALID_PULSE_TIMING')
        if self.non_sinking_supply is not None:
            if self.kind!='voltage' or self.ac_v is not None or self.sine is not None:raise ValueError('NON_SINKING_SUPPLY_REQUIRES_DC_OR_NONNEGATIVE_TRANSIENT_VOLTAGE')
            if min([self.dc_v,*((self.pulse or [])[:2]),*(v for _,v in self.pwl or [])])<0:raise ValueError('NON_SINKING_SUPPLY_NEGATIVE_SETPOINT')
        if self.series_resistance_ohm is not None and (self.kind!='voltage' or self.non_sinking_supply is not None):
            raise ValueError('SERIES_RESISTANCE_REQUIRES_UNREGULATED_VOLTAGE_FIXTURE')
        if self.series_connection is not None:
            if self.series_resistance_ohm is None:raise ValueError('SERIES_CONNECTION_REQUIRES_ON_RESISTANCE')
            if self.series_connection.off_resistance_ohm<=self.series_resistance_ohm:
                raise ValueError('SERIES_CONNECTION_RESISTANCE_ORDER_INVALID')
        return self


class EdgeTrigger(Strict):
    positive:Terminal
    negative:Terminal
    threshold:Number
    direction:Literal['rising','falling']
    occurrence:Annotated[int,Field(ge=1,le=10000)]=1
    search_window_s:Annotated[list[Number],Field(min_length=2,max_length=2)]

    @model_validator(mode='after')
    def window(self):
        if not 0<=self.search_window_s[0]<self.search_window_s[1]:raise ValueError('EDGE_SEARCH_WINDOW_INVALID')
        return self


class DelayMeasurement(Strict):
    trigger:EdgeTrigger
    target_threshold:Number
    target_direction:Literal['rising','falling']
    observation_end_s:Annotated[float,Field(gt=0,allow_inf_nan=False)]

    @model_validator(mode='after')
    def window(self):
        if self.observation_end_s<=self.trigger.search_window_s[1]:raise ValueError('EVENT_OBSERVATION_WINDOW_INVALID')
        return self


class SettlingMeasurement(Strict):
    trigger:EdgeTrigger
    band:Annotated[list[Number],Field(min_length=2,max_length=2)]
    minimum_hold_s:Annotated[float,Field(gt=0,allow_inf_nan=False)]
    observation_end_s:Annotated[float,Field(gt=0,allow_inf_nan=False)]

    @model_validator(mode='after')
    def window(self):
        if self.band[0]>=self.band[1]:raise ValueError('SETTLING_BAND_INVALID')
        if self.observation_end_s-self.trigger.search_window_s[1]<self.minimum_hold_s:raise ValueError('SETTLING_OBSERVATION_TOO_SHORT')
        return self


class FrequencyCrossing(Strict):
    reference_frequency_hz:Annotated[float,Field(gt=0,allow_inf_nan=False)]
    level_db:Number
    direction:Literal['rising','falling']
    occurrence:Annotated[int,Field(ge=1,le=10000)]=1
    search_window_hz:Annotated[list[Number],Field(min_length=2,max_length=2)]

    @model_validator(mode='after')
    def window(self):
        if not 0<self.search_window_hz[0]<self.search_window_hz[1]:raise ValueError('AC_CROSSING_SEARCH_WINDOW_INVALID')
        return self


class RatioTerm(Strict):
    positive:Terminal
    negative:Terminal
    quantity:Literal['voltage','current','power']
    measurement_scope:Literal['component','external_port']='component'
    polarity:Literal[1,-1]=1

    @model_validator(mode='after')
    def scope(self):
        if self.quantity in ('current','power') and self.positive.ref!=self.negative.ref:raise ValueError('DEVICE_MEASUREMENT_REQUIRES_SAME_COMPONENT_TERMINALS')
        if self.measurement_scope=='external_port' and self.quantity=='voltage':raise ValueError('EXTERNAL_PORT_SCOPE_REQUIRES_CURRENT_OR_POWER')
        return self


class MeasurementRatio(Strict):
    numerator:Annotated[list[RatioTerm],Field(min_length=1,max_length=32)]
    denominator:Annotated[list[RatioTerm],Field(min_length=1,max_length=32)]
    denominator_minimum_abs:Annotated[float,Field(gt=0,allow_inf_nan=False)]=1e-15

    @model_validator(mode='after')
    def dimensions(self):
        if len({x.quantity for x in self.numerator+self.denominator})!=1:raise ValueError('RATIO_REQUIRES_MATCHING_DIMENSIONS')
        for group in [self.numerator,self.denominator]:
            keys=[json.dumps(x.model_dump(exclude={'polarity'}),sort_keys=True) for x in group]
            if len(keys)!=len(set(keys)):raise ValueError('RATIO_DUPLICATE_OBSERVATION')
        return self


class TransientLowpass(Strict):
    """Ideal non-loading voltage instrument, with a specified -3 dB bandwidth."""
    response:Literal['single_pole','butterworth_2']
    cutoff_hz:Annotated[float,Field(gt=0,allow_inf_nan=False)]


class I2CByte(Strict):
    value:Annotated[int,Field(ge=0,le=255)]
    ack:bool


class I2CFrame(Strict):
    start:Literal['start','repeated_start']
    address:Annotated[int,Field(ge=8,le=119)]
    direction:Literal['read','write']
    address_ack:bool
    data:Annotated[list[I2CByte],Field(max_length=256)]


PositiveTime=Annotated[float,Field(gt=0,allow_inf_nan=False)]


class I2CTiming(Strict):
    minimum_clock_high_s:PositiveTime|None=None
    minimum_clock_low_s:PositiveTime|None=None
    maximum_clock_low_s:PositiveTime|None=None
    minimum_longest_clock_low_s:PositiveTime|None=None
    minimum_data_setup_s:PositiveTime|None=None
    minimum_data_hold_s:PositiveTime|None=None
    minimum_start_hold_s:PositiveTime|None=None
    minimum_repeated_start_setup_s:PositiveTime|None=None
    minimum_stop_setup_s:PositiveTime|None=None
    minimum_bus_free_s:PositiveTime|None=None
    maximum_rise_s:PositiveTime|None=None
    maximum_fall_s:PositiveTime|None=None


class I2CMeasurement(Strict):
    clock_positive:Terminal
    clock_negative:Terminal
    low_v:Number
    high_v:Number
    maximum_sample_interval_s:PositiveTime
    expected:Annotated[list[I2CFrame],Field(min_length=1,max_length=64)]
    timing:I2CTiming

    @model_validator(mode='after')
    def thresholds(self):
        if self.low_v>=self.high_v:raise ValueError('I2C_THRESHOLDS_INVALID')
        if self.expected[0].start!='start':raise ValueError('I2C_EXPECTED_FIRST_START_REQUIRED')
        return self


class I2SWord(Strict):
    channel:Literal['left','right']
    value:int


class I2STiming(Strict):
    minimum_clock_period_s:PositiveTime
    maximum_clock_period_s:PositiveTime
    minimum_clock_low_s:PositiveTime
    minimum_clock_high_s:PositiveTime
    minimum_data_setup_s:PositiveTime
    minimum_data_hold_s:PositiveTime
    minimum_ws_setup_s:PositiveTime
    minimum_ws_hold_s:PositiveTime
    maximum_rise_s:PositiveTime|None=None
    maximum_fall_s:PositiveTime|None=None

    @model_validator(mode='after')
    def period(self):
        if self.minimum_clock_period_s>self.maximum_clock_period_s:
            raise ValueError('I2S_CLOCK_PERIOD_LIMITS_REVERSED')
        return self


class I2SMeasurement(Strict):
    clock_positive:Terminal
    clock_negative:Terminal
    ws_positive:Terminal
    ws_negative:Terminal
    low_v:Number
    high_v:Number
    maximum_sample_interval_s:PositiveTime
    word_bits:Annotated[int,Field(ge=2,le=32)]
    slot_bits:Annotated[int,Field(ge=2,le=64)]
    padding:Literal['zero','ignore']='zero'
    expected:Annotated[list[I2SWord],Field(min_length=2,max_length=128)]
    timing:I2STiming

    @model_validator(mode='after')
    def words(self):
        if self.low_v>=self.high_v or self.slot_bits<self.word_bits:
            raise ValueError('I2S_THRESHOLDS_OR_SLOT_WIDTH_INVALID')
        bound=1 << (self.word_bits-1)
        if any(not -bound<=word.value<bound for word in self.expected):
            raise ValueError('I2S_EXPECTED_WORD_OUTSIDE_SIGNED_WIDTH')
        if any(a.channel==b.channel for a,b in zip(self.expected,self.expected[1:])):
            raise ValueError('I2S_EXPECTED_CHANNELS_MUST_ALTERNATE')
        return self


class THDNMeasurement(Strict):
    fundamental_hz:Annotated[float,Field(gt=0,allow_inf_nan=False)]
    bandwidth_hz:Annotated[list[Number],Field(min_length=2,max_length=2)]
    minimum_fundamental_rms_v:Annotated[float,Field(gt=0,allow_inf_nan=False)]
    stationary_noise_requirement:Key|None=None

    @model_validator(mode='after')
    def bandwidth(self):
        low,high=self.bandwidth_hz
        if not 0<low<=self.fundamental_hz<=high or low==high:raise ValueError('THDN_BAND_MUST_INCLUDE_FUNDAMENTAL')
        return self


class FunctionalRequirement(Strict):
    id:Key
    description:Annotated[str,Field(min_length=10,max_length=2000)]
    positive:Terminal|None=None
    negative:Terminal|None=None
    quantity:Literal['voltage','gain','gain_db','ac_crossing_frequency','logic','current','power','frequency','duty_cycle','delay','settling_time','ratio','noise_voltage_rms','i2c_frames','i2s_words','thd_plus_n']
    minimum:Number
    maximum:Number
    scenarios:Annotated[list[Key],Field(min_length=1)]
    aggregation:Literal['range','final','peak','minimum','peak_to_peak','average','rms']='range'
    time_window_s:Annotated[list[Number],Field(min_length=2,max_length=2)]|None=None
    denominator:Terminal|None=None
    threshold:Number|None=None
    ac_measurement:Literal['magnitude','real','imaginary']|None=None
    measurement_scope:Literal['component','external_port']='component'
    delay:DelayMeasurement|None=None
    settling:SettlingMeasurement|None=None
    ratio:MeasurementRatio|None=None
    reference_frequency_hz:Annotated[float,Field(gt=0,allow_inf_nan=False)]|None=None
    ac_crossing:FrequencyCrossing|None=None
    lowpass:TransientLowpass|None=None
    i2c:I2CMeasurement|None=None
    i2s:I2SMeasurement|None=None
    thdn:THDNMeasurement|None=None

    @model_serializer(mode='wrap')
    def omit_absent_lowpass(self,handler):
        result=handler(self)
        if self.lowpass is None:result.pop('lowpass',None)
        if self.i2c is None:result.pop('i2c',None)
        if self.i2s is None:result.pop('i2s',None)
        if self.thdn is None:result.pop('thdn',None)
        return result

    @model_validator(mode='after')
    def limits(self):
        if self.minimum>self.maximum:raise ValueError('REQUIREMENT_RANGE_REVERSED')
        if (self.quantity=='ac_crossing_frequency')!=(self.ac_crossing is not None):raise ValueError('AC_CROSSING_KIND_MISMATCH')
        if self.reference_frequency_hz is not None and self.quantity!='gain_db':raise ValueError('AC_REFERENCE_REQUIRES_GAIN_DB')
        if self.ac_crossing and (self.minimum<=0 or self.aggregation!='range' or self.time_window_s is not None or self.threshold is not None):raise ValueError('AC_CROSSING_OPTIONS_INVALID')
        if (self.quantity=='ratio')!=(self.ratio is not None):raise ValueError('RATIO_MEASUREMENT_KIND_MISMATCH')
        if self.ratio:
            if self.positive is not None or self.negative is not None or self.denominator is not None or self.threshold is not None or self.measurement_scope!='component':raise ValueError('RATIO_USES_EXPLICIT_TERM_OBSERVATIONS')
            if self.aggregation not in ('final','average','rms'):raise ValueError('EXPLICIT_RATIO_AGGREGATION_REQUIRED')
        elif self.positive is None or self.negative is None:raise ValueError('SCALAR_MEASUREMENT_TERMINALS_REQUIRED')
        if ((self.quantity=='delay')!=(self.delay is not None) or (self.quantity=='settling_time')!=(self.settling is not None)):raise ValueError('EVENT_MEASUREMENT_KIND_MISMATCH')
        if self.delay or self.settling:
            if self.minimum<0 or self.aggregation!='range' or self.time_window_s is not None or self.threshold is not None or self.denominator is not None:raise ValueError('EVENT_MEASUREMENT_OPTIONS_INVALID')
        if self.time_window_s and not 0<=self.time_window_s[0]<=self.time_window_s[1]:raise ValueError('TIME_WINDOW_INVALID')
        if (self.quantity=='i2c_frames')!=(self.i2c is not None):raise ValueError('I2C_MEASUREMENT_KIND_MISMATCH')
        if self.i2c and (not self.time_window_s or self.time_window_s[0]>=self.time_window_s[1] or self.aggregation!='range' or
                         self.threshold is not None or self.denominator is not None or self.minimum!=len(self.i2c.expected) or self.maximum!=self.minimum):
            raise ValueError('I2C_REQUIRES_WINDOW_AND_EXACT_EXPECTED_FRAME_COUNT')
        if (self.quantity=='i2s_words')!=(self.i2s is not None):raise ValueError('I2S_MEASUREMENT_KIND_MISMATCH')
        if self.i2s and (not self.time_window_s or self.time_window_s[0]>=self.time_window_s[1] or self.aggregation!='range' or
                         self.threshold is not None or self.denominator is not None or self.minimum!=len(self.i2s.expected) or self.maximum!=self.minimum):
            raise ValueError('I2S_REQUIRES_WINDOW_AND_EXACT_EXPECTED_WORD_COUNT')
        if self.lowpass and (self.quantity!='voltage' or self.aggregation not in ('peak_to_peak','rms') or
                             not self.time_window_s or self.time_window_s[0]>=self.time_window_s[1] or
                             self.threshold is not None or self.denominator is not None):
            raise ValueError('LOWPASS_REQUIRES_VOLTAGE_AGGREGATION_AND_NONZERO_WINDOW')
        if self.quantity=='noise_voltage_rms' and (self.minimum<0 or self.aggregation!='range' or self.time_window_s is not None or
                                                  self.threshold is not None or self.denominator is not None):
            raise ValueError('NOISE_VOLTAGE_REQUIRES_SPECTRAL_RMS_LIMITS')
        if (self.quantity=='thd_plus_n')!=(self.thdn is not None):raise ValueError('THDN_MEASUREMENT_KIND_MISMATCH')
        if self.thdn:
            if (self.minimum<0 or self.aggregation!='range' or not self.time_window_s or
                self.time_window_s[0]>=self.time_window_s[1] or self.threshold is not None or
                self.denominator is not None or self.positive==self.negative):
                raise ValueError('THDN_REQUIRES_DIFFERENTIAL_VOLTAGE_WINDOW_AND_LINEAR_RATIO_LIMITS')
            from .audio_spectrum import spectral_grid
            spectral_grid(self.time_window_s,self.thdn.fundamental_hz,self.thdn.bandwidth_hz)
        if self.quantity in ('gain','gain_db','ac_crossing_frequency') and self.denominator is None:raise ValueError('GAIN_INPUT_REQUIRED')
        if self.quantity in ('current','power') and self.positive.ref!=self.negative.ref:
            raise ValueError('DEVICE_MEASUREMENT_REQUIRES_SAME_COMPONENT_TERMINALS')
        if self.measurement_scope=='external_port' and self.quantity not in ('current','power'):
            raise ValueError('EXTERNAL_PORT_SCOPE_REQUIRES_CURRENT_OR_POWER')
        if self.quantity in ('frequency','duty_cycle'):
            if self.threshold is None:raise ValueError('PERIODIC_THRESHOLD_REQUIRED')
            if self.aggregation!='range':raise ValueError('PERIODIC_REQUIRES_CYCLE_RANGE')
            if self.quantity=='frequency' and self.minimum<=0:raise ValueError('FREQUENCY_MUST_BE_POSITIVE')
            if self.quantity=='duty_cycle' and not 0<=self.minimum<=self.maximum<=1:raise ValueError('DUTY_CYCLE_OUTSIDE_UNIT_INTERVAL')
        return self


class LinearSource(Strict):
    positive:Terminal
    negative:Terminal
    minimum_v:Number
    nominal_v:Number
    maximum_v:Number
    @model_validator(mode='after')
    def interval(self):
        if not 0<self.minimum_v<=self.nominal_v<=self.maximum_v:raise ValueError('LINEAR_SOURCE_RANGE_INVALID')
        return self


class CurrentSinkLoad(Strict):
    """Unidirectional equivalent load with a finite turn-on conductance."""
    current_a:Annotated[float,Field(gt=0,allow_inf_nan=False)]
    compliance_v:Annotated[float,Field(gt=0,allow_inf_nan=False)]


class BoundaryLoad(Strict):
    positive:Terminal
    negative:Terminal
    resistance_ohm:Annotated[float,Field(gt=0,le=1e15,allow_inf_nan=False)]|None=None
    capacitance_f:Annotated[float,Field(gt=0,le=1000,allow_inf_nan=False)]|None=None
    inductance_h:Annotated[float,Field(gt=0,le=1000,allow_inf_nan=False)]|None=None
    initial_v:Number|None=None
    current_sink:CurrentSinkLoad|None=None

    @model_serializer(mode='wrap')
    def omit_absent_current_sink(self,handler):
        result=handler(self)
        if self.current_sink is None:result.pop('current_sink',None)
        return result

    @model_validator(mode='after')
    def kind(self):
        if sum(value is not None for value in (self.resistance_ohm,self.capacitance_f,self.current_sink))!=1:
            raise ValueError('ONE_BOUNDARY_LOAD_KIND_REQUIRED')
        if self.initial_v is not None and self.capacitance_f is None:raise ValueError('INITIAL_VOLTAGE_REQUIRES_CAPACITOR')
        if self.inductance_h is not None and (self.resistance_ohm is None or self.capacitance_f is not None):raise ValueError('INDUCTIVE_BOUNDARY_REQUIRES_SERIES_RESISTANCE')
        return self


class TransientRecovery(Strict):
    first_timeout_s:Annotated[float,Field(ge=1,le=299,allow_inf_nan=False)]
    integration_method:Literal['trap','gear']
    step_s:Annotated[float,Field(gt=0,allow_inf_nan=False)]


class OperatingPointHint(Strict):
    terminal:Terminal
    guess_v:Annotated[float,Field(ge=-1000,le=1000,allow_inf_nan=False)]


class CapacitorInitialVoltage(Strict):
    """Voltage of the selected physical capacitor pin relative to its other pin at t=0.

    One pin must connect to the transient analysis ground. Parallel capacitors
    share the same initial node potential.
    """
    terminal: Terminal
    voltage_v: Number


class Scenario(Strict):
    id:Key
    engine:Literal['linear_dc','spice_op','spice_transient','spice_ac','spice_noise','digital_truth']
    drives:Annotated[list[Drive],Field(max_length=30)]=[]
    boundary_loads:Annotated[list[BoundaryLoad],Field(max_length=30)]=[]
    linear_sources:Annotated[list[LinearSource],Field(max_length=30)]=[]
    temperature_c:Number=25.
    body_temperature_profiles:dict[Key,TemperatureProfile]={}
    stop_s:Annotated[float,Field(gt=0,le=60,allow_inf_nan=False)]|None=None
    step_s:Annotated[float,Field(gt=0,allow_inf_nan=False)]|None=None
    frequency_hz:Annotated[list[Number],Field(min_length=2,max_length=2)]|None=None
    points:Annotated[int,Field(ge=2,le=10000)]=100
    solver_timeout_s:Annotated[float,Field(ge=1,le=300,allow_inf_nan=False)]|None=None
    integration_method:Literal['trap','gear']|None=None
    passive_current_observation:Literal['native']|None=None
    transient_recovery:TransientRecovery|None=None
    operating_point_hints:Annotated[list[OperatingPointHint],Field(max_length=16)]=[]
    capacitor_initial_voltages:Annotated[list[CapacitorInitialVoltage],Field(max_length=64)]=[]

    @model_serializer(mode='wrap')
    def preserve_absent_thermal_capability(self,handler):
        result=handler(self)
        if not self.body_temperature_profiles:result.pop('body_temperature_profiles',None)
        if self.solver_timeout_s is None:result.pop('solver_timeout_s',None)
        if self.integration_method is None:result.pop('integration_method',None)
        if self.passive_current_observation is None:result.pop('passive_current_observation',None)
        if self.transient_recovery is None:result.pop('transient_recovery',None)
        if not self.operating_point_hints:result.pop('operating_point_hints',None)
        if not self.capacitor_initial_voltages:result.pop('capacitor_initial_voltages',None)
        return result

    @model_validator(mode='after')
    def analysis(self):
        if self.solver_timeout_s is not None and not self.engine.startswith('spice_'):raise ValueError('SOLVER_TIMEOUT_REQUIRES_SPICE')
        if self.integration_method is not None and self.engine!='spice_transient':raise ValueError('INTEGRATION_METHOD_REQUIRES_TRANSIENT')
        if self.operating_point_hints and self.engine!='spice_transient':raise ValueError('OPERATING_POINT_HINT_REQUIRES_TRANSIENT')
        if self.capacitor_initial_voltages:
            if self.engine!='spice_transient':raise ValueError('BOARD_CAPACITOR_INITIAL_STATE_REQUIRES_TRANSIENT')
            refs=[item.terminal.ref for item in self.capacitor_initial_voltages]
            if len(set(refs))!=len(refs):raise ValueError('DUPLICATE_CAPACITOR_INITIAL_STATE')
        if self.passive_current_observation is not None and self.engine!='spice_transient':raise ValueError('NATIVE_PASSIVE_CURRENT_REQUIRES_TRANSIENT')
        if self.engine not in ('spice_op','spice_transient') and any(load.current_sink for load in self.boundary_loads):
            raise ValueError('CURRENT_SINK_LOAD_REQUIRES_SPICE_DC_OR_TRANSIENT')
        if any(d.non_sinking_supply is not None for d in self.drives) and self.engine not in ('spice_op','spice_transient'):raise ValueError('NON_SINKING_SUPPLY_REQUIRES_SPICE_DC_OR_TRANSIENT')
        if self.body_temperature_profiles:
            if self.engine!='spice_transient':raise ValueError('BODY_TEMPERATURE_PROFILE_REQUIRES_TRANSIENT')
            if self.stop_s is None or any(p.points[-1][0]<self.stop_s for p in self.body_temperature_profiles.values()):
                raise ValueError('BODY_TEMPERATURE_PROFILE_COVERAGE_INCOMPLETE')
        if self.engine=='spice_transient' and (self.stop_s is None or self.step_s is None or not self.step_s<=self.stop_s or self.stop_s/self.step_s>200000):raise ValueError('TRANSIENT_GRID_INVALID')
        if self.transient_recovery is not None:
            retry=self.transient_recovery
            if self.engine!='spice_transient':raise ValueError('RECOVERY_REQUIRES_TRANSIENT')
            if retry.first_timeout_s>(self.solver_timeout_s or 60.)-1:raise ValueError('RECOVERY_REQUIRES_RESERVED_SOLVER_BUDGET')
            if retry.step_s>self.step_s or self.stop_s/retry.step_s>200000:raise ValueError('RECOVERY_TRANSIENT_GRID_INVALID')
            if retry.step_s==self.step_s and retry.integration_method==self.integration_method:raise ValueError('RECOVERY_REQUIRES_DIFFERENT_NUMERICAL_SETTINGS')
        if self.engine in ('spice_ac','spice_noise') and (self.frequency_hz is None or not 0<self.frequency_hz[0]<self.frequency_hz[1]):raise ValueError('AC_RANGE_INVALID')
        if self.engine=='spice_noise' and (self.points<5 or sum(d.ac_v is not None or d.ac_a is not None for d in self.drives)!=1):
            raise ValueError('NOISE_REQUIRES_ONE_AC_REFERENCE_AND_AT_LEAST_FIVE_FREQUENCIES')
        if self.engine=='linear_dc' and self.drives:raise ValueError('LINEAR_DC_USES_ACTUAL_DECLARED_SOURCES')
        if self.engine!='spice_transient' and any(d.pulse for d in self.drives):raise ValueError('PULSE_REQUIRES_TRANSIENT')
        if self.engine!='spice_transient' and any(d.pwl is not None or d.sine is not None for d in self.drives):raise ValueError('WAVEFORM_REQUIRES_TRANSIENT')
        if self.engine=='spice_transient' and any(d.pwl is not None and d.pwl[-1][0]<self.stop_s for d in self.drives):raise ValueError('PWL_COVERAGE_INCOMPLETE')
        if any(d.open_drain and (self.engine!='spice_transient' or d.open_drain.control_pwl[-1][0]<self.stop_s) for d in self.drives):
            raise ValueError('OPEN_DRAIN_REQUIRES_COMPLETE_TRANSIENT_CONTROL')
        if any(d.series_resistance_ohm is not None for d in self.drives) and not self.engine.startswith('spice_'):
            raise ValueError('SERIES_RESISTANCE_REQUIRES_SPICE')
        if any(d.series_connection and (self.engine!='spice_transient' or d.series_connection.control_pwl[-1][0]<self.stop_s) for d in self.drives):
            raise ValueError('SERIES_CONNECTION_REQUIRES_COMPLETE_TRANSIENT_CONTROL')
        if self.engine not in ('spice_op','spice_transient','spice_ac','spice_noise') and any(d.kind=='current' for d in self.drives):raise ValueError('CURRENT_DRIVE_REQUIRES_SPICE')
        if self.boundary_loads and self.engine not in ('spice_op','spice_transient','spice_ac','spice_noise'):raise ValueError('BOUNDARY_LOAD_REQUIRES_SPICE')
        if self.engine!='spice_transient' and any(x.initial_v is not None for x in self.boundary_loads):raise ValueError('INITIAL_VOLTAGE_REQUIRES_TRANSIENT')
        return self


def matching_dc_bias(scenario,op):
    def drives(s):
        return [d.model_dump(mode='json',exclude={'ac_v','ac_a'}) for d in s.drives]
    return op.engine=='spice_op' and scenario.temperature_c==op.temperature_c and scenario.boundary_loads==op.boundary_loads and drives(scenario)==drives(op)


def matching_thdn_bias(transient,noise,settings,window):
    if (noise.frequency_hz!=settings.bandwidth_hz or transient.body_temperature_profiles or
        noise.temperature_c!=transient.temperature_c or noise.boundary_loads!=transient.boundary_loads):
        return False
    tones=[d for d in transient.drives if d.sine is not None]
    if len(tones)!=1 or any(d.pwl or d.pulse or d.open_drain or d.series_connection for d in transient.drives):
        return False
    tone=tones[0]
    if (tone.sine.frequency_hz!=settings.fundamental_hz or tone.sine.delay_s>window[0] or
        tone.sine.offset!=(tone.dc_v if tone.kind=='voltage' else tone.dc_a)):
        return False
    def bias(scenario):
        return [drive.model_dump(mode='json',exclude={'ac_v','ac_a','sine'}) for drive in scenario.drives]
    return bias(transient)==bias(noise)


class FunctionalContract(Strict):
    description:Annotated[str,Field(min_length=10,max_length=4000)]
    requirements:Annotated[list[FunctionalRequirement],Field(max_length=100)]
    scenarios:Annotated[list[Scenario],Field(max_length=64)]
    i2c_pullups: Annotated[list[I2CPullups], Field(max_length=32)] = []

    @model_serializer(mode='wrap')
    def omit_absent_pullups(self, handler):
        result = handler(self)
        if not self.i2c_pullups:
            result.pop('i2c_pullups', None)
        return result

    @model_validator(mode='after')
    def coverage(self):
        scenarios={s.id:s for s in self.scenarios}
        requirements={r.id:r for r in self.requirements}
        requirement_ids = [r.id for r in [*self.requirements, *self.i2c_pullups]]
        if len(scenarios)!=len(self.scenarios) or len(set(requirement_ids))!=len(requirement_ids):raise ValueError('DUPLICATE_FUNCTIONAL_ID')
        if bool(self.requirements) != bool(self.scenarios) or not requirement_ids:
            raise ValueError('FUNCTIONAL_SCENARIO_AND_REQUIREMENT_COVERAGE_MISSING')
        for r in self.requirements:
            if set(r.scenarios)-set(scenarios):raise ValueError('REQUIREMENT_SCENARIO_MISSING')
            for key in r.scenarios:
                engine=scenarios[key].engine
                if (engine=='digital_truth' and r.quantity not in ('logic','voltage')) or (r.quantity=='logic' and engine!='digital_truth'):raise ValueError('REQUIREMENT_ENGINE_MISMATCH')
                if (engine=='spice_noise')!=(r.quantity=='noise_voltage_rms'):raise ValueError('NOISE_REQUIRES_NOISE_VOLTAGE_AND_SPICE_NOISE_SCENARIO')
                if r.quantity in ('gain','gain_db','ac_crossing_frequency') and engine!='spice_ac':raise ValueError('GAIN_REQUIRES_AC')
                if r.reference_frequency_hz is not None and not scenarios[key].frequency_hz[0]<=r.reference_frequency_hz<=scenarios[key].frequency_hz[1]:raise ValueError('AC_REFERENCE_FREQUENCY_UNCOVERED')
                if r.ac_crossing:
                    lo,hi=scenarios[key].frequency_hz
                    if not lo<=r.ac_crossing.reference_frequency_hz<=hi or not lo<=r.ac_crossing.search_window_hz[0]<r.ac_crossing.search_window_hz[1]<=hi:raise ValueError('AC_CROSSING_FREQUENCY_UNCOVERED')
                if r.ac_measurement is not None and (engine!='spice_ac' or r.quantity not in ('voltage','current')):raise ValueError('AC_COMPONENT_REQUIRES_AC_VOLTAGE_OR_CURRENT')
                if engine=='spice_ac' and r.quantity=='voltage' and r.ac_measurement is None:raise ValueError('AC_VOLTAGE_COMPONENT_REQUIRED')
                if engine=='linear_dc' and r.quantity!='voltage':raise ValueError('LINEAR_DC_MEASUREMENT_ADAPTER_REQUIRED')
                if r.quantity=='power' and engine=='spice_ac':raise ValueError('AC_POWER_MEASUREMENT_UNSUPPORTED')
                if r.quantity in ('frequency','duty_cycle') and engine!='spice_transient':raise ValueError('PERIODIC_REQUIRES_TRANSIENT')
                if r.delay or r.settling:
                    if engine!='spice_transient':raise ValueError('EVENT_REQUIRES_TRANSIENT')
                    if (r.delay or r.settling).observation_end_s>scenarios[key].stop_s:raise ValueError('EVENT_OBSERVATION_UNCOVERED')
                if r.ratio and engine not in ('spice_op','spice_transient'):raise ValueError('RATIO_REQUIRES_OP_OR_TRANSIENT')
                if r.aggregation in ('average','rms','peak_to_peak') and engine!='spice_transient':raise ValueError('WAVEFORM_AGGREGATION_REQUIRES_TRANSIENT')
                if r.aggregation in ('average','rms') and r.time_window_s and r.time_window_s[0]==r.time_window_s[1]:raise ValueError('NONZERO_TRANSIENT_INTERVAL_REQUIRED')
                if r.time_window_s and (engine!='spice_transient' or r.time_window_s[1]>scenarios[key].stop_s):raise ValueError('REQUIREMENT_TIME_WINDOW_UNCOVERED')
                if r.lowpass and scenarios[key].step_s>0.05/r.lowpass.cutoff_hz:
                    raise ValueError('LOWPASS_TRANSIENT_STEP_TOO_COARSE')
                if r.i2c and scenarios[key].step_s>r.i2c.maximum_sample_interval_s:
                    raise ValueError('I2C_TRANSIENT_STEP_TOO_COARSE')
                if r.i2s and scenarios[key].step_s>r.i2s.maximum_sample_interval_s:
                    raise ValueError('I2S_TRANSIENT_STEP_TOO_COARSE')
                if r.thdn:
                    if engine!='spice_transient' or scenarios[key].step_s>.1/r.thdn.bandwidth_hz[1]:
                        raise ValueError('THDN_TRANSIENT_STEP_TOO_COARSE')
                    noise_id=r.thdn.stationary_noise_requirement
                    if noise_id is not None:
                        noise=requirements.get(noise_id)
                        if (noise is None or noise.quantity!='noise_voltage_rms' or len(noise.scenarios)!=1 or
                            noise.scenarios[0] not in scenarios or (noise.positive,noise.negative)!=(r.positive,r.negative) or
                            not matching_thdn_bias(scenarios[key],scenarios[noise.scenarios[0]],r.thdn,r.time_window_s)):
                            raise ValueError('THDN_STATIONARY_NOISE_REQUIRES_MATCHING_BAND_BIAS_LOAD_AND_TERMINALS')
        if set(scenarios)-{key for r in self.requirements for key in r.scenarios}:raise ValueError('UNMEASURED_SCENARIO')
        for scenario in self.scenarios:
            if scenario.engine!='spice_noise':continue
            outputs={(r.positive.ref,r.positive.number,r.negative.ref,r.negative.number) for r in self.requirements if scenario.id in r.scenarios}
            if len(outputs)!=1:raise ValueError('NOISE_SCENARIO_REQUIRES_ONE_DIFFERENTIAL_OUTPUT')
            if not any(matching_dc_bias(scenario,op) for op in self.scenarios):raise ValueError('NOISE_MATCHING_DC_BIAS_REQUIRED')
        return self


class FunctionalCheck(Read):
    revision:Annotated[int,Field(ge=0)]
    expected_spec_sha256:Hash
    contract:FunctionalContract
    contract_sha256:Hash
    model_bindings:dict[str,Hash]={}
    replacement:ReplacementTarget|None=None
    replacements:Annotated[list[ReplacementTarget],Field(min_length=2,max_length=16)]|None=None

    @model_validator(mode='after')
    def unique_replacement_scope(self):
        if self.replacements is not None:
            if self.replacement is not None:
                raise ValueError('REPLACEMENT_SCOPE_CONFLICT')
            ReplacementGroup(replacements=self.replacements)
        return self

    @model_serializer(mode='wrap')
    def omit_absent_group(self, handler):
        result = handler(self)
        if self.replacements is None:
            result.pop('replacements', None)
        return result


def terminals(s):
    assigned={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    result={}
    for c in s['components'].values():
        for p in c['pins']:
            if (c['id'],p['pin_id']) in assigned:result[c['ref'],p['number']]=assigned[c['id'],p['pin_id']]
    return result


def resolve_terminal(mapping,terminal):
    key=(terminal.ref,terminal.number)
    if key not in mapping:raise ValueError('FUNCTIONAL_TERMINAL_NOT_CONNECTED: '+str(key))
    return mapping[key]


def verify_pullup(resistors, nets, bindings, signal_net, supply, expected, context):
    if signal_net == supply:
        raise ValueError(f'I2C_PULLUP_TERMINALS_SHORTED: {context}')
    candidates = []
    for resistor in resistors:
        ref = resistor['ref']
        pins = {nets.get((ref, pin)): pin for pin in ('1', '2')}
        if pins.keys() == {signal_net, supply}:
            candidates.append((resistor, pins))
    if len(candidates) != 1:
        raise ValueError(f'I2C_PULLUP_COUNT_MISMATCH: {context} found {len(candidates)}')
    from .electrical import number
    resistor, pins = candidates[0]
    nominal = number(resistor['value'])
    if nominal is None or not math.isclose(nominal, expected, rel_tol=1e-9):
        raise ValueError(f'I2C_PULLUP_NOMINAL_MISMATCH: {context} found {nominal}, expected {expected}')
    return dict(reference=resistor['ref'], nominal_ohm=nominal, signal_pin=pins[signal_net],
                supply_pin=pins[supply], definition_hash=bindings[resistor['id']]['definition_hash'])


def resolved_parts(s):
    current={p['candidate_id']:p for p in parts.catalog()};result={}
    for c in s['components'].values():
        p=s.get('part_bindings',{}).get(c['id'])
        if not p or current.get(p['candidate_id'])!=p or p['evidence_status']!='available' or not parts.binding_valid(s,c,p):raise ValueError('FUNCTIONAL_PART_IDENTITY_STALE: '+c['ref'])
        result[c['ref']]=p
    return result


def resolve_models(s,bindings,records):
    available=verification_models.packages();result={}
    for ref,key in bindings.items():
        if ref not in records:raise ValueError('FUNCTIONAL_MODEL_MISSING: '+ref)
        model,folder=verification_models.bound_model(available,ref,key,records[ref]['definition_hash'])
        component=next(c for c in s['components'].values() if c['ref']==ref)
        connected=terminals(s)
        for number in model['excluded_pins']:
            pin=next(p for p in component['pins'] if p['number']==number)
            if (ref,number) in connected or s.get('pin_intents',{}).get(component['id']+':'+pin['pin_id'],{}).get('intent')!='no_connect':raise ValueError('MODEL_EXCLUDED_PIN_NOT_NC')
        result[ref]=(model,folder)
    return result


def drive_lines(drive,index,positive,negative):
    """Positive current flows from positive to negative, as on a SPICE I source."""
    if drive.open_drain is not None:
        switch=drive.open_drain;internal=f'drive_port{index}';control=f'drain_control{index}';model=f'mcp_drain_{index}'
        levels=' '.join(format(x,'.15g') for pair in switch.control_pwl for x in pair)
        return [f'Vdrive{index} {positive} {internal} 0',f'Vdrain{index} {control} {negative} PWL({levels})',
                f'Sdrain{index} {internal} {negative} {control} {negative} {model}',
                f'.model {model} SW(Ron={switch.on_resistance_ohm:.15g} Roff={switch.off_resistance_ohm:.15g} Vt=0.5 Vh=0)']
    dc=drive.dc_v if drive.kind=='voltage' else drive.dc_a
    ac=drive.ac_v if drive.kind=='voltage' else drive.ac_a
    value=f'DC {dc:.15g}'
    if ac is not None:value+=f' AC {ac:.15g}'
    if drive.pulse:value+=' PULSE('+ ' '.join(format(x,'.15g') for x in drive.pulse)+')'
    if drive.pwl:value+=' PWL('+ ' '.join(format(x,'.15g') for pair in drive.pwl for x in pair)+')'
    if drive.sine:
        wave=drive.sine
        value+=' SIN('+ ' '.join(format(x,'.15g') for x in [wave.offset,wave.amplitude,wave.frequency_hz,wave.delay_s,0.,wave.phase_deg])+')'
    if drive.non_sinking_supply is not None:
        # The setpoint source is disconnected from the circuit. A one-quadrant
        # current source presents finite output resistance while sourcing, and
        # neither sinks charge nor clamps the board to a disabled setpoint.
        supply=drive.non_sinking_supply;target=f'drive_target{index}';internal=f'drive_port{index}'
        expression=f'(V({target},{negative})>0)*min({supply.current_limit_a:.15g},max(0,V({target},{internal})/{supply.output_resistance_ohm:.15g}))'
        return [f'Vtarget{index} {target} {negative} {value}',f'Vdrive{index} {positive} {internal} 0',f'Bsource{index} {negative} {internal} I={{{expression}}}']
    if drive.kind=='voltage':
        if drive.series_resistance_ohm is None:return [f'Vdrive{index} {positive} {negative} {value}']
        source=f'drive_source{index}';resistance=drive.series_resistance_ohm
        lines=[f'Vdrive{index} {source} {negative} {value}']
        connection=drive.series_connection
        if connection is None:return lines+[f'Rseries{index} {positive} {source} {resistance:.15g}']
        control=f'series_control{index}';model=f'mcp_series_{index}'
        levels=' '.join(format(x,'.17g') for pair in connection.control_pwl for x in pair)
        return lines+[f'Vseries{index} {control} {negative} PWL({levels})',
                      f'Sseries{index} {positive} {source} {control} {negative} {model}',
                      f'.model {model} SW(Ron={resistance:.15g} Roff={connection.off_resistance_ohm:.15g} Vt=0.5 Vh=0)']
    # The zero-volt series probe preserves the same measured-current vector for
    # both source kinds; it does not clamp the external boundary voltage.
    internal='drive_port'+str(index)
    return [f'Vdrive{index} {positive} {internal} 0',f'Idrive{index} {internal} {negative} {value}']


def passive_connector_role(component,part):
    """Identify ideal physical contacts with no internal electrical topology."""
    if part['kind']!='connector':return None
    symbol=component['library_id']
    if symbol.startswith('Connector_Generic:'):return 'external_port'
    pins=component['pins']
    if not pins or any(p.get('electrical_type')!='passive' for p in pins):return None
    if symbol.startswith('Connector:Screw_Terminal_'):return 'external_port'
    if symbol=='Connector:TestPoint' and len(pins)==1:return 'voltage_probe'
    return None


def capacitor_initial_conditions(state, scenario, records, mapping, names):
    by_ref = {component['ref']: component for component in state['components'].values()}
    voltages, evidence = {}, {}
    for initial in scenario.capacitor_initial_voltages:
        ref, pin = initial.terminal.ref, initial.terminal.number
        component = by_ref.get(ref)
        if component is None or records[ref]['kind'] != 'capacitor':
            raise ValueError('BOARD_CAPACITOR_INITIAL_STATE_REQUIRES_BOUND_CAPACITOR: ' + ref)
        numbers = [p['number'] for p in component['pins']]
        if len(numbers) != 2 or len(set(numbers)) != 2 or pin not in numbers:
            raise ValueError('BOARD_CAPACITOR_INITIAL_PIN_INVALID: ' + ref)
        other = next(number for number in numbers if number != pin)
        positive = names[resolve_terminal(mapping, initial.terminal)]
        negative = names[resolve_terminal(mapping, Terminal(ref=ref, number=other))]
        if positive == negative or '0' not in (positive, negative):
            raise ValueError('BOARD_CAPACITOR_INITIAL_STATE_REQUIRES_GROUNDED_CAPACITOR: ' + ref)
        node, voltage = (positive, initial.voltage_v) if negative == '0' else (negative, -initial.voltage_v)
        if node in voltages and voltages[node] != voltage:
            raise ValueError('SPICE_INITIAL_CAPACITOR_VOLTAGE_CONFLICT')
        voltages[node] = voltage
        evidence[ref] = dict(positive_pin=pin, negative_pin=other, voltage_v=initial.voltage_v,
                             solver_node=node, expected_node_v=voltage,
                             scope='declared terminal voltage on existing grounded capacitor; internal model states follow its operating point')
    return voltages, evidence


def compile_spice(s,scenario,models,records,instrument_ports=False):
    mapping=terminals(s)
    if not scenario.drives:raise ValueError('SPICE_EXPLICIT_BOUNDARY_DRIVES_REQUIRED')
    ground=resolve_terminal(mapping,scenario.drives[0].negative)
    names={nid:'0' if nid==ground else 'n'+str(i+1) for i,nid in enumerate(sorted(s['nets']))}
    lines=['Source-bound project '+s['id']];model_texts={};evidence=[];probes={};vendor_models={};port_rows={};save_currents=[]
    dialect=next(iter({m.get('spice_dialect','native') for m,_ in models.values() if m['engine'].startswith('spice')}),'native')
    for ref in scenario.body_temperature_profiles:
        if ref not in models or (models[ref][0]['engine']!='temperature_resistance' and models[ref][0].get('behavioral_temperature')!='TEMP'):
            raise ValueError('BODY_TEMPERATURE_PROFILE_MODEL_REQUIRED: '+ref)
    def instrument(component,index,pin_numbers):
        result=[];rows=[]
        for j,number in enumerate(pin_numbers):
            net=resolve_terminal(mapping,Terminal(ref=component['ref'],number=number));internal=f'p{index}_{j}';source=f'Vprobe{index}_{j}'
            lines.append(f'{source} {names[net]} {internal} 0');result.append(internal)
            rows.append(dict(number=number,net_id=net,vector='i('+source.lower()+')'))
        probes[component['ref']]=rows;return result
    roles={cid:passive_connector_role(c,records[c['ref']]) for cid,c in s['components'].items()}
    initial_voltages, initial_states = capacitor_initial_conditions(s, scenario, records, mapping, names)
    for index,(cid,c) in enumerate(s['components'].items(),1):
        part=records[c['ref']];facts=part['facts']
        if roles[cid]=='voltage_probe':
            evidence.append(dict(reference=c['ref'],model='ideal_voltage_probe_contact',part_definition_hash=part['definition_hash'],scope='single passive test loop; no loading, parasitics, or fixture drive qualification'))
            continue
        if roles[cid]=='external_port':
            if instrument_ports:
                numbers=[p['number'] for p in c['pins'] if (c['ref'],p['number']) in mapping]
                probes[c['ref']]=[]
                for j,number in enumerate(numbers):
                    row=dict(number=number,net_id=mapping[c['ref'],number],vector=f'derived_port_current_{index}_{j}',scope='external_port',terms=[])
                    probes[c['ref']].append(row);port_rows[c['ref'],number]=row
                evidence.append(dict(reference=c['ref'],model='ideal_external_port',part_definition_hash=part['definition_hash'],scope='sum of original fixture branch observations; no added probe elements, contact resistance or connector dissipation model'))
            continue
        if c['ref'] not in models and part['kind'] in ('resistor','capacitor') and c['library_id'] in ('Device:R','Device:C','Device:C_Polarized'):
            numbers=[p['number'] for p in c['pins']]
            if len(numbers)!=2:raise ValueError('SPICE_PRIMITIVE_PIN_COUNT')
            key='value_ohm' if part['kind']=='resistor' else 'capacitance_f';prefix='R' if part['kind']=='resistor' else 'C';value=facts[key]
            if not math.isfinite(value) or value<=0:raise ValueError('SPICE_PRIMITIVE_VALUE_INVALID')
            element=f'{prefix}{index}'
            reference=dict(reference=c['ref'],model='ideal_'+part['kind'],part_definition_hash=part['definition_hash'],scope='nominal_reference_no_tolerance_leakage_or_parasitics')
            if scenario.passive_current_observation=='native':
                nets=[resolve_terminal(mapping,Terminal(ref=c['ref'],number=number)) for number in numbers]
                pins=[names[net] for net in nets]
                branch=f'i({element.lower()})' if dialect=='ltspice' else f'@{element.lower()}[i]'
                if dialect!='ltspice':save_currents.append(branch)
                probes[c['ref']]=[dict(number=number,net_id=net,vector=f'derived_passive_current_{index}_{j}',
                                       terms=[dict(kind='direct',vector=branch,factor=1 if j==0 else -1)])
                                  for j,(number,net) in enumerate(zip(numbers,nets))]
                reference.update(current_observation='native_branch_current',current_vector=branch,positive_current_enters_pin=numbers[0])
            else:
                pins=instrument(c,index,numbers)
            lines.append(f'{element} {pins[0]} {pins[1]} {value:.15g}')
            evidence.append(reference)
            continue
        if c['ref'] not in models:raise ValueError('SPICE_COMPONENT_MODEL_REQUIRED: '+c['ref'])
        model,folder=models[c['ref']]
        if model['engine']=='temperature_resistance':
            from . import temperature_resistance
            profile=scenario.body_temperature_profiles.get(c['ref'])
            pins=instrument(c,index,model['pins'])
            lines.extend(temperature_resistance.emit(model,index,pins,scenario.temperature_c,profile,dialect))
            evidence.append(dict(reference=c['ref'],model_id=folder.name,part_definition_hash=part['definition_hash'],
                conditions=model['conditions'],body_temperature_profile=profile.model_dump(mode='json') if profile else None,
                scope='source-linked nominal piecewise resistance at prescribed body temperature; no self-heating, thermal lag, ambient coupling or tolerance guarantee'))
            continue
        if not model['engine'].startswith('spice'):raise ValueError('SPICE_MODEL_ENGINE_MISMATCH')
        if not model['temperature_range_c'][0]<=scenario.temperature_c<=model['temperature_range_c'][1]:
            missing = []
            for ref, (bound, directory) in sorted(models.items()):
                if bound['engine'].startswith('spice') and not bound['temperature_range_c'][0] <= scenario.temperature_c <= bound['temperature_range_c'][1]:
                    missing.append(dict(reference=ref, model_id=directory.name, temperature_range_c=bound['temperature_range_c']))
            raise spice.SimulationFailure('SPICE_MODEL_TEMPERATURE_OUTSIDE_SCOPE',
                f'{scenario.temperature_c:g}°C 超出' + '、'.join(m['reference'] for m in missing) + ' 的模型温区。',
                '取得覆盖该温度的器件模型或厂家特性依据，再复跑同一温度场景。',
                requested_temperature_c=scenario.temperature_c, models_outside_scope=missing)
        for number in model['excluded_pins']:
            pin=next(p for p in c['pins'] if p['number']==number)
            if (c['ref'],number) in mapping or s.get('pin_intents',{}).get(c['id']+':'+pin['pin_id'],{}).get('intent')!='no_connect':raise ValueError('MODEL_EXCLUDED_PIN_NOT_NC')
        pins=instrument(c,index,model['pins'])
        prefix='X' if model['engine']=='spice_subcircuit' else model['device']
        entry=model['name'];body_node=None;temperature_adaptation=None;dialect_adaptation=None
        if model.get('external_ltspice') is not None:
            from . import ltspice
            path=ltspice.resolve_model(model['external_ltspice'],model['name'],(model.get('spice_units') or [model['pins']])[0])
            filename='vendor_'+model['external_ltspice']['model_sha256']+'.sub';vendor_models[filename]=path
            validated=['.include '+filename]
        else:
            from .spice_bundle import read
            if model.get('source_spice_dialect') is not None:
                dialect_adaptation=dict(source=model['source_spice_dialect'],target=model['spice_dialect'])
            validated=read(model,folder,adaptation=dialect_adaptation)
        if c['ref'] in scenario.body_temperature_profiles:
            from .spice_temperature import adapt
            profile=scenario.body_temperature_profiles[c['ref']]
            validated,entry,body_node,source,count=adapt(validated,entry,index,profile,model['temperature_range_c'],dialect)
            lines.append(source)
            temperature_adaptation=dict(variable='TEMP',behavioral_occurrences=count,body_temperature_profile=profile.model_dump(mode='json'),
                scope='Instance-local behavioral TEMP only; semiconductor/process temperature remains scenario.temperature_c. Immutable source unchanged; adapted deck retained.')
        if model['engine']=='spice_subcircuit' and model.get('external_ltspice') is None:
            from .spice_bundle import isolate
            validated,entry=isolate(validated,entry,index)
        if model.get('spice_units'):
            physical=dict(zip(model['pins'],pins))
            physical.update({number:f'unit_nc_{index}_{j}' for j,number in enumerate(model['excluded_pins'])})
            for unit_index,unit in enumerate(model['spice_units']):
                lines.append(f'X{index}_{unit_index} '+' '.join([*(physical[number] for number in unit),*([body_node] if body_node else [])])+' '+entry)
        else:lines.append(f'{prefix}{index} '+ ' '.join([*pins,*([body_node] if body_node else [])])+' '+entry)
        if entry in model_texts and model_texts[entry]!=validated:raise ValueError('SPICE_MODEL_NAME_COLLISION')
        model_texts[entry]=validated
        evidence.append(dict(reference=c['ref'],model_id=folder.name,part_definition_hash=part['definition_hash'],conditions=model['conditions'],
            physical_unit_mapping=model.get('spice_units'),excluded_physical_pins=model['excluded_pins'],body_temperature_adaptation=temperature_adaptation,
            dependency_assets=model.get('dependency_assets',[]),source_scope='instance_local_subcircuit' if model['engine']=='spice_subcircuit' and model.get('external_ltspice') is None else 'declared_external_or_device_model'))
        if dialect_adaptation is not None:evidence[-1]['spice_dialect_adaptation']=dialect_adaptation
    boundary={n['id'] for n in s['nets'].values() if any(roles[p['component_id']]=='external_port' for p in n['pins'])}
    def fixture_node(terminal):
        if instrument_ports and (terminal.ref,terminal.number) not in port_rows:raise ValueError('PORT_MEASUREMENT_REQUIRES_EXPLICIT_CONNECTOR_FIXTURE_TERMINAL')
        return names[resolve_terminal(mapping,terminal)]
    def port_term(positive,negative,term):
        if instrument_ports:
            port_rows[positive.ref,positive.number]['terms'].append(dict(term,factor=1))
            port_rows[negative.ref,negative.number]['terms'].append(dict(term,factor=-1))
    def voltage_vector(node):return None if node=='0' else 'v('+node+')'
    for i,drive in enumerate(scenario.drives):
        p=resolve_terminal(mapping,drive.positive);n=resolve_terminal(mapping,drive.negative)
        if p not in boundary or n not in boundary:raise ValueError('SPICE_DRIVE_REQUIRES_EXTERNAL_CONNECTOR_BOUNDARY')
        if drive.series_resistance_ohm is not None and p==n:raise ValueError('SERIES_DRIVE_REQUIRES_DISTINCT_BOUNDARY_NETS')
        lines.extend(drive_lines(drive,i,fixture_node(drive.positive),fixture_node(drive.negative)))
        port_term(drive.positive,drive.negative,dict(kind='direct',vector='i(vdrive'+str(i)+')'))
    for entry in evidence:
        if entry['reference'] in initial_states:
            entry['initial_state'] = initial_states[entry['reference']]
    for i,load in enumerate(scenario.boundary_loads):
        p=resolve_terminal(mapping,load.positive);n=resolve_terminal(mapping,load.negative)
        if p==n or p not in boundary or n not in boundary:raise ValueError('SPICE_LOAD_REQUIRES_DISTINCT_EXTERNAL_BOUNDARY')
        if load.current_sink is not None:
            sink=load.current_sink
            positive,negative=fixture_node(load.positive),fixture_node(load.negative)
            lines.append(f'Bfixture{i} {positive} {negative} I={sink.current_a:.15g}*max(0,min(1,V({positive},{negative})/{sink.compliance_v:.15g}))')
            port_term(load.positive,load.negative,dict(kind='current_sink',positive=voltage_vector(positive),
                      negative=voltage_vector(negative),current_a=sink.current_a,compliance_v=sink.compliance_v))
        elif load.resistance_ohm is not None:
            target=fixture_node(load.negative) if load.inductance_h is None else f'fixture_port{i}'
            lines.append(f'Rfixture{i} {fixture_node(load.positive)} {target} {load.resistance_ohm:.15g}')
            if load.inductance_h is not None:lines.append(f'Lfixture{i} {target} {fixture_node(load.negative)} {load.inductance_h:.15g}')
            port_term(load.positive,load.negative,dict(kind='ohmic',positive=voltage_vector(names[p]),negative=voltage_vector(target),resistance_ohm=load.resistance_ohm))
        else:
            lines.append(f'Cfixture{i} {fixture_node(load.positive)} {fixture_node(load.negative)} {load.capacitance_f:.15g}')
            if scenario.engine=='spice_transient':
                vector=f'i(cfixture{i})' if dialect=='ltspice' else f'@cfixture{i}[i]'
                port_term(load.positive,load.negative,dict(kind='direct',vector=vector))
                if instrument_ports and dialect!='ltspice':save_currents.append(vector)
            elif scenario.engine=='spice_ac':port_term(load.positive,load.negative,dict(kind='ac_capacitance',positive=voltage_vector(names[p]),negative=voltage_vector(names[n]),capacitance_f=load.capacitance_f))
            else:port_term(load.positive,load.negative,dict(kind='zero'))
            if load.initial_v is not None:
                if names[n]!='0':raise ValueError('SPICE_INITIAL_CAPACITOR_REQUIRES_GROUND_REFERENCE')
                if names[p] in initial_voltages and initial_voltages[names[p]]!=load.initial_v:
                    raise ValueError('SPICE_INITIAL_CAPACITOR_VOLTAGE_CONFLICT')
                initial_voltages[names[p]]=load.initial_v
    # Solve the initial operating point with voltage constraints. UIC would
    # leave connected on-board capacitors at zero and produce ideal impulses.
    for node,value in initial_voltages.items():lines.append(f'.ic V({node})={value:.15g}')
    if scenario.operating_point_hints:
        if dialect!='ltspice':raise ValueError('OPERATING_POINT_HINT_REQUIRES_LTSPICE')
        seen=set()
        for hint in scenario.operating_point_hints:
            net=resolve_terminal(mapping,hint.terminal)
            node=names[net]
            if net in boundary or node=='0':raise ValueError('OPERATING_POINT_HINT_REQUIRES_INTERNAL_NET')
            if net in seen:raise ValueError('OPERATING_POINT_HINT_NET_DUPLICATED')
            seen.add(net)
            lines.append(f'.nodeset V({node})={hint.guess_v:.15g}')
    for raw in model_texts.values():lines.extend(raw)
    lines.append(f'.temp {scenario.temperature_c:.15g}')
    if save_currents:lines.append('.save all '+' '.join(save_currents))
    if scenario.engine=='spice_op':lines.append('.op')
    elif scenario.engine=='spice_transient':lines.append(transient_recovery.transient_directive(scenario.step_s,scenario.stop_s))
    elif scenario.engine=='spice_ac':lines.append(f'.ac lin {scenario.points} {scenario.frequency_hz[0]:.15g} {scenario.frequency_hz[1]:.15g}')
    else:raise ValueError('SPICE_ANALYSIS_NOT_SUPPORTED')
    lines.append('.end')
    return '\n'.join(lines)+'\n',names,evidence,probes,vendor_models


def lowpass_instruments(requirements,mapping,names):
    """Build isolated measurement branches from the actual resolved board nets."""
    instruments={};channels={};lines=[]
    for r in requirements:
        if r.lowpass is None:continue
        p=names[resolve_terminal(mapping,r.positive)];n=names[resolve_terminal(mapping,r.negative)]
        if p==n:raise ValueError('LOWPASS_REQUIRES_DISTINCT_MEASUREMENT_NETS')
        response=r.lowpass.response;cutoff=r.lowpass.cutoff_hz
        key=(p,n,response,cutoff)
        if key not in channels:
            i=len(channels);prefix=f'mcp_meter_{i}';source=prefix+'_source';out=prefix+'_out'
            resistance=1000.;tau=(1/math.tau)/cutoff
            capacitance=tau/resistance
            lines.append(f'E{prefix} {source} 0 {p} {n} 1')
            if response=='butterworth_2':
                middle=prefix+'_rl';inductance=resistance/math.sqrt(2)*tau
                lines.extend([f'R{prefix} {source} {middle} {resistance:.17g}',f'L{prefix} {middle} {out} {inductance:.17g}'])
                capacitance*=math.sqrt(2)
            else:
                lines.append(f'R{prefix} {source} {out} {resistance:.17g}')
            if not math.isfinite(capacitance) or capacitance<=0 or (response=='butterworth_2' and not math.isfinite(inductance)):
                raise ValueError('LOWPASS_INSTRUMENT_VALUE_UNREPRESENTABLE')
            lines.append(f'C{prefix} {out} 0 {capacitance:.17g}')
            channels[key]=f'v({out})'
        instruments[r.id]=channels[key]
    return instruments,lines


def evaluate(s,request,folder):
    contract=request.contract;findings=[];measurements=[];runs=[]
    records=resolved_parts(s);models=resolve_models(s,request.model_bindings,records);mapping=terminals(s)
    for scenario in contract.scenarios:
        requirements=[r for r in contract.requirements if scenario.id in r.scenarios]
        try:
            if scenario.engine=='linear_dc':
                if scenario.linear_sources:
                    actual=list(s.get('power_sources',{}).values());ctx=s.get('power_envelope',{})
                    if len(actual)!=len(scenario.linear_sources):raise ValueError('FROZEN_SOURCE_COUNT_CHANGED')
                    for source in scenario.linear_sources:
                        p=resolve_terminal(mapping,source.positive);n=resolve_terminal(mapping,source.negative)
                        rows=[x for x in actual if x['net_id']==p and x['ground_net_id']==n]
                        if len(rows)!=1:raise ValueError('FROZEN_SOURCE_CONNECTION_CHANGED')
                        row=rows[0];matched=ctx and ctx['positive_pin']==row['boundary_pin'] and ctx['ground_net_id']==n
                        bounds=[ctx['min_v'],ctx['nominal_v'],ctx['max_v']] if matched else [row['nominal_v']]*3
                        if bounds!=[source.minimum_v,source.nominal_v,source.maximum_v]:raise ValueError('FROZEN_SOURCE_RANGE_CHANGED')
                analysis=linear_passive.analyze(s)
                if analysis is None:raise ValueError('LINEAR_COMPONENT_MODEL_COVERAGE_MISSING')
                runs.append(dict(scenario=scenario.id,engine=scenario.engine,result=analysis))
                if analysis['verdict']!='pass':raise ValueError('LINEAR_REFERENCE_INCOMPLETE_OR_FAILED')
                for r in requirements:
                    p=resolve_terminal(mapping,r.positive);n=resolve_terminal(mapping,r.negative)
                    # Independent interval subtraction is conservative for differential output.
                    low=analysis['rails'][p]['min_v']-analysis['rails'][n]['max_v'];high=analysis['rails'][p]['max_v']-analysis['rails'][n]['min_v']
                    measurements.append(dict(requirement=r.id,scenario=scenario.id,minimum=low,maximum=high,passed=low>=r.minimum and high<=r.maximum,scope='exhaustive_positive_linear_DC_corners'))
                continue
            if scenario.engine=='digital_truth':
                from .digital_truth import evaluate as digital_evaluate
                from . import digital_power
                result=digital_evaluate(s,scenario,models,records,mapping,requirements)
                reference=digital_power.reference(s,scenario,models,records,mapping,result)
                measurements.extend(result['measurements']);runs.append(dict(scenario=scenario.id,engine=scenario.engine,result=result,electrical_reference=reference,models=reference['models'],solver=dict(dialect='digital_static',runtime_sha256=digital_power.runtime_hash())));continue
            instrument_ports=any(r.measurement_scope=='external_port' or (r.ratio and any(t.measurement_scope=='external_port' for t in r.ratio.numerator+r.ratio.denominator)) for r in requirements)
            spice_scenario=scenario.model_copy(update={'engine':'spice_op'}) if scenario.engine=='spice_noise' else scenario
            deck,names,evidence,probes,vendor_models=compile_spice(s,spice_scenario,models,records,instrument_ports=instrument_ports)
            dialects={model.get('spice_dialect','native') for model,_ in models.values() if model['engine'].startswith('spice')}
            if len(dialects)>1:raise ValueError('MIXED_SPICE_DIALECTS_REQUIRE_QUALIFIED_COMMON_MODE')
            dialect=next(iter(dialects),'native')
            if scenario.engine=='spice_noise':
                from . import noise_analysis
                first=requirements[0]
                p=names[resolve_terminal(mapping,first.positive)];n=names[resolve_terminal(mapping,first.negative)]
                result,run=noise_analysis.evaluate(deck,folder/scenario.id,scenario,p,n,evidence,models,dialect)
                runs.append(run)
                for r in requirements:
                    measurements.append(dict(requirement=r.id,scenario=scenario.id,quantity=r.quantity,aggregation='spectral_rms',
                        minimum=result,maximum=result,passed=r.minimum<=result<=r.maximum,units='V RMS',
                        scope='sampled_integrated_small_signal_noise_of_declared_models_about_dc_bias'))
                continue
            lowpass_vectors,lowpass_lines=lowpass_instruments(requirements,mapping,names)
            if lowpass_lines:deck=deck[:-5]+'\n'.join(lowpass_lines)+'\n.end\n'
            vectors=['v('+node+')' for node in names.values() if node!='0']
            vectors.extend(lowpass_vectors.values())
            if scenario.engine!='spice_ac':vectors+=['i(vdrive'+str(i)+')' for i in range(len(scenario.drives))]
            vectors+=[row['vector'] for pins in probes.values() for row in pins if 'terms' not in row]
            from . import port_currents
            vectors+=port_currents.vectors(probes)
            if scenario.engine=='spice_transient':vectors.append('time')
            if scenario.engine=='spice_ac':vectors.append('frequency')
            vectors=list(dict.fromkeys(vectors))
            artifacts=folder/scenario.id
            attempts=None
            if scenario.transient_recovery is not None:
                meta,waves,artifacts,attempts=transient_recovery.run(deck,vectors,scenario,artifacts,dialect=dialect,vendor_models=vendor_models)
            else:
                resource_options={} if scenario.solver_timeout_s is None else dict(timeout=scenario.solver_timeout_s)
                meta,waves=spice.run(transient_recovery.integration_deck(deck,scenario.integration_method),vectors,artifacts,dialect=dialect,vendor_models=vendor_models,**resource_options)
            port_currents.materialize(probes,waves,scenario.engine)
            if scenario.engine=='spice_transient' and attempts is None:
                transient_recovery.require_transient_coverage(waves,scenario.stop_s)
            if scenario.engine=='spice_ac' and (len(waves['frequency'])!=scenario.points or not np.allclose([waves['frequency'][0].real,waves['frequency'][-1].real],scenario.frequency_hz,rtol=1e-9)):
                raise ValueError('AC_ANALYSIS_INCOMPLETE')
            run=dict(scenario=scenario.id,engine=scenario.engine,solver=meta,models=evidence,artifact_directory=str(artifacts));runs.append(run)
            for entry in evidence:
                if 'initial_state' not in entry:continue
                initial=entry['initial_state']
                solved=float(waves['v('+initial['solver_node']+')'][0])
                initial['solved_initial_node_v']=solved
                if waves['time'][0]!=0 or not math.isclose(solved,initial['expected_node_v'],abs_tol=1e-9,rel_tol=1e-9):
                    raise ValueError('BOARD_CAPACITOR_INITIAL_STATE_NOT_REALIZED: '+entry['reference'])
            if scenario.operating_point_hints:
                hints=[]
                for hint in scenario.operating_point_hints:
                    node=names[resolve_terminal(mapping,hint.terminal)]
                    hints.append(dict(**hint.model_dump(mode='json'),solver_node=node,
                                      solved_initial_v=float(waves['v('+node+')'][0])))
                run['operating_point_hints']=hints
            if attempts is not None:run['solver_attempts']=attempts
            if instrument_ports:run['external_port_observations']=dict(method='sum of original fixture branches; no added circuit elements',rows=port_currents.rows(probes))
            length=len(next(iter(waves.values())))
            def voltage(t):
                name=names[resolve_terminal(mapping,t)]
                return np.zeros(length) if name=='0' else waves['v('+name+')']
            def observation(measurement):
                signal=voltage(measurement.positive)-voltage(measurement.negative)
                if measurement.quantity in ('current','power'):
                    pins=probes.get(measurement.positive.ref)
                    if not pins:raise ValueError('MEASUREMENT_COMPONENT_HAS_NO_CURRENT_PROBES')
                    port=records[measurement.positive.ref]['kind']=='connector' and all(pin.get('scope')=='external_port' for pin in pins)
                    if port!=(measurement.measurement_scope=='external_port'):raise ValueError('MEASUREMENT_COMPONENT_OR_PORT_SCOPE_MISMATCH')
                    selected=[pin for pin in pins if pin['number']==measurement.positive.number]
                    if len(selected)!=1:raise ValueError('MEASUREMENT_CURRENT_PIN_UNMAPPED')
                    if measurement.quantity=='current':signal=waves[selected[0]['vector']]
                    elif port:signal=signal*waves[selected[0]['vector']]
                    else:
                        reference=voltage(measurement.negative)
                        signal=sum(((np.zeros(length) if names[pin['net_id']]=='0' else waves['v('+names[pin['net_id']]+')'])-reference)*waves[pin['vector']] for pin in pins)
                return signal
            if scenario.engine!='spice_ac':
                def net_voltage(nid):return np.zeros(length) if names[nid]=='0' else waves['v('+names[nid]+')']
                sources=[];stress=[];stress_complete=True
                for i,d in enumerate(scenario.drives):
                    p=resolve_terminal(mapping,d.positive);n=resolve_terminal(mapping,d.negative);current=-waves['i(vdrive'+str(i)+')'];v=voltage(d.positive)-voltage(d.negative)
                    sources.append(dict(net_id=p,ground_net_id=n,drive_kind=d.kind,min_v=float(v.min()),max_v=float(v.max()),start_v=float(v[0]),end_v=float(v[-1]),min_current_a=float(current.min()),max_current_a=float(current.max()),pulse=d.pulse,pwl=d.pwl,sine=d.sine.model_dump(mode='json') if d.sine else None))
                    if d.non_sinking_supply is not None:sources[-1]['non_sinking_supply']=d.non_sinking_supply.model_dump(mode='json')
                    if d.open_drain is not None:sources[-1]['open_drain']=d.open_drain.model_dump(mode='json')
                    if d.series_resistance_ohm is not None:sources[-1]['series_resistance_ohm']=d.series_resistance_ohm
                    if d.series_connection is not None:sources[-1]['series_connection']=d.series_connection.model_dump(mode='json')
                for ref,pins in probes.items():
                    if records[ref]['kind']=='connector':continue
                    f=records[ref]['facts'];power=sum(net_voltage(row['net_id'])*waves[row['vector']] for row in pins);currents={row['number']:float(np.max(np.abs(waves[row['vector']]))) for row in pins}
                    row=dict(reference=ref,maximum_dissipation_w=float(power.max()),terminal_currents_a=currents);stress.append(row)
                    if records[ref]['kind'] in ('resistor','capacitor'):
                        v=net_voltage(pins[0]['net_id'])-net_voltage(pins[1]['net_id']);rating=f.get('voltage_rating_v');row['maximum_voltage_v']=float(np.max(np.abs(v)))
                        if rating is None:stress_complete=False
                        elif row['maximum_voltage_v']>rating:raise ValueError('SIMULATED_PASSIVE_VOLTAGE_EXCEEDED: '+ref)
                        if records[ref]['kind']=='resistor':
                            if f.get('rated_power_w') is None:stress_complete=False
                            elif row['maximum_dissipation_w']>f['rated_power_w']:raise ValueError('SIMULATED_RESISTOR_POWER_EXCEEDED: '+ref)
                    if ref in models:
                        model=models[ref][0]
                        row.update(terminal_current_limit_covered_pins=sorted(model.get('terminal_current_limits_a',{})),terminal_current_limit_uncovered_pins=sorted(set(model['pins'])-set(model.get('terminal_current_limits_a',{}))),model_terminal_current_sum_reference_a=float(np.max(np.abs(sum(waves[pin['vector']] for pin in pins)))),power_reference_scope='sum of model terminal powers; not qualified as physical thermal dissipation')
                        if not model.get('voltage_limits') or not model.get('power_limit_w') or not model.get('terminal_current_limits_a'):stress_complete=False
                        for limit in model.get('voltage_limits',[]):
                            v=voltage(Terminal(ref=ref,number=limit['positive']))-voltage(Terminal(ref=ref,number=limit['negative']))
                            if np.min(v)<limit['minimum_v']-1e-9 or np.max(v)>limit['maximum_v']+1e-9:raise ValueError('SIMULATED_MODEL_VOLTAGE_LIMIT: '+ref)
                        if model.get('power_limit_w') and row['maximum_dissipation_w']>model['power_limit_w']:raise ValueError('SIMULATED_MODEL_POWER_LIMIT: '+ref)
                        if any(currents[number]>limit for number,limit in model.get('terminal_current_limits_a',{}).items()):raise ValueError('SIMULATED_MODEL_CURRENT_LIMIT: '+ref)
                run['electrical_reference']=dict(sources=sources,component_stress=stress,stress_reference_complete=stress_complete,stress_reference_scope='declared model limits only; uncovered pin limits and physical current observability are not qualified',temperature_c=scenario.temperature_c,duration_s=scenario.stop_s if scenario.engine=='spice_transient' else None,scope='sampled_nominal_model_reference_not_guaranteed_worst_case_or_physical_current')
            for r in requirements:
                if r.thdn:
                    from .audio_spectrum import measure
                    if resolve_terminal(mapping,r.positive)==resolve_terminal(mapping,r.negative):
                        raise ValueError('THDN_REQUIRES_DISTINCT_MEASUREMENT_NETS')
                    result=measure(observation(r),waves['time'],r.time_window_s,r.thdn)
                    value=result['in_band_trace_residual_rms_v']/result['fundamental_rms_v']
                    if not math.isfinite(value):raise ValueError('THDN_RATIO_NONFINITE')
                    measurements.append(dict(requirement=r.id,scenario=scenario.id,quantity=r.quantity,
                        aggregation='in_band_rms_ratio',minimum=value,maximum=value,passed=r.minimum<=value<=r.maximum,
                        units='1',thdn_evidence=result,scope='in_band_residual_present_in_the_sampled_model_trace'))
                    continue
                if r.i2c:
                    from .i2c_protocol import measure
                    data_p,data_n=(resolve_terminal(mapping,t) for t in (r.positive,r.negative))
                    clock_p,clock_n=(resolve_terminal(mapping,t) for t in (r.i2c.clock_positive,r.i2c.clock_negative))
                    if data_p==clock_p or data_p==data_n or clock_p==clock_n or data_p==clock_n or clock_p==data_n:
                        raise ValueError('I2C_REQUIRES_DISTINCT_DATA_AND_CLOCK_NETS')
                    result=measure(observation(r),voltage(r.i2c.clock_positive)-voltage(r.i2c.clock_negative),waves['time'],r.time_window_s,r.i2c)
                    count=len(result['frames'])
                    measurements.append(dict(requirement=r.id,scenario=scenario.id,quantity=r.quantity,aggregation='decode',
                        minimum=count,maximum=count,passed=result['passed'],i2c_evidence=result,
                        scope='sampled_i2c_waveform_on_resolved_nets_under_declared_models'))
                    continue
                if r.i2s:
                    from .i2s_protocol import measure
                    positive=(r.positive,r.i2s.clock_positive,r.i2s.ws_positive)
                    negative=(r.negative,r.i2s.clock_negative,r.i2s.ws_negative)
                    signals={resolve_terminal(mapping,t) for t in positive}
                    returns={resolve_terminal(mapping,t) for t in negative}
                    if len(signals)!=3 or signals & returns:
                        raise ValueError('I2S_REQUIRES_DISTINCT_DATA_CLOCK_AND_WS_NETS')
                    data,clock,ws=[voltage(p)-voltage(n) for p,n in zip(positive,negative)]
                    result=measure(data,clock,ws,waves['time'],r.time_window_s,r.i2s)
                    count=len(result['words'])
                    measurements.append(dict(requirement=r.id,scenario=scenario.id,quantity=r.quantity,aggregation='decode',
                        minimum=count,maximum=count,passed=result['passed'],i2s_evidence=result,
                        scope='sampled_i2s_receiver_waveform_on_resolved_nets_under_declared_models'))
                    continue
                if r.ratio:
                    from .measurements import ratio_of_aggregates
                    numerator=[term.polarity*observation(term) for term in r.ratio.numerator]
                    denominator=[term.polarity*observation(term) for term in r.ratio.denominator]
                    result=ratio_of_aggregates(numerator,denominator,r.aggregation,waves.get('time') if scenario.engine=='spice_transient' else None,r.time_window_s,r.ratio.denominator_minimum_abs)
                    value=result['ratio']
                    measurements.append(dict(requirement=r.id,scenario=scenario.id,quantity='ratio',aggregation=r.aggregation,minimum=value,maximum=value,passed=r.minimum<=value<=r.maximum,units='1',ratio_evidence=result,scope='ratio_of_same_trace_aggregates_under_declared_models_not_physical_or_worst_case_efficiency'))
                    continue
                if r.lowpass is not None:
                    from .measurements import aggregate,samples
                    vector=lowpass_vectors[r.id];axis=waves['time']
                    signal,time=samples(waves[vector],axis,r.time_window_s)
                    start,end=r.time_window_s
                    gap=float(np.max(np.diff(axis)[(axis[:-1]<end)&(axis[1:]>start)]))
                    if gap*r.lowpass.cutoff_hz>0.05*(1+1e-8):raise ValueError('LOWPASS_TRACE_TOO_COARSE')
                    low,high=aggregate(signal,r.aggregation,time)
                    measurements.append(dict(requirement=r.id,scenario=scenario.id,quantity='voltage',aggregation=r.aggregation,
                        minimum=low,maximum=high,passed=r.minimum<=low and high<=r.maximum,units='V',
                        lowpass_evidence=dict(**r.lowpass.model_dump(),window_s=r.time_window_s,vector=vector,maximum_sample_interval_s=gap,
                                              method='non-loading unity-DC-gain differential SPICE lowpass'),
                        scope='sampled_filtered_deterministic_model_voltage_under_declared_conditions'))
                    continue
                signal=observation(r)
                if r.delay or r.settling:
                    from . import transient_events
                    event=r.delay or r.settling
                    trigger_signal=voltage(event.trigger.positive)-voltage(event.trigger.negative)
                    result=transient_events.measure(signal,trigger_signal,waves['time'],event)
                    value=result['seconds']
                    measurements.append(dict(requirement=r.id,scenario=scenario.id,quantity=r.quantity,aggregation='event',minimum=value,maximum=value,passed=r.minimum<=value<=r.maximum,units='s',event_evidence=result,scope='sampled_model_piecewise_linear_event_measurement_not_physical_or_unsampled_proof'))
                    continue
                if scenario.engine=='spice_ac' and r.quantity in ('voltage','current'):
                    from .measurements import ac_component
                    signal=ac_component(signal,r.ac_measurement or 'magnitude')
                ac_evidence=None
                if r.quantity in ('gain','gain_db','ac_crossing_frequency'):
                    denominator=voltage(r.denominator)-voltage(r.negative)
                    if np.any(np.abs(denominator)<1e-15):raise ValueError('GAIN_INPUT_ZERO')
                    signal=np.abs(signal/denominator)
                    if r.quantity in ('gain_db','ac_crossing_frequency'):
                        from .measurements import ac_gain_db,ac_frequency_crossing
                        reference=r.ac_crossing.reference_frequency_hz if r.ac_crossing else r.reference_frequency_hz
                        signal,ac_evidence=ac_gain_db(signal,waves['frequency'],reference)
                        if r.ac_crossing:
                            result=ac_frequency_crossing(signal,waves['frequency'],r.ac_crossing)
                            low,high=result['bracket_hz']
                            measurements.append(dict(requirement=r.id,scenario=scenario.id,quantity=r.quantity,aggregation='frequency_crossing_bracket',minimum=low,maximum=high,passed=r.minimum<=low and high<=r.maximum,units='Hz',crossing_evidence=result,ac_reference=ac_evidence,scope='sampled_ac_crossing_bracket_and_linear_estimate_not_unsampled_bandwidth_or_physical_guarantee'))
                            continue
                if np.iscomplexobj(signal):raise ValueError('COMPLEX_VOLTAGE_MEASUREMENT_UNDEFINED')
                from .measurements import aggregate,periodic
                period_evidence=None
                if r.quantity in ('frequency','duty_cycle'):
                    cycles=periodic(signal,waves['time'],r.threshold,r.time_window_s)
                    low,high=aggregate(cycles[r.quantity],'range');period_evidence=cycles['evidence']
                else:low,high=aggregate(signal,r.aggregation,waves.get('time') if scenario.engine=='spice_transient' else None,r.time_window_s)
                measurements.append(dict(requirement=r.id,scenario=scenario.id,quantity=r.quantity,aggregation=r.aggregation,minimum=low,maximum=high,passed=low>=r.minimum and high<=r.maximum,scope='specified_source_model_and_simulated_scenario_only',measurement_scope=r.measurement_scope,ac_measurement=(r.ac_measurement or 'magnitude') if scenario.engine=='spice_ac' and r.quantity in ('voltage','current') else None,current_convention=('positive_leaving_board_through_named_external_port_terminal' if r.measurement_scope=='external_port' else 'entering_named_component_terminal') if r.quantity=='current' else None,power_convention=('power_transferred_from_board_through_named_port_relative_to_named_return' if r.measurement_scope=='external_port' else 'sum_of_all_instrumented_terminal_powers') if r.quantity=='power' else None))
                if period_evidence:measurements[-1]['cycle_evidence']=period_evidence
                if ac_evidence is not None:measurements[-1].update(units='dB',ac_reference=ac_evidence)
        except (ValueError,KeyError) as exc:
            if isinstance(exc, spice.SimulationFailure):
                finding = dict(exc.finding)
                if scenario.engine == 'spice_transient' and 'solver_files' in finding:
                    finding['requested_stop_s'] = scenario.stop_s
            else:
                finding = dict(code=str(exc), next_action='Correct the identified model, connection or scenario evidence and re-run against the unchanged requirement.')
            findings.append(dict(finding, scenario=scenario.id, severity='error'))
            attempts_file=folder/scenario.id/'attempts.json'
            if attempts_file.is_file():findings[-1]['solver_attempts_path']=str(attempts_file)
    scenarios={scenario.id:scenario for scenario in contract.scenarios}
    for run in runs:
        if run['engine']!='spice_noise':continue
        scenario=scenarios[run['scenario']]
        bias=next((candidate for candidate in runs if matching_dc_bias(scenario,scenarios[candidate['scenario']]) and
                   len(candidate.get('electrical_reference',{}).get('sources',[]))==len(scenario.drives)),None)
        if bias is None:
            findings.append(dict(code='NOISE_MATCHING_MEASURED_DC_BIAS_MISSING',scenario=scenario.id,severity='error',next_action='Obtain the matching operating-point source evidence.'))
            continue
        run['electrical_reference']=dict(bias_scenario=bias['scenario'],sources=[dict(row,analysis_scope='matching_dc_bias_for_small_signal_noise',bias_scenario=bias['scenario']) for row in bias['electrical_reference']['sources']])
    requirements={r.id:r for r in contract.requirements}
    stationary_noise={row['requirement']:row for row in measurements if row.get('quantity')=='noise_voltage_rms'}
    proven_noise_scenarios={run['scenario'] for run in runs if run['engine']=='spice_noise' and 'electrical_reference' in run}
    for row in measurements:
        r=requirements[row['requirement']]
        noise_id=r.thdn.stationary_noise_requirement if r.thdn else None
        if noise_id is None:continue
        noise=stationary_noise.get(noise_id)
        if noise is None or not noise['passed'] or noise['scenario'] not in proven_noise_scenarios:
            row['passed']=False
            findings.append(dict(code='THDN_STATIONARY_NOISE_EVIDENCE_UNAVAILABLE',scenario=row['scenario'],severity='error',
                                 requirement=r.id,next_action='Complete the referenced stationary noise measurement and its DC bias check.'))
            continue
        evidence=row['thdn_evidence']
        total=math.hypot(evidence['in_band_trace_residual_rms_v'],noise['maximum'])
        value=total/evidence['fundamental_rms_v']
        if not math.isfinite(value):
            row['passed']=False
            findings.append(dict(code='THDN_RATIO_NONFINITE',scenario=row['scenario'],severity='error',requirement=r.id))
            continue
        evidence.update(stationary_noise_requirement=noise_id,stationary_noise_rms_v=noise['maximum'],
                        combined_residual_rms_v=total,noise_combination='quadrature with uncorrelated stationary small-signal model noise')
        row.update(minimum=value,maximum=value,passed=r.minimum<=value<=r.maximum,
                   scope='sampled_deterministic_trace_plus_stationary_small_signal_model_noise_at_declared_dc_bias')
    if contract.i2c_pullups:
        resistors = [c for c in s['components'].values() if c['library_id'] == 'Device:R']
        for check in contract.i2c_pullups:
            row = dict(requirement=check.id, scenario=None, quantity='i2c_pullups',
                       scope='source_bound_nominal_pullups_on_actual_terminal_nets', passed=False)
            try:
                sda, scl, supply = (resolve_terminal(mapping, pin) for pin in (check.sda, check.scl, check.supply))
                if len({sda, scl, supply}) != 3:
                    raise ValueError('I2C_PULLUP_TERMINALS_SHORTED')
                row['pullups'] = {
                    signal: verify_pullup(resistors, mapping, s['part_bindings'], net, supply, ohm, signal)
                    for signal, net, ohm in (('SDA', sda, check.sda_ohm), ('SCL', scl, check.scl_ohm))
                }
                row['passed'] = True
            except ValueError as error:
                row['error'] = str(error)
                findings.append(dict(code=str(error).split(':', 1)[0], severity='error',
                                     requirement=check.id, message=str(error)))
            measurements.append(row)
    for row in measurements:
        if not row['passed']:findings.append(dict(code='FUNCTIONAL_REQUIREMENT_FAILED',severity='error',requirement=row['requirement'],scenario=row['scenario'],next_action='Revise circuit or select a source-supported part; preserve requirement limits.'))
    expected={(r.id,scenario) for r in contract.requirements for scenario in r.scenarios}
    expected.update((check.id, None) for check in contract.i2c_pullups)
    actual={(m['requirement'],m['scenario']) for m in measurements}
    return dict(project_id=s['id'],revision=s['revision'],spec_sha256=spec_hash(s),contract_sha256=request.contract_sha256,verdict='pass' if not findings and actual==expected else 'fail',measurements=measurements,runs=runs,findings=findings,coverage=dict(required=len(expected),executed=len(actual)),physical_verified=False,qualification='functional_reference_under_frozen_declared_model_conditions_not_universal_or_physical_proof')


class FunctionalService:
    def functional_check(self,request):
        replacement = request.replacement
        replacements = request.replacements
        proposed = replacement is not None or replacements is not None
        with self.locked():
            current_code=registry.sha(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py'))))
            require(current_code==self.runtime_source_sha256,'SERVER_CODE_CHANGED_RESTART_REQUIRED','Server source changed after process initialization; restart before generating functional evidence.')
            state=copy.deepcopy(self.load(request.project_id))
            require(state['revision']==request.revision,'REVISION_CONFLICT','Project changed.')
            base_spec_sha256=spec_hash(state)
            if replacement is not None:
                state=self.replacement_snapshot(state,**replacement.model_dump())[0]
            elif replacements is not None:
                state=self.replacement_group_snapshot(state,[row.model_dump() for row in replacements])[0]
            require(spec_hash(state)==request.expected_spec_sha256,'FUNCTIONAL_SPEC_CHANGED','Actual design differs from the requested verification snapshot.')
            require(registry.sha(registry.canonical(request.contract.model_dump(mode='json')))==request.contract_sha256,'FUNCTIONAL_CONTRACT_CHANGED','Frozen functional contract hash differs.')
            folder=self.project_path(state['id'])/'functional_runs'/uuid.uuid4().hex;folder.mkdir(parents=True)
            (folder/'request.json').write_text(request.model_dump_json(indent=2)+'\n')
            (folder/'state.json').write_bytes(registry.canonical(state))
            frozen_backend=parts.rules_hash()
        # Expensive solvers operate only on the immutable snapshot. Holding the
        # shared data-root lock here blocks independent projects and recovery.
        with registry.using(self.root):
            result=evaluate(state,request,folder)
        with self.locked():
            current_code=registry.sha(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py'))))
            require(current_code==self.runtime_source_sha256,'SERVER_CODE_CHANGED_RESTART_REQUIRED','Server source changed during functional execution; restart and recheck.')
            require(parts.rules_hash()==frozen_backend,'FUNCTIONAL_BACKEND_CHANGED_DURING_RUN','Source-bound backend/catalog changed during execution; frozen evidence cannot be published.')
            current=self.load(request.project_id)
            base_current=current['revision']==request.revision and spec_hash(current)==base_spec_sha256
            result.update(run_id=folder.name,artifact_directory=str(folder),backend_sha256=frozen_backend,
                          applies_to_current_spec=not proposed and base_current)
            if proposed:
                result.update(base_spec_sha256=base_spec_sha256, replacement_base_current=base_current)
                if replacement is not None:
                    result['replacement'] = replacement.model_dump()
                else:
                    result['replacements'] = [row.model_dump() for row in replacements]
            (folder/'report.json').write_bytes(registry.canonical(result))
            index_path=self.project_path(state['id'])/'functional_index.json'
            index=json.loads(index_path.read_text()) if index_path.is_file() else []
            artifacts = file_snapshot(folder)
            entry=dict(run_id=folder.name,report_sha256=artifacts['report.json'],artifacts=artifacts)
            if proposed:
                entry['scope']='replacement'
            index.append(entry)
            from .common import write_json
            write_json(index_path,index)
            return result
