import functools
import json
import os
from typing import get_type_hints
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from .models import (Create,Search,Candidate,Read,Add,Parameter,NetCreate,Members,NetPinMoves,CircuitRemove,Build,
                     BuildRef,Preview,Export,Artifact,Reply,Capabilities,ProjectCreated,
                     ProjectState,CandidateData,SearchData,AddedData,ParameterData,
                     NetData,MembersData,SpecReport,BuiltData,VerifyReport,ERCReport,
                     PreviewData,ExportData,ArtifactData,RemoveComponent,RemoveNet,Intent,
                     Requirements,ConstraintSet,ConstraintRemove,ProjectList,EditedData,RequirementsData,
                     PowerSource,PowerRemove,RuleRequest,RulesData,RuleData,ElectricalReport)
from .models import (PartSearch,PartData,PartResults,PartBind,PartUnbind,OperatingConditions,EngineeringReport,BOMData,ReviewPrepare,ReviewRef,ReviewData,FabricationData)
from .models import PowerEnvelope,PowerIntegrityReport,ReadinessReport,PowerBudget,PowerBudgetReport,StartupCheck,StartupReport,ResetCheck,ResetReport
from .models import PassiveRatings,BOMCheck,BOMDraft
from .service import Service,DomainError
from .models import (FootprintSearch,FootprintData,FootprintResults,FootprintBind,FootprintUnbind,BoardConstraints,PCBPreflight,PCBCreate,PCBRef,PCBStage,PCBRoute,PCBBuilt,PCBCheck,PCBPreview,PCBExport)

from .config import data_dir
from .catalog_import import CatalogPackage
from .design_contract import Proposal
from .functional import FunctionalCheck
from .verification_models import VerificationModel
from .design_jobs import DesignJobCreate,DesignJobRef,DesignJobAttempt,DesignJobSearch
service=Service(data_dir())
mcp=FastMCP('PCB Design MCP',instructions='Experimental source-bound schematic and two/four-layer PCB tools. Query and resolve real symbols, footprints and curated MPN candidates; never invent parts or ratings. Account for every pin, power and constraints. Bind parts only to their specified footprints. Declare conditions; identity/nominal specification checks are automatic before prototype builds; the existing narrow LED stress gate remains mandatory for LED profiles. Use bom_check, explicit passive_ratings_set, power_budget_set/power_budget_check and power_startup_check/reset_timing_check; capacitor slew scenarios are read-only assumptions and never prove whole-board startup; declared DC consistency never proves actual total current or manufacturing qualification; incomplete draft exports never imply production qualification. Build/independently verify schematic/ERC and actual PCB; route/verify/DRC. Prepare immutable BOM/engineering/PCB review. Gerber requires a separate human P256 receipt from the local system-authenticated reviewer. No tool can approve, install an authority, or bypass signature/revision/hash/expiry checks. Never operate the human approval interface on behalf of the user. Use design_job_create and design_job_attempt for a durable end-to-end delivery. For a change to a delivered job, pass its job ID and attempt as base_job to design_job_create, then submit a revision plan. Use project_fork before low-level changes to other working designs. Use component_replacement_preview then component_part_replace for compatible source-linked edits; do not change identity by display value alone. Use design_audit to re-run actual checks, and design_readiness before offering fabrication: geometry pass does not mean power or real-part readiness. Declare supply envelopes only from explicit external conditions; never invent ratings or waive unknowns. No orders or production qualification.')


def tool(description):
    def decorate(fn):
        result_type=get_type_hints(fn)['return']
        @functools.wraps(fn)
        def guarded(*args,**kwargs):
            try:
                data=fn(*args,**kwargs)
                return result_type(project_id=data.get('project_id'),revision=data.get('revision'),data=data)
            except DomainError as e:
                raise ToolError(json.dumps(e.payload,ensure_ascii=False)) from e
            except Exception as e:
                raise ToolError(json.dumps(dict(code='BACKEND_FAILURE',message=str(e),retryable=False),ensure_ascii=False)) from e
        mcp.tool(description=description)(guarded)
        return guarded
    return decorate


@tool('Report real backend versions and explicitly unsupported capabilities.')
def system_capabilities() -> Reply[Capabilities]:
    return service.capabilities()

@tool('Create a generic schematic experiment or legacy LED project. An optional idempotency key returns the original creation result on retry. Neither profile is manufacturing approved.')
def project_create(request: Create) -> Reply[ProjectCreated]:
    return service.create(request)

