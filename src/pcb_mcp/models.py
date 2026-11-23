from typing import Annotated, Literal, Generic, TypeVar, Union
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

Id = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]
Revision = Annotated[int, Field(ge=0, strict=True)]
Short = Annotated[str, Field(min_length=1, max_length=100)]
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=100)]
LibraryName = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}$')]
PartKind = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Read(Strict):
    project_id: Id
    revision: Revision | None = None


class Mutation(Strict):
    project_id: Id
    expected_revision: Revision
    idempotency_key: IdempotencyKey


class Create(Strict):
    name: Short
    profile: Literal['led_series_experiment','schematic_experiment'] = 'schematic_experiment'
    idempotency_key: IdempotencyKey | None = None


class Search(Strict):
    query: Annotated[str, Field(min_length=1, max_length=80)]
    kind: Literal['symbol'] = 'symbol'
    library: LibraryName | None = None
    limit: Annotated[int, Field(ge=1,le=50)] = 20
    offset: Annotated[int, Field(ge=0,le=100000)] = 0


class Candidate(Strict):
    candidate_id: Id


class Add(Mutation):
    candidate_id: Id
    ref: Annotated[str, Field(pattern=r'^[A-Z][A-Z0-9]{0,7}$')]
    # Values are typed and normalized by symbol role, never executable source.
    value: Annotated[str, Field(min_length=1, max_length=40)]


class Parameter(Mutation):
    component_id: Id
    value: Annotated[str, Field(min_length=1, max_length=40)]


class NetCreate(Mutation):
    name: Annotated[str, Field(pattern=r'^[A-Z][A-Z0-9_]{0,39}$')]
    kind: Literal['signal', 'power', 'ground']


class Pin(Strict):
    component_id: Id
    pin_id: Id


class Members(Mutation):
    net_id: Id
    pins: Annotated[list[Pin], Field(max_length=200)]


class NetPinMove(Strict):
    pin: Pin
    from_net_id: Id
    to_net_id: Id


class NetPinMoves(Mutation):
    moves: Annotated[list[NetPinMove], Field(min_length=1, max_length=200)]


class CircuitRemove(Mutation):
    component_ids: Annotated[list[Id], Field(min_length=1, max_length=127)]


class Build(Mutation):
    layout_profile: Literal['auto','led_loop','labeled_grid'] = 'auto'


class BuildRef(Strict):
    project_id: Id
    build_id: Id


class Preview(BuildRef):
    format: Literal['svg'] = 'svg'


class Export(BuildRef):
    mode: Literal['validated'] = 'validated'
    format: Literal['review_bundle'] = 'review_bundle'


class Artifact(Strict):
    project_id: Id
    build_id: Id
    artifact_id: Literal['schematic', 'preview', 'netlist', 'erc', 'manifest']


T = TypeVar('T')


class Reply(Strict, Generic[T]):
    ok: Literal[True] = True
    project_id: str | None = None
    revision: int | None = None
    data: T


class Capabilities(Strict):
    contract_version: str
    versions: dict[str,str]
    kicad: dict
    profile: str
    library_scope: list[str]
    transport: Literal['stdio']
    features: dict[str,bool]
    limitations: list[str]


class ProjectCreated(Strict):
    project_id: Id
    revision: Revision
    profile: Literal['led_series_experiment','schematic_experiment']


class ProjectState(Strict):
    id: Id
    name: str
    profile: Literal['led_series_experiment','schematic_experiment']
    revision: Revision
    components: dict
    nets: dict
    builds: dict
    requirements: list[dict] = []
    constraints: dict = {}
    pin_intents: dict = {}
    power_sources: dict = {}
    footprint_bindings: dict = {}
    board_constraints: dict = {}
    pcb_builds: dict = {}
    part_bindings: dict = {}
    operating_conditions: dict = {}
    manufacturing_reviews: dict = {}
    power_envelope: dict = {}
    provenance: dict = {}
    pcb_copper_policy: dict = {}
    passive_rating_requirements: dict = {}
    power_budget_policy: dict = {}


class CandidateData(Strict):
    candidate_id: Id
    library_id: str
    symbol: str
    definition_hash: str
    pins: list[dict]
    completeness: Literal['symbol_only']
    manufacturer: None
    mpn: None
    offers: list
    footprint_bindings: list
    source: str
    supported: bool = True
    unsupported_reasons: list[str] = []
    datasheet: str | None = None
    description: str = ''
    source_definition_hash: str = ''
    inherited: bool = False


class SearchData(Strict):
    candidates: list[CandidateData]
    source_status: Literal['local_library_available']
    total: int = 0
    next_offset: int | None = None


class AddedData(Strict):
    project_id: Id
    revision: Revision
    component_id: Id
    pins: list[dict]
    completeness: Literal['symbol_only']