@tool('Read the explicit current revision; stale revisions are rejected.')
def project_read(request: Read) -> Reply[ProjectState]:
    return service.read(request)

@tool('Search configured KiCad libraries; return actual symbols, pins and support limits.')
def library_search(request: Search) -> Reply[SearchData]:
    return service.search(request)

@tool('Resolve a candidate to actual pin IDs and a definition hash before selection.')
def library_get(request: Candidate) -> Reply[CandidateData]:
    return service.get_candidate(request)

@tool('Add one resolved real-library symbol with a checked experiment value.')
def component_add(request: Add) -> Reply[AddedData]:
    return service.add(request)

@tool('Change a component experiment value; increments revision and invalidates old builds.')
def component_parameters_set(request: Parameter) -> Reply[ParameterData]:
    return service.parameters(request)

@tool('Create a uniquely named typed net. At most 100 in generic profile, three in legacy LED profile.')
def net_create(request: NetCreate) -> Reply[NetData]:
    return service.create_net(request)

@tool('Atomically replace all members of one net. Unknown and multiply connected pins fail.')
def net_members_set(request: Members) -> Reply[MembersData]:
    return service.members(request)


@tool('Atomically move existing connected pins between existing nets. Each move names its current and destination net IDs. All source connections are checked against the same revision; existing power boundaries must remain valid.')
def net_pins_move(request: NetPinMoves) -> Reply[EditedData]:
    return service.move_net_pins(request)


@tool('Atomically remove the specified components, their bindings, and nets emptied by that removal. Retain other wiring and board rules. Reject dangling nets, linked design constraints and broken power boundaries.')
def circuit_remove(request: CircuitRemove) -> Reply[EditedData]:
    return service.remove_circuit(request)

@tool('Check library freshness, pin accounting, stacked pins, declared connectivity and external power boundaries. Automatically apply supported nominal electrical rule packs; fail before building on errors.')
def spec_validate(request: Read) -> Reply[SpecReport]:
    return service.validate(request)

@tool('Generate a real SKiDL netlist, validate it, and use the existing deterministic managed-symbol schematic adapter or legacy LED loop. No discarded automatic-layout intermediate, model coordinates or executable code accepted.')
def schematic_build(request: Build) -> Reply[BuiltData]:
    return service.build(request)

@tool('Independently export actual KiCad netlist and compare all members, values and symbols to the spec.')
def schematic_verify(request: BuildRef) -> Reply[VerifyReport]:
    return service.verify(request)

@tool('Run actual KiCad ERC; return violations and disabled checks without suppressing errors.')
def erc_run(request: BuildRef) -> Reply[ERCReport]:
    return service.erc(request)

@tool('Export actual KiCad SVG preview. Automatic text collision checking is explicitly unsupported.')
def schematic_preview(request: Preview) -> Reply[PreviewData]:
    return service.preview(request)

@tool('Export a review ZIP only after current netlist verification, ERC and preview. Never Gerber.')
def schematic_export(request: Export) -> Reply[ExportData]:
    return service.export(request)

@tool('Read a generated artifact by controlled ID; arbitrary paths are not accepted.')
def artifact_read(request: Artifact) -> Reply[ArtifactData]:
    return service.artifact(request)


@tool('List managed projects with their authoritative current revisions. Does not read arbitrary directories.')
def project_list() -> Reply[ProjectList]:
    return service.list_projects()


@tool('Replace the recorded requirement list. Proposed assumptions remain explicit; free text is not independently verified or human-approved.')
def requirements_set(request: Requirements) -> Reply[RequirementsData]:
    return service.requirements(request)


@tool('Add or update a typed must_connect or pairwise must_separate constraint over real pins. No arbitrary expressions.')
def constraint_set(request: ConstraintSet) -> Reply[EditedData]:
    return service.constraint(request)


@tool('Remove one explicit constraint; increments revision and invalidates all old builds.')
def constraint_remove(request: ConstraintRemove) -> Reply[EditedData]:
    return service.remove_constraint(request)


@tool('Mark a real unconnected pin no_connect with a required reason, or clear that intent. Never suppress a connected pin error.')
def pin_intent_set(request: Intent) -> Reply[EditedData]:
    return service.intent(request)


@tool('Remove a component only after its nets, constraints and pin-intent references have been explicitly removed.')
def component_remove(request: RemoveComponent) -> Reply[EditedData]:
    return service.remove_component(request)