class ParameterData(Strict):
    project_id: Id
    revision: Revision
    component_id: Id
    value: str


class NetData(Strict):
    project_id: Id
    revision: Revision
    net_id: Id


class MembersData(NetData):
    members: list[Pin]


class SpecReport(Strict):
    verdict: Literal['pass','fail','incomplete']
    revision: Revision
    rulepack: str
    findings: list[dict]
    coverage: list[dict]
    qualification: str


class RemoveComponent(Mutation):
    component_id: Id


class RemoveNet(Mutation):
    net_id: Id


class Intent(Mutation):
    pin: Pin
    intent: Literal['no_connect','unspecified']
    reason: Annotated[str,Field(max_length=500)] = ''


class Requirement(Strict):
    key: Annotated[str,Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
    value: str | int | float | bool
    origin: Literal['user_statement','proposed_assumption']
    source_message_ref: Annotated[str,Field(max_length=200)] | None = None


class Requirements(Mutation):
    items: Annotated[list[Requirement],Field(max_length=50)]


class MustConnect(Strict):
    kind: Literal['must_connect']
    pins: Annotated[list[Pin],Field(min_length=2,max_length=200)]


class MustSeparate(Strict):
    kind: Literal['must_separate']
    pins: Annotated[list[Pin],Field(min_length=2,max_length=200)]


class ConstraintSet(Mutation):
    constraint_id: Id | None = None
    constraint: Annotated[Union[MustConnect,MustSeparate],Field(discriminator='kind')]


class ConstraintRemove(Mutation):
    constraint_id: Id


class ProjectSummary(Strict):
    project_id: Id
    name: str
    profile: str
    revision: Revision
    component_count: int
    net_count: int


class ProjectList(Strict):
    projects: list[ProjectSummary]


class PowerSource(Mutation):
    source_id: Id | None = None
    net_id: Id
    ground_net_id: Id
    boundary_pin: Pin
    nominal_v: Annotated[float,Field(gt=0,le=24,allow_inf_nan=False)]
    reason: Annotated[str,Field(min_length=5,max_length=500)]


class PowerRemove(Mutation):
    source_id: Id


class RuleRequest(Strict):
    rulepack_id: Literal['esp32_module_v1','am2302_v1','tlv75533_v1','usb_c_sink_v1','txs0102_v1','r78k33_v1','usb_gate_v1']


class RulesData(Strict):
    rulepacks: list[dict]


class RuleData(Strict):
    rulepack: dict


class ElectricalReport(Strict):
    verdict: Literal['pass','fail','incomplete']
    revision: Revision
    findings: list[dict]
    coverage: list[dict]
    rails: dict
    qualification: str


class EditedData(Strict):
    project_id: Id
    revision: Revision
    entity_id: str


class RequirementsData(Strict):
    project_id: Id
    revision: Revision
    items: list[Requirement]
    verification: Literal['recorded_not_independently_verified']


class BuiltData(Strict):
    project_id: Id
    revision: Revision
    build_id: Id
    schematic_hash: str


class VerifyReport(Strict):
    verdict: Literal['pass','fail']
    revision: Revision
    build_id: Id
    expected_nets: dict
    actual_nets: dict
    components_match: bool
    no_connect_match: bool
    native_no_connect_nets: list[dict] = []
    native_no_connect_error: str | None = None
    native_source_fields: dict | None = None
    schematic_hash: str
    engine: str


class ERCReport(Strict):
    verdict: Literal['pass','fail']
    revision: Revision
    build_id: Id
    findings: list[dict]
    ignored_checks: list[dict]
    engine_version: str


class PreviewData(Strict):
    artifact_id: Literal['preview']
    sha256: str
    resource_uri: str
    layout_report: dict


class ExportData(Strict):
    build_id: Id
    revision: Revision
    bundle_path: str
    schematic_path: str
    manifest: dict


class ArtifactData(Strict):
    artifact_id: Literal['schematic','preview','netlist','erc','manifest']
    sha256: str
    content: str


class FootprintSearch(Strict):
    query: Annotated[str,Field(min_length=1,max_length=80)]
    library: LibraryName | None = None
    limit: Annotated[int,Field(ge=1,le=20)] = 10
    offset: Annotated[int,Field(ge=0,le=100000)] = 0


class FootprintData(Strict):
    candidate_id: Id
    footprint_id: str
    definition_hash: str
    pads: list[dict]
    supported: bool
    unsupported_reasons: list[str]
    completeness: Literal['geometry_only']
    description: str
    source: str


class FootprintResults(Strict):
    candidates: list[FootprintData]
    total: int
    next_offset: int | None


class FootprintBind(Mutation):
    component_id: Id
    candidate_id: Id
    mapping_mode: Literal['number_identity'] = 'number_identity'
    purpose: Literal['geometry_experiment'] = 'geometry_experiment'
    reason: Annotated[str,Field(min_length=5,max_length=500)]


class FootprintUnbind(Mutation):
    component_id: Id


class ComponentPlacement(Strict):
    component_id: Id
    x_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    y_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    rotation_deg: Literal[0,90,180,270] = 0
    reason: Annotated[str,Field(min_length=5,max_length=500)]


class CopperPathConstraint(Strict):
    source: Pin
    target: Pin
    max_length_mm: Annotated[float,Field(gt=0,le=500,allow_inf_nan=False)]
    max_vias: Annotated[int,Field(ge=0,le=16)] = 0
    reason: Annotated[str,Field(min_length=10,max_length=1000)]


class CopperKeepout(Strict):
    x_min_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    y_min_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    x_max_mm: Annotated[float,Field(gt=0,le=150,allow_inf_nan=False)]
    y_max_mm: Annotated[float,Field(gt=0,le=150,allow_inf_nan=False)]
    no_tracks: bool = True
    no_vias: bool = True
    no_fills: bool = True
    reason: Annotated[str,Field(min_length=5,max_length=500)]


class RoutePoint(Strict):
    x_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    y_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]


class LocalRoute(Strict):
    net_id: Id
    layer: Literal['F.Cu','In1.Cu','In2.Cu','B.Cu'] = 'F.Cu'
    width_mm: Annotated[float,Field(ge=.15,le=2,allow_inf_nan=False)]
    points: Annotated[list[RoutePoint],Field(min_length=2,max_length=64)]
    via_at_end: bool = False
    reason: Annotated[str,Field(min_length=5,max_length=500)]


class LocalVia(Strict):
    net_id: Id
    x_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    y_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    diameter_mm: Annotated[float,Field(ge=.5,le=2,allow_inf_nan=False)]
    drill_mm: Annotated[float,Field(ge=.2,le=1,allow_inf_nan=False)]
    filled_capped: bool = False
    reason: Annotated[str,Field(min_length=5,max_length=500)]


class StackupCopper(Strict):
    name: Literal['F.Cu','In1.Cu','In2.Cu','B.Cu']
    thickness_um: Annotated[float,Field(ge=5,le=210,allow_inf_nan=False)]


class StackupDielectric(Strict):
    kind: Literal['core','prepreg']
    thickness_mm: Annotated[float,Field(gt=0,le=5,allow_inf_nan=False)]
    material: Annotated[str,Field(min_length=1,max_length=80)]
    epsilon_r: Annotated[float,Field(gt=1,le=20,allow_inf_nan=False)]
    loss_tangent: Annotated[float,Field(ge=0,lt=1,allow_inf_nan=False)]


class BoardStackup(Strict):
    copper: Annotated[list[StackupCopper],Field(min_length=2,max_length=4)]
    dielectrics: Annotated[list[StackupDielectric],Field(min_length=1,max_length=3)]
    basis: Annotated[str,Field(min_length=10,max_length=2000,description='Source or proposed construction basis; dimensions alone do not establish manufacturer acceptance or controlled impedance.')]


class SilkscreenLabel(Strict):
    text: Annotated[str,Field(pattern=r'^[A-Za-z0-9+./_() -]{1,32}$')]
    x_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    y_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]

    @model_validator(mode='after')
    def visible_text(self):
        if not self.text.strip():raise ValueError('SILKSCREEN_LABEL_EMPTY')
        return self


class MountingHole(Strict):
    x_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    y_mm: Annotated[float,Field(ge=0,le=150,allow_inf_nan=False)]
    diameter_mm: Annotated[float,Field(ge=.3,le=10,allow_inf_nan=False)]
    keepout_mm: Annotated[float,Field(ge=0,le=10,allow_inf_nan=False,description='Radial clearance from the wall of the nonplated hole to copper and component bodies.')]


class ViaSize(Strict):
    drill_mm: Annotated[float,Field(ge=.2,le=1,allow_inf_nan=False)]
    pad_mm: Annotated[float,Field(ge=.45,le=2.5,allow_inf_nan=False)]


class InterfaceMarking(Strict):
    component_id: Id
    number: Annotated[str,Field(min_length=1,max_length=40)]
    role: Literal['pin_1','positive','return']
    net_id: Id | None = None


BoardDimension = Annotated[float,Field(ge=30,le=150,allow_inf_nan=False)]