@tool('Remove an empty net. Connected nets must be explicitly cleared first.')
def net_remove(request: RemoveNet) -> Reply[EditedData]:
    return service.remove_net(request)


@tool('List versioned experimental electrical rule packs, exact symbol scope, evidence links, hashes and unsupported checks. No human approval is implied.')
def rules_list() -> Reply[RulesData]:
    return service.rules_list()


@tool('Read the source evidence, policy and coverage of one experimental electrical rule pack.')
def rules_get(request: RuleRequest) -> Reply[RuleData]:
    return service.rules_get(request)


@tool('Declare nominal external power at a real connected connector or battery pin and its reference ground. This creates audited ERC flags, not measured electrical evidence.')
def power_source_set(request: PowerSource) -> Reply[EditedData]:
    return service.power_source(request)


@tool('Remove one external power declaration and invalidate all old builds through the revision counter.')
def power_source_remove(request: PowerRemove) -> Reply[EditedData]:
    return service.remove_source(request)


@tool('Check supported source-linked nominal voltage and topology rules. Reports tolerances, USB current and thermal budgets as unverified; cannot certify functional or manufacturing safety.')
def electrical_check(request: Read) -> Reply[ElectricalReport]:
    return service.electrical_check(request)


@tool('Search real local KiCad footprint geometry by name. No procurement or manufacturer package-fit claim.')
def footprint_search(request: FootprintSearch) -> Reply[FootprintResults]:
    return service.footprint_search(request)


@tool('Resolve a searched footprint to actual electrical pad numbers, geometry, source and hash before binding.')
def footprint_get(request: Candidate) -> Reply[FootprintData]:
    return service.footprint_get(request)


@tool('Bind resolved footprint geometry to a component only when all physical pin/pad numbers match and real symbol filters allow it. Geometry experiment only; never claims verified MPN fit.')
def footprint_bind(request: FootprintBind) -> Reply[EditedData]:
    return service.footprint_bind(request)


@tool('Remove a footprint binding explicitly, increment revision and invalidate old schematic/PCB results.')
def footprint_unbind(request: FootprintUnbind) -> Reply[EditedData]:
    return service.footprint_unbind(request)


@tool('Set bounded two/four-layer geometry, widths and clearances. Four layers require an explicit copper/dielectric stackup. Optionally provide complete placements, ground-plane intent, keepouts, typed local routes/vias, and top silkscreen interface labels. Label coordinates are board-relative millimeters. Native stackup, geometry, net verification and DRC remain required; no executable input.')
def board_constraints_set(request: BoardConstraints) -> Reply[EditedData]:
    return service.board_constraints_set(request)


@tool('Check current schematic spec, all real footprint mappings and declared physical constraints. Always reports manufacturer/package/functional approval as unverified.')
def pcb_preflight(request: Read) -> Reply[PCBPreflight]:
    return service.pcb_preflight(request)


@tool('Build an unrouted KiCad PCB and DSN after current schematic/ERC and physical preflight. A placement-only fork may use its verified parent as a routing seed. For a verification-only fork, reuse_parent_pcb_build_id copies the verified parent PCB and board rules after checking the unchanged design. Run fresh PCB checks and export on the new build.')
def pcb_build(request: PCBCreate) -> Reply[PCBBuilt]:
    return service.pcb_build(request)


@tool('Independently reopen actual unrouted/routed PCB and compare every component, value, footprint, electrical pad net and outline against the spec.')
def pcb_verify(request: PCBStage) -> Reply[PCBCheck]:
    return service.pcb_verify(request)


@tool('Route verified PCB with pinned Freerouting; verify output and run DRC separately.')
def pcb_autoroute(request: PCBRoute) -> Reply[PCBBuilt]:
    return service.pcb_autoroute(request)


@tool('Run actual KiCad DRC including unconnected tracks; violations and backend failure never count as pass. Functional/manufacturing correctness is outside coverage.')
def drc_run(request: PCBRef) -> Reply[PCBCheck]:
    return service.pcb_drc(request)


@tool('Export actual routed PCB copper, silkscreen and outline SVG for visual review.')
def pcb_preview(request: PCBRef) -> Reply[PCBPreview]:
    return service.pcb_preview(request)