class BoardConstraints(Mutation):
    layers: Literal[2,4] = 2
    thickness_mm: Annotated[float,Field(ge=.4,le=3.2,allow_inf_nan=False)] = 1.6
    stackup: BoardStackup | None = None
    outline: Literal['rectangle'] = 'rectangle'
    width_mm: BoardDimension = 60.0
    height_mm: BoardDimension = 40.0
    edge_margin_mm: Annotated[float,Field(ge=5,le=15,allow_inf_nan=False)] = 5.0
    clearance_mm: Annotated[float,Field(ge=.15,le=1,allow_inf_nan=False,description='Prototype geometry only; board-house capability and manufacturing approval are separate.')] = .25
    track_width_mm: Annotated[float,Field(ge=.15,le=2,allow_inf_nan=False,description='Global prototype width; no power-trace ampacity or voltage-drop qualification.')] = .3
    edge_clearance_mm: Annotated[float,Field(ge=.25,le=2,allow_inf_nan=False)] = .5
    min_drill_mm: Annotated[float,Field(ge=.2,le=1,allow_inf_nan=False)] = .3
    hole_clearance_mm: Annotated[float,Field(ge=.15,le=1,allow_inf_nan=False)] = .25
    hole_to_hole_mm: Annotated[float,Field(ge=.15,le=1,allow_inf_nan=False)] = .25
    placement_profile: Literal['bounded_grid','esp32_sensor_reference','explicit'] = 'bounded_grid'
    placements: Annotated[list[ComponentPlacement],Field(max_length=128)] = []
    ground_plane_net_id: Id | None = None
    ground_plane_reason: Annotated[str,Field(max_length=500)] = ''
    copper_keepouts: Annotated[list[CopperKeepout],Field(max_length=32)] = []
    local_routes: Annotated[list[LocalRoute],Field(max_length=4096)] = []
    local_vias: Annotated[list[LocalVia],Field(max_length=1024)] = []
    path_constraints: Annotated[list[CopperPathConstraint],Field(max_length=128)] = []
    silkscreen_labels: Annotated[list[SilkscreenLabel],Field(max_length=64)] = []
    mounting_holes: Annotated[list[MountingHole],Field(max_length=16)] = []
    via_size: ViaSize | None = Field(default=None,description='Exact nominal PCB via drill and flashed copper diameter. Component through-hole pads are separate.')
    component_height_max_mm: Annotated[float,Field(gt=0,le=200,allow_inf_nan=False)] | None = None
    interface_markings: Annotated[list[InterfaceMarking],Field(max_length=128)] = []

    @model_serializer(mode='wrap')
    def retain_legacy_shape(self, handler):
        result=handler(self)
        if not self.silkscreen_labels:result.pop('silkscreen_labels',None)
        if not self.mounting_holes:result.pop('mounting_holes',None)
        if self.via_size is None:result.pop('via_size',None)
        if self.component_height_max_mm is None:result.pop('component_height_max_mm',None)
        if not self.interface_markings:result.pop('interface_markings',None)
        return result

    @model_validator(mode='after')
    def layer_stack(self):
        from .stackup import validate
        validate(self.model_dump())
        centers={(label.x_mm,label.y_mm) for label in self.silkscreen_labels}
        if len(centers)!=len(self.silkscreen_labels):raise ValueError('SILKSCREEN_LABEL_POSITION_DUPLICATE')
        if any(not 0<x<self.width_mm or not 0<y<self.height_mm for x,y in centers):raise ValueError('SILKSCREEN_LABEL_OUTSIDE_BOARD')
        from .mounting import validate
        validate(self.model_dump())
        if self.via_size is not None:
            from .native_fabrication import units
            drill, pad = units(self.via_size.drill_mm), units(self.via_size.pad_mm)
            if drill < units(self.min_drill_mm) or pad - drill < units(.25):
                raise ValueError('VIA_SIZE_BELOW_PROCESS_MINIMUM')
            if any((units(via.drill_mm), units(via.diameter_mm)) != (drill, pad) for via in self.local_vias):
                raise ValueError('LOCAL_VIA_SIZE_DIFFERS_FROM_BOARD_CONSTRAINT')
        return self


class PCBPreflight(Strict):
    verdict: Literal['pass','fail']
    revision: Revision
    findings: list[dict]
    coverage: list[dict]


class PCBCreate(Mutation):
    schematic_build_id: Id
    routing_base_pcb_build_id: Id | None = None
    reuse_parent_pcb_build_id: Id | None = None


class PCBRef(Strict):
    project_id: Id
    pcb_build_id: Id


class PCBRetry(Mutation):
    pcb_build_id: Id


class CopperPadEscape(Strict):
    component_id: Id
    pins: Annotated[list[str],Field(min_length=1,max_length=16)]
    min_width_mm: Annotated[float,Field(ge=.15,le=1,allow_inf_nan=False)]
    half_window_mm: Annotated[float,Field(ge=.3,le=2.5,allow_inf_nan=False)]
    max_total_length_mm: Annotated[float,Field(gt=0,le=6,allow_inf_nan=False)]
    reason: Annotated[str,Field(min_length=20,max_length=500)]


class CopperNetRule(Strict):
    net_id: Id
    min_width_mm: Annotated[float,Field(ge=.15,le=2,allow_inf_nan=False)]
    design_current_a: Annotated[float,Field(gt=0,le=3,allow_inf_nan=False,description='Declared design screen, not measured current or a verified source/load bound.')]
    reference_drop_budget_v: Annotated[float,Field(ge=.001,le=1,allow_inf_nan=False,description='Budget for a conservative sum of track resistances at 20C; excludes pads/vias and is not an actual path drop.')]
    escape_profile: Literal['none','tusb320_gpio_ground_v1'] = 'none'
    escape_design_current_a: Annotated[float,Field(gt=0,le=.1,allow_inf_nan=False,description='Unverified total controller ground current design assumption, including all pin domains; only applicable after native backbone isolation check.')] | None = None
    pad_escapes: Annotated[list[CopperPadEscape],Field(max_length=16)] = []
    return_model: Literal['tracks','stitched_ground_planes'] = 'tracks'


class CopperLoopLeg(Strict):
    source: Pin
    target: Pin


class CopperLoop(Strict):
    name: Short
    legs: Annotated[list[CopperLoopLeg], Field(min_length=2,max_length=16)]
    maximum_track_drop_v: Annotated[float, Field(gt=0,le=1,allow_inf_nan=False)]


class CopperRules(Mutation):
    rules: Annotated[list[CopperNetRule],Field(min_length=1,max_length=100)]
    copper_thickness_um: Annotated[float,Field(ge=10,le=140,allow_inf_nan=False)]
    temperature_rise_c: Annotated[float,Field(ge=5,le=40,allow_inf_nan=False)]
    origin: Literal['proposed_assumption','user_statement'] = 'proposed_assumption'
    reason: Annotated[str,Field(min_length=5,max_length=500)]
    dc_loops: Annotated[list[CopperLoop], Field(max_length=16)] = Field(default_factory=list)


class CopperReport(Strict):
    project_id: Id
    pcb_build_id: Id
    revision: Revision
    verdict: Literal['fail','incomplete']
    geometry_verdict: Literal['pass','fail']
    reference_screen: Literal['pass','fail','incomplete']
    findings: list[dict]
    nets: list[dict]
    dc_track_paths: list[dict] = Field(default_factory=list)
    dc_loop_references: list[dict] = Field(default_factory=list)
    assumptions: dict
    evidence: dict
    thermal_qualified: Literal[False] = False
    full_path_drop_qualified: Literal[False] = False
    manufacturing_approved: Literal[False] = False
    qualification: str


class PCBStage(PCBRef):
    stage: Literal['unrouted','routed'] = 'routed'


class PCBRoute(PCBRef):
    max_passes: Annotated[int,Field(ge=1,le=100,description='Maximum routing passes; observed count checked. Hard wall timeout: 90 seconds.')] = 20


class PCBBuilt(Strict):
    pcb_build_id: Id
    revision: Revision
    board_hash: str
    qualification: str


class PCBCheck(Strict):
    pcb_build_id: Id
    revision: Revision
    verdict: Literal['pass','fail']
    findings: list[dict]
    details: dict


class PCBPreview(Strict):
    pcb_build_id: Id
    revision: Revision
    preview_path: str
    sha256: str
    resource_uri: str
    layer_paths: dict[str,str] = {}


class PCBExport(Strict):
    pcb_build_id: Id
    revision: Revision
    bundle_path: str
    manifest: dict

class PartSearch(Strict):
    query: Annotated[str,Field(min_length=1,max_length=80)]
    kind: PartKind | None = None

class PartData(Strict):
    candidate_id: Id
    manufacturer: str
    mpn: str
    kind: PartKind
    symbol_id: str
    footprint_id: str
    definition_hash: str
    facts: dict
    sources: list[dict]
    skus: list[dict]
    evidence_status: Literal['available','missing_or_changed']
    qualification: Literal['source_linked_candidate_not_human_qualified']

class PartResults(Strict):
    candidates: list[PartData]
    total: int
    scope: str

class PartBind(Mutation):
    component_id: Id
    candidate_id: Id
    purpose: Literal['source_linked_prototype'] = 'source_linked_prototype'

class PartUnbind(Mutation):
    component_id: Id

class OperatingConditions(Mutation):
    profile: Literal['led_indicator_dc_v1'] = 'led_indicator_dc_v1'
    positive_pin: Pin
    ground_net_id: Id
    nominal_v: Annotated[float,Field(gt=0,le=12,allow_inf_nan=False)] = 3.0
    min_v: Annotated[float,Field(gt=0,le=12,allow_inf_nan=False)] = 2.7
    max_v: Annotated[float,Field(gt=0,le=12,allow_inf_nan=False)] = 3.3
    available_current_ma: Annotated[float,Field(gt=0,le=1000,allow_inf_nan=False)] = 20.0
    ambient_c: Annotated[float,Field(ge=-40,le=85,allow_inf_nan=False)] = 25.0
    origin: Literal['user_statement','proposed_assumption'] = 'proposed_assumption'
    reason: Annotated[str,Field(min_length=5,max_length=500)]