@tool('Export a PCB review bundle after matching actual pads, zero current DRC and preview. Fully source-bound projects include BOM, native positions and assembly process tables. Keeps unresolved evidence. No Gerber, ordering or manufacturing approval.')
def pcb_review_export(request: PCBRef) -> Reply[PCBExport]:
    return service.pcb_review_export(request)


@tool('Search curated source-linked manufacturer MPN records and package constraints.')
def parts_search(request: PartSearch) -> Reply[PartResults]:
    return service.parts_search(request)


@tool('List actual installed and imported symbol/footprint libraries and immutable catalog packages. Discover names before querying a new family; inventory is not electrical qualification.')
def library_inventory() -> Reply[dict]:
    return service.library_inventory()


@tool('Import an immutable source-linked catalog package containing document snapshots and optional namespaced KiCad libraries/model data. Verify asset hashes, evidence locations, actual symbol/footprint hashes and full physical pin mapping. Existing MPNs cannot be overwritten. No model or code execution; imported source interpretations still require functional validation.')
def catalog_import(request: CatalogPackage) -> Reply[dict]:
    return service.catalog_import(request)


@tool('Compose source-selected circuit modules using explicit ports and connections. Check declared voltage/current/reference compatibility, allocate unique references, preserve separate nets and emit a standard data plan and frozen requirement JSON. Resolve real part hashes and account for all pins. This checks interface consistency, not module function or simulation; no build or project mutation.')
def design_compose(request: Proposal) -> Reply[dict]:
    return service.design_compose(request)

@tool('Import a source-linked immutable SPICE or digital truth model for an exact part definition. For SPICE text, name must equal the actual .SUBCKT or .MODEL entry in the unchanged asset, not a friendly label. Preserve vendor model data and resolve the physical pin order. Native/PSpice text uses ngspice including built-in T delay lines; explicit LTspice text or hash-bound installed vendor binary models use the external official Mac runtime. Validate source assets and all physical pins. No caller executable code or simulator control commands.')
def verification_model_import(request: VerificationModel) -> Reply[dict]:
    return service.verification_model_import(request)

@tool('Execute a frozen functional contract against exact project nets and bound part/model sources. An optional resolved replacement target tests its proposed specification without applying the edit. Supports linear DC, SPICE and source-linked digital truth analysis. Preserve solver artifacts and failed requirements. No physical qualification.')
def functional_check(request: FunctionalCheck) -> Reply[dict]:
    return service.functional_check(request)

@tool('Create a durable design job with immutable requirements. For a first design supply the functional contract and formal_requirements in requirement_json. Each scalar check must match its allowed interval. Conditions can bind observation_window or measurement_band. Default CAD and delivery checks apply when software_acceptance is absent. attempt_timeout_s sets the per-execution deadline from 30 to 600 seconds, default 300. To revise a delivery pass base_job and the natural-language change. For a named functional limit, include functional_limit_changes with id, previous and revised intervals (minimum, maximum, unit). For linked project text, include project_requirement with key, previous and revised. To change I2C pull-up resistance, include i2c_pullup_changes with the inherited check id, previous and revised objects with sda_ohm and scl_ohm, and optional project_requirement. Use component_revision and replace exactly the pull-up resistors whose values change. Update linked contract wording with contract_description_change containing previous and revised text. Add functional_additions as new checks using inherited scenarios or functional_scenario_additions. Each added scenario uses the same schema as contract.scenarios, has a new ID, and must be measured by an added check. For a stackup revision supply stackup_change with previous and revised objects containing layers, thickness_mm and stackup. For a rectangular board resize supply outline_change with previous and revised width_mm and height_mm. Include both outline_change and stackup_change to revise them together. For mounting holes use mounting_hole_change with previous and revised ordered hole lists of x_mm, y_mm, diameter_mm and keepout_mm. It can accompany outline or stackup changes. For hole additions or removals, unchanged retained holes are matched by geometry and order. When changing the count and moving retained holes, use zero-based removed_indices into previous and added_indices into revised to bind the retained order. Hole-specific checks follow retained holes; only checks for removed holes are retired. In a mechanical, mounting, stackup, or outline revision, record user-authorized placements in placement_requirements, a list with ref and any specified x_mm, y_mm and rotation_deg. Only listed components may move. If any axis is specified, omitted axes stay at the delivered coordinates. A row with only ref permits a free pose for that component. Use allow_placement_changes for unrestricted placement on resize only when the user permits it. To move existing connections, include connectivity_changes with the user-confirmed list of pin (ref and number), from_net, and to_net. To remove a subcircuit, include circuit_removal with components and retire_checks. Other functional checks and fixtures are inherited.')
def design_job_create(request: DesignJobCreate) -> Reply[dict]:
    return service.design_job_create(request)

@tool('Find existing design jobs in this workspace using short words from the job title or original requirement, or a candidate project ID. Empty query lists recent jobs. Results contain one reference per job, including failed and unstarted jobs. Use design_job_read on the selected job_id to verify delivery and get editable files before revising it.')
def design_job_search(request: DesignJobSearch) -> Reply[dict]:
    return service.design_job_search(request)


@tool('Read user_result for the verified delivery, review-only KiCad files from failed attempts, and functional coverage for each version. A failed later candidate preserves the completed job status when an earlier delivery exists.')
def design_job_read(request: DesignJobRef) -> Reply[dict]:
    return service.design_job_read(request)

@tool('Execute one source-selected candidate and require a complete verified delivery for success. A revision forks its delivered base and rechecks acceptance. Use functional_revision for verification-only changes, component_revision for replacement, circuit_extension to add parts, connectivity_revision to move pins, circuit_removal to remove parts, mounting_revision for mounting hole changes, with authorized placement changes when needed, stackup_revision for board construction changes with optional authorized placement changes, or outline_revision when the outline changes, including when the construction changes in the same job. For mechanical, mounting, stackup, and outline revisions, candidate placements must satisfy placement_requirements when provided. Mounting and stackup revisions may move only explicitly authorized components. Outline revision otherwise keeps parent placements unless the frozen request permits a free layout. Connectivity moves must exactly match the frozen connectivity_changes for new design jobs. Use replacement for one part or replacements for an atomic group under component_revision. Set model_id for each modeled replacement to the imported model of the new part. A group must pass the inherited functional contract before application. For circuit_extension, use model_bindings for new references. If interrupted, resubmit the identical candidate and idempotency key. The runner verifies keyed initial creation and can register an already published, unchanged revision branch. Execution is bounded by the stored attempt_timeout_s. A terminal attempt with the same key returns its retained result.')
def design_job_attempt(request: DesignJobAttempt) -> Reply[dict]:
    return service.design_job_attempt(request)

@tool('Resolve the exact part candidate, source evidence, pin meaning and required footprint before selection.')
def part_get(request: Candidate) -> Reply[PartData]:
    return service.part_get(request)

@tool('Bind a resolved physical-part candidate only to a matching symbol/value and specified source-linked footprint. Never accepts an invented MPN or rating.')
def part_bind(request: PartBind) -> Reply[EditedData]:
    return service.part_bind(request)

@tool('Explicitly remove a physical-part binding and invalidate old validation/review revisions.')
def part_unbind(request: PartUnbind) -> Reply[EditedData]:
    return service.part_unbind(request)

@tool('Declare finite supply bounds, available current and ambient temperature with source/reason. Declarations are not physical measurements. Restricted series LED DC profile only.')
def operating_conditions_set(request: OperatingConditions) -> Reply[EditedData]:
    return service.conditions_set(request)

@tool('Check fresh part evidence, exact topology/polarity and conservative resistor/current/LED DC power bounds at 25C. Unknown assembly, brightness and thermal/transient evidence remains visible.')
def engineering_check(request: Read) -> Reply[EngineeringReport]:
    return service.engineering_check(request)

@tool('Export a source-linked BOM CSV/JSON from the current resolved part bindings; includes real catalog SKU/URLs without live stock/price claims.')
def bom_export(request: Read) -> Reply[BOMData]:
    return service.bom_export(request)


@tool('Audit every BOM identity against fresh manufacturer evidence, value, symbol and footprint. Screen resistor/capacitor ratings only against explicit declared requirements. Missing and unknown remain incomplete; never production qualification.')
def bom_check(request: Read) -> Reply[BOMCheck]:
    return service.bom_check(request)


@tool('Atomically replace or clear typed passive voltage/tolerance/power/dielectric requirements. Polarized capacitors require an explicit power/ground polarity rule. Assumptions or user statements only; no inferred measurements, approval flag, coordinates or executable rules.')
def passive_ratings_set(request: PassiveRatings) -> Reply[EditedData]:
    return service.passive_ratings_set(request)