class EngineeringReport(Strict):
    revision: Revision
    verdict: Literal['pass','fail','incomplete']
    findings: list[dict]
    calculations: dict
    coverage: list[dict]
    evidence_hash: str
    qualification: str

class BOMData(Strict):
    revision: Revision
    rows: list[dict]
    csv_path: str
    json_path: str
    sha256: str
    qualification: str


class PassiveRatingRequirement(Strict):
    component_id: Id
    max_applied_voltage_v: Annotated[float, Field(gt=0,le=1000,allow_inf_nan=False)]
    max_tolerance_fraction: Annotated[float, Field(gt=0,le=.5,allow_inf_nan=False)]
    min_resistor_rated_power_w: Annotated[float, Field(gt=0,le=100,allow_inf_nan=False)] | None = None
    allowed_dielectrics: Annotated[list[Literal['X7R','X5R','C0G','aluminum_oxide']],Field(max_length=4)] = []
    polarized_connection: Literal['positive_to_power_negative_to_ground'] | None = None


class PassiveRatings(Mutation):
    requirements: Annotated[list[PassiveRatingRequirement],Field(max_length=100)]
    origin: Literal['proposed_assumption','user_statement']
    reason: Annotated[str,Field(min_length=5,max_length=500)]


class BOMCheck(Strict):
    project_id: Id
    revision: Revision
    verdict: Literal['fail','incomplete']
    identity_verdict: Literal['pass','fail','incomplete']
    declared_rating_screen: Literal['conditional','fail','incomplete']
    counts: dict[str,int]
    rows: list[dict]
    findings: list[dict]
    evidence_hash: str
    physical_qualified: Literal[False] = False
    manufacturing_approved: Literal[False] = False
    qualification: str


class BOMDraft(Strict):
    project_id: Id
    revision: Revision
    json_path: str
    csv_path: str
    json_sha256: str
    csv_sha256: str
    report: BOMCheck
    qualification: str

class ReviewPrepare(PCBRef):
    purpose: Literal['prototype_files_only'] = 'prototype_files_only'
    qualification_declaration: Annotated[str,Field(min_length=20,max_length=1000)] | None = None
    validity_hours: Annotated[int,Field(ge=1,le=72)] = 24

class ReviewRef(Strict):
    project_id: Id
    review_id: Id

class ReviewData(Strict):
    project_id: Id
    revision: Revision
    review_id: Id
    status: Literal['pending_human_signature','approved','expired','authority_not_configured','rejected']
    payload_sha256: str
    review_path: str
    approval_command: list[str]
    expires_at: int
    findings: list[dict]

class FabricationData(Strict):
    review_id: Id
    revision: Revision
    bundle_path: str
    manifest: dict

class PowerEnvelope(Mutation):
    positive_pin: Pin
    ground_net_id: Id
    nominal_v: Annotated[float,Field(gt=0,le=12,allow_inf_nan=False)]
    min_v: Annotated[float,Field(gt=0,le=12,allow_inf_nan=False)]
    max_v: Annotated[float,Field(gt=0,le=12,allow_inf_nan=False)]
    available_current_ma: Annotated[float,Field(gt=0,le=3000,allow_inf_nan=False)]
    available_sink_current_ma: Annotated[float,Field(ge=0,le=3000,allow_inf_nan=False)] | None = None
    sink_capacity_basis: Annotated[str,Field(min_length=10,max_length=1000)] | None = None
    ambient_c: Annotated[float,Field(ge=-40,le=85,allow_inf_nan=False)] = 25.0
    ambient_range_c: Annotated[list[Annotated[float,Field(ge=-40,le=85,allow_inf_nan=False)]],Field(min_length=2,max_length=2)] | None = None
    origin: Literal['user_statement','proposed_assumption'] = 'proposed_assumption'
    reason: Annotated[str,Field(min_length=5,max_length=500)]

    @model_validator(mode='after')
    def explicit_sink_capacity(self):
        if self.ambient_range_c is not None and not self.ambient_range_c[0]<=self.ambient_c<=self.ambient_range_c[1]:
            raise ValueError('AMBIENT_RANGE_MUST_CONTAIN_REFERENCE_TEMPERATURE')
        if (self.available_sink_current_ma is None)!=(self.sink_capacity_basis is None):
            raise ValueError('SINK_CAPACITY_REQUIRES_EXPLICIT_BASIS')
        return self