@tool('Export an explicitly incomplete BOM draft CSV/JSON with one row per component, missing identities and unqualified ratings. Does not replace strict bom_export or authorize procurement/manufacturing.')
def bom_draft_export(request: Read) -> Reply[BOMDraft]:
    return service.bom_draft_export(request)

@tool('Prepare a hash-bound expiring human review of exact PCB, BOM, engineering assumptions, reports and warnings after real verification/ERC/DRC. Does not approve or export Gerber.')
def manufacturing_review_prepare(request: ReviewPrepare) -> Reply[ReviewData]:
    return service.review_prepare(request)

@tool('Read current review validity and independently verify any human signature. Invalid/tampered/foreign receipts fail closed. Cannot create approval.')
def manufacturing_review_status(request: ReviewRef) -> Reply[ReviewData]:
    return service.review_status(request)

@tool('Export Gerber/drill files only with a valid independent human P256 approval for these immutable review bytes, current revision and unexpired action. No approval flag, authority/key upload, forced export or ordering.')
def fabrication_export(request: ReviewRef) -> Reply[FabricationData]:
    return service.fabrication_export(request)


@mcp.resource('pcb://{project_id}/{build_id}/{artifact_id}')
def artifact_resource(project_id: str,build_id: str,artifact_id: str) -> str:
    request=Artifact(project_id=project_id,build_id=build_id,artifact_id=artifact_id)
    return service.artifact(request)['content']


@mcp.resource('pcb-board://{project_id}/{pcb_build_id}/preview',mime_type='image/svg+xml')
def pcb_preview_resource(project_id: str,pcb_build_id: str) -> str:
    return service.pcb_preview_content(PCBRef(project_id=project_id,pcb_build_id=pcb_build_id))


def main():
    mcp.run(transport='stdio')




@tool('Declare the real board-side supply range/current/environment at an existing external boundary. It is not measurement or a waiver. Once present, power-integrity errors automatically block schematic and PCB builds.')
def power_envelope_set(request: PowerEnvelope) -> Reply[EditedData]:
    return service.envelope_set(request)

@tool('Source-family voltage-envelope, LDO capacity and reference thermal diagnostics. Unknown load/cap/USB/thermal evidence is explicit and cannot pass manufacturing readiness. No model ratings accepted.')
def power_integrity_check(request: Read) -> Reply[PowerIntegrityReport]:
    return service.power_check(request)

@tool('Inspect actual KiCad geometry: antenna keepout, intrusion, bypass distances and declared pin-to-pin copper path length/via limits. Path lengths conservatively count whole contacted tracks; no RF/thermal qualification.')
def pcb_layout_check(request: PCBStage) -> Reply[PCBCheck]:
    return service.layout_check(request)

@tool('Consolidate current source/spec, real part, engineering, power, PCB and layout evidence into blockers and next tools. It never creates approval or considers unknown checks passed.')
def design_readiness(request: Read) -> Reply[ReadinessReport]:
    return service.readiness(request)

from .models import (ProjectFork, ForkData, ReplacementPreview, ReplacementApply, ReplacementPlan, ReplacementData,
                     ReplacementGroupPreview, ReplacementGroupApply, ReplacementGroupPlan, ReplacementGroupData,
                     AuditRequest, AuditData)


@tool('Copy an exact specification revision into an independent editable project. Preserve the original; copy no builds, approval receipts or manufacturing files. Idempotent; fork starts at revision zero with hash-bound parent provenance.')
def project_fork(request: ProjectFork) -> Reply[ForkData]:
    return service.project_fork(request)


@tool('Preview one same-symbol source-linked replacement or a replacements list of distinct components. Supply functional_run_id to use a passing functional_check of the exact proposed design. Returns a plan hash bound to the revision and evidence. No project mutation or manufacturing approval.')
def component_replacement_preview(request: ReplacementPreview | ReplacementGroupPreview) -> Reply[ReplacementPlan | ReplacementGroupPlan]:
    return service.replacement_preview(request)


@tool('Apply a matching compatible replacement plan. A group requires a passing functional_run_id for the exact full set and commits in one revision. Preserve pin nets and invalidate old build and approval revisions. Unknown candidates, changed plans or specification errors leave the project unchanged.')
def component_part_replace(request: ReplacementApply | ReplacementGroupApply) -> Reply[ReplacementData | ReplacementGroupData]:
    return service.replacement_apply(request)


@tool('Re-run current specification/electrical/engineering and actual KiCad netlist/ERC/PCB/DRC checks for an existing build chain, plus ESP32 layout checks when applicable. Write a human-readable snapshot report. Ambiguous targets require explicit IDs; no builds, routing, signing, Gerber or orders. Any missing/failing check blocks readiness.')
def design_audit(request: AuditRequest) -> Reply[AuditData]:
    return service.design_audit(request)


from .models import ModuleReport


@tool('Check sensor signal roles, supply ranges and static logic margins from sources.')
def module_constraints_check(request: Read) -> Reply[ModuleReport]:
    return service.module_check(request)


from .connectivity import TraceRequest,TraceResult


@tool('Re-export and verify an existing current schematic netlist, then trace one resolved logical pin to its exact shared-net physical pins. Supports grouped pins, verified no-connect and pagination. No arbitrary paths, inferred conduction through parts, design-spec edits or approval.')
def schematic_pin_connections(request: TraceRequest) -> Reply[TraceResult]:
    return service.pin_connections(request)


from .models import USBPowerReport

@tool('Diagnose the current USB-C sink specification: independent Rd, unknown CC current, attach/load policy, direct VBUS capacitors and declared supply assumptions. Read-only; no live CC measurement, no source-current override, no USB compliance or manufacturing approval.')
def usb_c_power_check(request: Read) -> Reply[USBPowerReport]:
    return service.usb_power_check(request)


from .models import USBGateReport

@tool('Verify the fixed TUSB320LAI GPIO / Schmitt inverter / active-high TPS2553 interlock topology and return the four manufacturer steady-state current modes. Conditional truth table only; not live CC measurements, startup simulation, analog margin proof, USB compliance or manufacturing approval.')
def usb_c_gate_scenarios(request: Read) -> Reply[USBGateReport]:
    return service.usb_gate_check(request)





from .models import CopperRules,CopperReport

@tool('Atomically replace typed per-power/ground-net widths and declared copper/current reference budgets. Writes actual DSN classes and KiCad custom minimum rules on rebuild. No routing coordinates, measurements, approval or material qualification accepted.')
def pcb_net_rules_set(request: CopperRules) -> Reply[EditedData]:
    return service.copper_rules_set(request)

@tool('Read actual hashed routed KiCad copper geometry. Check per-net widths and conditional external-layer current / 20C track-resistance inventory. Pads, barrels, physical copper, temperature and complete current paths remain unqualified; never manufacturing approval.')
def pcb_copper_check(request: PCBRef) -> Reply[CopperReport]:
    return service.copper_check(request)


from .models import PCBRetry

@tool('Prepare a new immutable PCB routing attempt from a failed real DRC connectivity report. Preserve existing routes as engine input; do not overwrite parent artifacts or inherit checks/approvals. Only unconnected items and dangling vias are supported. Reverify unrouted pads/layout, autoroute, and run fresh post-route checks.')
def pcb_route_retry_prepare(request: PCBRetry) -> Reply[PCBBuilt]:
    return service.pcb_retry_prepare(request)


@tool('Declare enabled-state DC current assumptions per real supply pin, stage efficiency/overhead and power-net reserves. Supported ESP32 USB reference only. Assumptions are never measurements or approval. Atomic revision/idempotency checks; empty lists clear the declaration.')
def power_budget_set(request: PowerBudget) -> Reply[EditedData]:
    return service.power_budget_set(request)

@tool('Calculate declared enabled-state DC budget through real connected stages, selected source-linked ratings and resistor tolerance. Missing domains fail closed; contradictions return fail. A conditional budget always remains physically incomplete and cannot approve fabrication.')
def power_budget_check(request: Read) -> Reply[PowerBudgetReport]:
    return service.power_budget_check(request)

@tool('Check local and upstream capacitor charge against declared power budgets.')
def power_startup_check(request: StartupCheck) -> Reply[StartupReport]:
    return service.startup_check(request)

@tool('Read-only ESP32-WROOM-32E EN reset RC topology and initial-tolerance timing reference. Query current source-linked pullup/capacitor; inspect external header loads. Only explicitly declared ideal linear supply ramps and initial EN voltage are modeled. Conservatively keep EN below 0.6V through supply stability+50us; reaching 75 percent is not the earliest reset-release bound. Missing waveforms or external-load assumptions stay incomplete. Never produces physical qualification or approval.')
def reset_timing_check(request: ResetCheck) -> Reply[ResetReport]:
    return service.reset_check(request)

if __name__=='__main__':
    main()