class PowerIntegrityReport(Strict):
    revision: Revision
    verdict: Literal['pass','fail','incomplete']
    findings: list[dict]
    rails: dict
    calculations: list[dict]
    evidence_hash: str
    qualification: str


class DCSupplyLoad(Strict):
    pin: Pin
    max_current_ma: Annotated[float,Field(ge=0,le=10000,allow_inf_nan=False)]
    scope: Literal['supply_terminal_dc_including_io_drive'] = 'supply_terminal_dc_including_io_drive'


class DCStage(Strict):
    component_id: Id
    kind: Literal['buck','ldo','load_switch']
    input_overhead_ma: Annotated[float,Field(ge=0,le=1000,allow_inf_nan=False)]
    efficiency_lower_bound: Annotated[float,Field(gt=0,le=1,allow_inf_nan=False)] | None = None


class DCReserve(Strict):
    net_id: Id
    unmodeled_dc_reserve_ma: Annotated[float,Field(ge=0,le=10000,allow_inf_nan=False)]


class PowerBudget(Mutation):
    profile: Literal['esp32_usb_enabled_dc_v1','direct_source_dc_v1'] = 'esp32_usb_enabled_dc_v1'
    loads: Annotated[list[DCSupplyLoad],Field(max_length=100)]
    stages: Annotated[list[DCStage],Field(max_length=30)]
    reserves: Annotated[list[DCReserve],Field(max_length=100)]
    origin: Literal['proposed_assumption','user_statement']
    reason: Annotated[str,Field(min_length=5,max_length=500)]


class PowerBudgetReport(Strict):
    project_id: Id
    revision: Revision
    verdict: Literal['fail','incomplete']
    budget_verdict: Literal['conditional','fail','incomplete']
    coverage: dict
    findings: list[dict]
    rails: list[dict]
    loads: list[dict]
    stages: list[dict]
    passive_loads: list[dict]
    declared_dc_reference_input_a: float | None
    actual_input_current_upper_bound_a: None
    physical_qualified: Literal[False]
    manufacturing_approved: Literal[False]
    evidence_hash: str
    qualification: str

class RailSlewAssumption(Strict):
    net_id: Id
    max_slew_v_per_ms: Annotated[float,Field(gt=0,le=100000,allow_inf_nan=False,description='Declared maximum instantaneous positive voltage slope, not average rise-time or guaranteed device behavior.')]


class StartupCheck(Read):
    profile: Literal['esp32_local_cap_slew_reference_v1'] = 'esp32_local_cap_slew_reference_v1'
    slew_assumptions: Annotated[list[RailSlewAssumption],Field(max_length=100)] = []
    origin: Literal['proposed_assumption','user_statement'] | None = None
    reason: Annotated[str,Field(max_length=500)] = ''


class ResetScenario(Strict):
    supply_rise_ms: Annotated[float,Field(ge=0,le=100000,allow_inf_nan=False,description='Ideal linear rise from0V to the modeled final rail; zero means a step, not a measured real supply.')]
    supply_stable_at_ms: Annotated[float,Field(ge=0,le=100000,allow_inf_nan=False)]
    initial_en_v: Annotated[float,Field(ge=0,le=3.6,allow_inf_nan=False)]
    external_en_ports: Literal['assumed_disconnected'] | None = None


class ResetCheck(Read):
    profile: Literal['esp32_unloaded_en_rc_linear_ramp_v1'] = 'esp32_unloaded_en_rc_linear_ramp_v1'
    scenario: ResetScenario | None = None
    origin: Literal['proposed_assumption','user_statement'] | None = None
    reason: Annotated[str,Field(max_length=500)] = ''


class ResetReport(Strict):
    project_id: Id
    revision: Revision
    connectivity_basis: Literal['current_structured_spec_not_new_KiCad_netlist']
    verdict: Literal['fail','incomplete']
    reference_timing_verdict: Literal['conditional','fail','incomplete']
    topology: dict
    calculation: dict | None
    corners: list[dict]
    findings: list[dict]
    scenario: dict
    scenario_sha256: str
    evidence_hash: str
    sources: list[str]
    actual_reset_release_time_bounds_ms: None
    physical_qualified: Literal[False]
    manufacturing_approved: Literal[False]
    qualification: str


class StartupReport(Strict):
    project_id: Id
    revision: Revision
    connectivity_basis: Literal['current_structured_spec_not_new_KiCad_netlist']
    verdict: Literal['fail','incomplete']
    reference_slew_verdict: Literal['conditional','fail','incomplete']
    capacitors: list[dict]
    rails: list[dict]
    charge_transfer: dict
    findings: list[dict]
    coverage: dict
    scenario: dict
    scenario_sha256: str
    evidence_hash: str
    sources: list[str]
    actual_peak_input_current_upper_bound_a: None
    actual_startup_time_upper_bound_ms: None
    physical_qualified: Literal[False]
    manufacturing_approved: Literal[False]
    qualification: str


class USBPowerReport(Strict):
    revision: Revision
    verdict: Literal['fail','incomplete']
    findings: list[dict]
    ports: list[dict]
    source_declaration: dict | None
    minimum_input_design_current_lower_bound_a: float | None
    total_input_current_upper_bound_a: None
    sources: list[dict]
    evidence_hash: str
    manufacturing_approved: Literal[False] = False
    qualification: str

class USBGateReport(Strict):
    revision: Revision
    verdict: Literal['pass','fail']
    findings: list[dict]
    scenarios: list[dict]
    current_limit: dict | None
    startup_and_analog_behavior: Literal['not_qualified']
    live_cc_measurement: Literal[False]
    manufacturing_approved: Literal[False] = False
    sources: list[dict]
    evidence_hash: str
    qualification: str

class ReadinessReport(Strict):
    project_id: Id
    revision: Revision
    status: Literal['blocked','ready_for_human_review']
    gates: list[dict]
    blockers: list[dict]
    next_actions: list[dict]
    manufacturing_approved: Literal[False] = False
    ordered: Literal[False] = False
    qualification: str


class ProjectFork(Strict):
    project_id: Id
    expected_revision: Revision
    name: Short
    idempotency_key: IdempotencyKey


class ForkData(ProjectCreated):
    provenance: dict


class ReplacementTarget(Strict):
    component_id: Id
    candidate_id: Id
    footprint_candidate_id: Id


class ReplacementGroup(Strict):
    replacements: Annotated[list[ReplacementTarget], Field(min_length=2, max_length=16)]

    @model_validator(mode='after')
    def unique_targets(self):
        if len({target.component_id for target in self.replacements}) != len(self.replacements):
            raise ValueError('DUPLICATE_REPLACEMENT_COMPONENT')
        return self


class ReplacementPreview(ReplacementTarget):
    project_id: Id
    revision: Revision
    functional_run_id: Id | None = None


class ReplacementGroupPreview(ReplacementGroup):
    project_id: Id
    revision: Revision
    functional_run_id: Id | None = None


class ReplacementGroupApply(Mutation, ReplacementGroup):
    plan_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    functional_run_id: Id


class ReplacementApply(Mutation, ReplacementTarget):
    plan_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    functional_run_id: Id | None = None

    @model_serializer(mode='wrap')
    def keep_legacy_mutation_fingerprint(self, handler):
        result = handler(self)
        if self.functional_run_id is None:
            result.pop('functional_run_id', None)
        return result


class ReplacementProof(Strict):
    project_id: Id
    revision: Revision
    original_spec_sha256: str
    proposed_spec_sha256: str
    engineering_evidence_hash: str
    policy_sha256: str
    connectivity_preserved: Literal[True]
    specification: dict
    compatible: bool
    requires_rebuild_and_new_approval: Literal[True]
    qualification: str
    plan_sha256: str


class ReplacementChange(Strict):
    component_id: Id
    reference: str
    before: dict
    after: dict


class ReplacementPlan(ReplacementProof, ReplacementChange):
    pass


class ReplacementGroupPlan(ReplacementProof):
    replacements: list[ReplacementChange]


class ReplacementData(EditedData):
    plan_sha256: str
    value: str
    mpn: str
    connectivity_preserved: Literal[True]
    approvals_invalidated: Literal[True]


class ReplacementGroupData(Strict):
    project_id: Id
    revision: Revision
    plan_sha256: str
    replacements: list[dict]
    connectivity_preserved: Literal[True]
    approvals_invalidated: Literal[True]


class AuditRequest(Read):
    schematic_build_id: Id | None = None
    pcb_build_id: Id | None = None


class AuditData(Strict):
    project_id: Id
    revision: Revision
    audit_id: Id
    created_at: str
    spec_sha256: str
    schematic_build_id: Id | None
    pcb_build_id: Id | None
    stages: list[dict]
    artifact_evidence: list[dict]
    readiness: dict
    status: Literal['blocked', 'ready_for_human_review']
    manufacturing_approved: Literal[False]
    gerber_generated: Literal[False]
    ordered: Literal[False]
    qualification: str
    json_path: str
    report_path: str
    sha256: str


class ModuleReport(Strict):
    revision: Revision
    verdict: Literal['pass','fail','incomplete']
    modules: list[dict]
    findings: list[dict]
    sources: list[dict]
    evidence_hash: str
    whole_board_power_verdict: Literal['pass','fail','incomplete']
    coverage: list[dict]
    qualification: Literal['restricted_static_port_contract_not_functional_or_manufacturing_approval']
    manufacturing_approved: Literal[False]
