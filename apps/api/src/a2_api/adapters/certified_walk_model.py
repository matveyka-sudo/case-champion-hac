"""Offline native verification for an explicitly approved canonical access model."""

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Identifier
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.journeys import JourneyResult, Leg, LineGeometry
from a2_contracts.v2.map_data import AnalysisMapData, MapFeature, PointGeometry
from a2_contracts.v2.requests import AnalysisRequest, UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult, EvidenceRef, PopulationBasis, SubjectRef
from a2_engine import load_urban_config
from a2_engine.urban.walk import geometry as geometry_engine
from a2_engine.urban.walk import physical_certificate, source_bound, turn_certificate
from a2_engine.urban.walk.canonical_point import CanonicalWalkPointResult
from a2_engine.urban.walk.geometry import CertifiedWalkGeometry
from a2_engine.urban.walk.physical_certificate import (
    DesignatedAccessReceipt,
    DesignatedAccessResult,
)
from pydantic import Field

from a2_api.adapters.product_projection import Projection, known
from a2_api.adapters.product_runtime import MAX_DOCUMENT_BYTES, FileRef, unavailable
from a2_api.core.config import RuntimeSettings
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry

MAX_METADATA_BYTES = 1024 * 1024
MAX_NATIVE_FILE_BYTES = 96 * 1024 * 1024
MAX_NATIVE_TOTAL_BYTES = 128 * 1024 * 1024
MAX_GEOMETRY_TOTAL_BYTES = 16 * 1024 * 1024


class GeometrySupplement(ContractModel):
    schema_version: Literal["waydex-certified-walk-geometry-binding/1"]
    receipt: FileRef
    original_snapshot: FileRef


class ProofBinding(ContractModel):
    schema_version: Literal["waydex-certified-walk-model/1"]
    snapshot_id: Identifier
    snapshot_hash: str = Field(pattern="^[a-f0-9]{64}$")
    city_id: Identifier
    point_id: Identifier
    destination_id: Identifier
    service_date: date
    native_receipt: FileRef
    physical_access: FileRef
    compatibility: FileRef
    policy: FileRef
    official_sources: FileRef
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=64)
    geometry: FileRef | None = Field(default=None, exclude_if=lambda value: value is None)


@dataclass(frozen=True)
class GeometryMetadata:
    supplement: GeometrySupplement
    root: Path
    receipt: dict[str, Any]


@dataclass(frozen=True)
class CertifiedRuntime:
    binding: ProofBinding
    digest: str
    native_root: Path
    native: dict[str, Any]
    physical_json: str
    compatibility_json: str
    policy_json: str
    official_json: str
    graph_fingerprint: str
    profile_fingerprint: str
    geometry: GeometryMetadata | None = None


def _text(path: Path, expected: str, limit: int = MAX_METADATA_BYTES) -> str:
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("CERTIFIED_INPUT_HASH_OR_BYTE_BUDGET_MISMATCH")
    return raw.decode("utf-8")


def _reference(root: Path, ref: FileRef) -> tuple[Path, str]:
    # These paths are operator-owned, pinned startup configuration. They never
    # come from a request and may reference a separately mounted proof archive.
    path = (root / ref.path).resolve()
    return path, _text(path, ref.sha256)


def _file_digest(path: Path, budget: list[int]) -> str:
    size = path.stat().st_size
    if size > MAX_NATIVE_FILE_BYTES or size > budget[0]:
        raise ValueError("CERTIFIED_NATIVE_BYTE_BUDGET_EXCEEDED")
    digest = hashlib.sha256()
    read = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            read += len(chunk)
            if read > size:
                raise ValueError("CERTIFIED_NATIVE_FILE_CHANGED_DURING_READ")
            digest.update(chunk)
    if read != size:
        raise ValueError("CERTIFIED_NATIVE_FILE_CHANGED_DURING_READ")
    budget[0] -= read
    return digest.hexdigest()


def _geometry_metadata(
    root: Path, reference: FileRef, binding: ProofBinding, native: dict[str, Any]
) -> GeometryMetadata:
    supplement_path, content = _reference(root, reference)
    supplement = GeometrySupplement.model_validate_json(content)
    receipt_path, receipt_json = _reference(supplement_path.parent, supplement.receipt)
    receipt = json.loads(receipt_json)
    files = receipt["files_sha256"]
    if (
        receipt["schema"] != "waydex-certified-walk-geometry/1"
        or receipt["geometry_only"] is not True
        or receipt["promotes_minimum_or_coverage"] is not False
        or len(files) != 32
        or receipt["source_files_sha256"] != native["files_sha256"]
        or receipt["native_receipt_sha256"] != binding.native_receipt.sha256
        or receipt["physical_receipt_sha256"] != binding.physical_access.sha256
        or receipt["source_scope_id"] != native["closed_turn_certificate"]["source_scope_id"]
        or receipt["source_build_manifest_sha256"] != native["files_sha256"]["build-manifest.json"]
        or receipt["candidate_sha256"] != native["files_sha256"]["turn-candidate.json"]
        or files["native-receipt.json"] != binding.native_receipt.sha256
        or files["physical-receipt.json"] != binding.physical_access.sha256
        or files["proof-binding.json"] != receipt["proof_binding_sha256"]
        or files["model-receipt.json"] != receipt["model_receipt_sha256"]
    ):
        raise ValueError("CERTIFIED_GEOMETRY_SOURCE_BINDING_CHANGED")
    budget = [MAX_GEOMETRY_TOTAL_BYTES]
    archive = receipt_path.parent.resolve()
    for name, expected in files.items():
        path = (archive / name).resolve()
        if (
            Path(name).is_absolute()
            or not path.is_relative_to(archive)
            or len(expected) != 64
            or _file_digest(path, budget) != expected
        ):
            raise ValueError("CERTIFIED_GEOMETRY_ARCHIVE_CHANGED")
    if (
        geometry_engine.__file__ is None
        or hashlib.sha256(Path(geometry_engine.__file__).read_bytes()).hexdigest()
        != files["geometry.py"]
    ):
        raise ValueError("CERTIFIED_GEOMETRY_VERIFIER_CHANGED")
    original = ProofBinding.model_validate_json(
        _text(archive / "proof-binding.json", receipt["proof_binding_sha256"])
    )
    if original.geometry is not None or original.model_dump(
        mode="json", exclude={"geometry"}
    ) != binding.model_dump(mode="json", exclude={"geometry"}):
        raise ValueError("CERTIFIED_GEOMETRY_ORIGINAL_BINDING_CORE_CHANGED")
    snapshot_path = (supplement_path.parent / supplement.original_snapshot.path).resolve()
    if _file_digest(snapshot_path, budget) != supplement.original_snapshot.sha256:
        raise ValueError("CERTIFIED_GEOMETRY_ORIGINAL_SNAPSHOT_BYTES_CHANGED")
    snapshot = AcceptedData.model_validate_json(
        _text(snapshot_path, supplement.original_snapshot.sha256, MAX_DOCUMENT_BYTES)
    )
    # File SHA f616... and API canonical e3ac... are distinct. ML1's separate
    # ASCII canonical7c57... remains checked by its existing compatibility gate.
    if (
        snapshot.snapshot_id != original.snapshot_id
        or snapshot.snapshot_id != receipt["snapshot_id"]
        or canonical_hash(snapshot.model_dump(mode="json")) != original.snapshot_hash
        or receipt["snapshot_hash"] != original.snapshot_hash
    ):
        raise ValueError("CERTIFIED_GEOMETRY_ORIGINAL_SNAPSHOT_SEMANTICS_CHANGED")
    return GeometryMetadata(supplement, archive, receipt)


def load_binding(config_hash: str) -> CertifiedRuntime:
    path = Path(os.environ.get("A2_CERTIFIED_WALK_MODEL_PATH", ""))
    expected = os.environ.get("A2_CERTIFIED_WALK_MODEL_SHA256", "")
    try:
        binding = ProofBinding.model_validate_json(_text(path, expected))
        native_path, native_json = _reference(path.parent, binding.native_receipt)
        native = json.loads(native_json)
        files = native["files_sha256"]
        required = {
            "export.jsonl",
            "request.json",
            "valhalla.json",
            "walking-profile.json",
            "build-manifest.json",
            "source-bound-walk",
            "artifact.json",
            "replay.jsonl",
            "turn-plan.json",
            "turn-weights.jsonl",
            "turn-replay.jsonl",
            "source_bound.py",
            "turn_certificate.py",
        }
        if not required <= files.keys() or len(files) > 64:
            raise ValueError("CERTIFIED_NATIVE_SOURCE_INVENTORY_REQUIRED")
        budget = [MAX_NATIVE_TOTAL_BYTES]
        for name, digest in files.items():
            if (
                Path(name).name != name
                or len(digest) != 64
                or _file_digest(native_path.parent / name, budget) != digest
            ):
                raise ValueError("CERTIFIED_NATIVE_SOURCE_FILE_CHANGED")
        source_binding = native["closed_turn_certificate"]["source_binding"]
        for name in required & source_binding.keys():
            if files[name] != source_binding[name]:
                raise ValueError("CERTIFIED_NATIVE_EXECUTION_BINDING_CHANGED")
        if (
            not {
                "export.jsonl",
                "request.json",
                "valhalla.json",
                "walking-profile.json",
                "build-manifest.json",
                "source-bound-walk",
                "artifact.json",
            }
            <= source_binding.keys()
        ):
            raise ValueError("CERTIFIED_NATIVE_EXECUTION_BINDING_REQUIRED")
        scope = hashlib.sha256(json.dumps(source_binding, sort_keys=True).encode()).hexdigest()
        if scope != native["closed_turn_certificate"]["source_scope_id"]:
            raise ValueError("CERTIFIED_NATIVE_EXECUTION_SCOPE_CHANGED")
        verifier_hashes = {}
        for module in (source_bound, turn_certificate, physical_certificate):
            if module.__file__ is None:
                raise ValueError("CERTIFIED_VERIFIER_SOURCE_REQUIRED")
            verifier_hashes[module.__name__] = hashlib.sha256(
                Path(module.__file__).read_bytes()
            ).hexdigest()
        if any(
            verifier_hashes[module.__name__] != files[name]
            for module, name in (
                (source_bound, "source_bound.py"),
                (turn_certificate, "turn_certificate.py"),
            )
        ):
            raise ValueError("CERTIFIED_NATIVE_VERIFIER_IMPLEMENTATION_CHANGED")
        physical = _reference(path.parent, binding.physical_access)[1]
        compatibility = _reference(path.parent, binding.compatibility)[1]
        policy = _reference(path.parent, binding.policy)[1]
        official = _reference(path.parent, binding.official_sources)[1]
        graph = canonical_hash(
            {
                "native_receipt": binding.native_receipt.sha256,
                "native_files": files,
                "physical_access": binding.physical_access.sha256,
                "verifiers": verifier_hashes,
            }
        )
        geometry = None
        if binding.geometry is not None:
            geometry = _geometry_metadata(path.parent, binding.geometry, binding, native)
            graph = canonical_hash(
                {
                    "model_graph": graph,
                    "geometry_supplement": binding.geometry.sha256,
                    "geometry_receipt": geometry.supplement.receipt.sha256,
                    "geometry_export": geometry.receipt["files_sha256"]["export.jsonl"],
                    "geometry_plan": geometry.receipt["files_sha256"]["plan.json"],
                    "geometry_verifier": geometry.receipt["files_sha256"]["geometry.py"],
                }
            )
        profile = canonical_hash(
            {
                "graph": graph,
                "snapshot_hash": binding.snapshot_hash,
                "compatibility": binding.compatibility.sha256,
                "policy": binding.policy.sha256,
                "official_sources": binding.official_sources.sha256,
                "config_hash": config_hash,
            }
        )
        return CertifiedRuntime(
            binding,
            expected,
            native_path.parent,
            native,
            physical,
            compatibility,
            policy,
            official,
            graph,
            profile,
            geometry,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise unavailable("CERTIFIED_WALK_MODEL_INPUTS_UNAVAILABLE") from exc


def _scope(runtime: CertifiedRuntime, request: AnalysisRequest, snapshot: AcceptedData) -> None:
    binding = runtime.binding
    if not isinstance(request, UrbanPassengerRequest) or (
        request.city_id != binding.city_id
        or request.passenger_mode != "walk"
        or request.traffic_mode != "static"
        or request.categories != ["airport"]
        or request.area_id is not None
        or snapshot.snapshot_id != binding.snapshot_id
        or canonical_hash(snapshot.model_dump(mode="json")) != binding.snapshot_hash
    ):
        raise unavailable("REQUEST_OUTSIDE_CERTIFIED_WALK_MODEL_SCOPE")
    city = next(t for t in snapshot.territories if t.territory_id == binding.city_id)
    if request.departure_at.astimezone(ZoneInfo(city.timezone)).date() != binding.service_date:
        raise unavailable("CERTIFIED_AIRPORT_CATALOG_DATE_UNAVAILABLE")
    if runtime.digest not in (RuntimeSettings().code_version or ""):
        raise unavailable("CERTIFIED_WALK_MODEL_CODE_IDENTITY_CHANGED")


async def preflight(request: AnalysisRequest, snapshot: AcceptedData) -> None:
    def check() -> None:
        runtime = load_binding(ProductRegistry().config_hash)
        _scope(runtime, request, snapshot)

    await asyncio.to_thread(check)


def _verify_native(
    runtime: CertifiedRuntime,
) -> tuple[DesignatedAccessReceipt, DesignatedAccessResult, str, CertifiedWalkGeometry | None]:
    """Recompute the exact certificate; receipt output flags are never read."""
    native, root = runtime.native, runtime.native_root
    files = native["files_sha256"]
    binding = native["closed_turn_certificate"]["source_binding"]
    scope = native["closed_turn_certificate"]["source_scope_id"]
    graph = source_bound.load_native_walk_export(
        _text(root / "export.jsonl", files["export.jsonl"], MAX_NATIVE_FILE_BYTES),
        expected_sha256=files["export.jsonl"],
    )
    artifact_json = _text(root / "artifact.json", files["artifact.json"])
    manifest = json.loads(_text(root / "build-manifest.json", files["build-manifest.json"]))
    access = physical_certificate.load_designated_access_receipt(
        runtime.physical_json,
        expected_sha256=runtime.binding.physical_access.sha256,
        graph=graph,
        expected_bindings={
            "source_scope_id": scope,
            "artifact_sha256": binding["artifact.json"],
            "profile_sha256": binding["walking-profile.json"],
            "request_sha256": binding["request.json"],
            "pbf_sha256": manifest["source_sha256"],
        },
        artifact=json.loads(artifact_json),
    )
    initial = source_bound.solve_native_walk_relaxation(graph)
    incumbent = turn_certificate.make_native_turn_incumbent(
        graph, scope, initial, _text(root / "replay.jsonl", files["replay.jsonl"])
    )
    plan = turn_certificate.plan_native_turn_pairs(graph, scope, incumbent)
    if plan.request_json() != _text(
        root / "turn-plan.json", files["turn-plan.json"], MAX_NATIVE_FILE_BYTES
    ):
        raise ValueError("CERTIFIED_NATIVE_TURN_PLAN_CHANGED")
    weights = turn_certificate.load_native_turn_weights(
        _text(root / "turn-weights.jsonl", files["turn-weights.jsonl"], MAX_NATIVE_FILE_BYTES),
        plan,
        expected_sha256=files["turn-weights.jsonl"],
    )
    optimum = turn_certificate.solve_native_turn_plan(graph, plan, weights)
    minimum = physical_certificate.verify_common_designated_access_minimum(
        graph,
        access,
        plan,
        weights,
        optimum,
        _text(root / "turn-replay.jsonl", files["turn-replay.jsonl"]),
    )
    geometry = None
    if runtime.geometry is not None:
        meta = runtime.geometry
        files = meta.receipt["files_sha256"]
        geometry = geometry_engine.load_certified_walk_geometry(
            _text(meta.root / "export.jsonl", files["export.jsonl"], MAX_GEOMETRY_TOTAL_BYTES),
            expected_sha256=files["export.jsonl"],
            plan_json=_text(meta.root / "plan.json", files["plan.json"]),
            graph=graph,
            candidate=minimum.candidate.network_path,
            access=access,
        )
        if (
            geometry.physical_coordinates is None
            or geometry.discontinuities
            or len(geometry.edge_ids) != meta.receipt["edge_count"]
            or len(geometry.physical_coordinates) != meta.receipt["physical_vertices"]
            or geometry.candidate_sha256 != meta.receipt["candidate_sha256"]
            or Fraction(meta.receipt["unchanged_model_total_seconds"]) != minimum.lower_seconds
        ):
            raise ValueError("CERTIFIED_GEOMETRY_VERIFIED_MODEL_MISMATCH")
    return access, minimum, artifact_json, geometry


def _append_geometry(
    out: Projection,
    runtime: CertifiedRuntime,
    point: CanonicalWalkPointResult,
    minimum: DesignatedAccessResult,
    geometry: CertifiedWalkGeometry,
) -> None:
    """Present a microsecond timeline without rounding the methodology operands."""
    meta = runtime.geometry
    if (
        meta is None
        or geometry.physical_geojson is None
        or point.destination.physical_node_id is None
    ):
        raise ValueError("CERTIFIED_GEOMETRY_PHYSICAL_CHAIN_REQUIRED")
    if point.minimum_seconds.value is not None and point.minimum_seconds.value != float(
        minimum.lower_seconds
    ):
        raise ValueError("CERTIFIED_GEOMETRY_EXACT_TIME_OPERAND_CHANGED")
    source_id = "osm-geofabrik-pbf-" + json.loads(runtime.physical_json)["pbf_sha256"]
    source = next((row for row in out.evidence if row.evidence_id == source_id), None)
    if source is None:
        raise ValueError("CERTIFIED_GEOMETRY_SOURCE_EVIDENCE_REQUIRED")
    geometry_refs = []
    for kind, digest in (
        ("receipt", meta.supplement.receipt.sha256),
        ("export", geometry.geometry_export_sha256),
        ("plan", meta.receipt["files_sha256"]["plan.json"]),
    ):
        identity = "certified-walk-geometry-" + kind + ":" + digest
        # Preserve the pinned source retrieval metadata. This is a new local
        # proof section of that source, not a claim of a remote receipt download.
        out.evidence.append(
            source.model_copy(
                update={
                    "evidence_id": identity,
                    "section": "Locally preserved certified geometry "
                    + kind
                    + " SHA256 "
                    + digest
                    + "; URL identifies originating PBF, not this local proof artifact.",
                }
            )
        )
        geometry_refs.append(identity)
    refs = sorted(set(point.evidence_refs) | set(geometry_refs))
    microseconds = round(minimum.lower_seconds * 1_000_000)
    departure = out.context.normalized_request.departure_at.astimezone(UTC)
    arrival = departure + timedelta(microseconds=microseconds)
    timeline_seconds = (arrival - departure).total_seconds()
    if abs(Fraction(microseconds, 1_000_000) - minimum.lower_seconds) > Fraction(1, 2_000_000):
        raise ValueError("CERTIFIED_GEOMETRY_TIMELINE_PRECISION_EXCEEDED")
    identity = (
        "urban-canonical-route-"
        + canonical_hash(
            {
                "binding": runtime.digest,
                "geometry": geometry.geometry_export_sha256,
                "point": point.point.point_id,
                "destination": point.destination.destination_id,
                "departure": departure.isoformat(),
            }
        )[:24]
    )
    out.journeys.append(
        JourneyResult(
            journey_id=identity,
            scope="urban",
            origin_node_id=point.point.origin_node_id,
            destination_node_id=point.destination.physical_node_id,
            requested_departure_at=departure,
            departure_at=departure,
            arrival_at=arrival,
            total_seconds=timeline_seconds,
            initial_wait_seconds=0,
            path_seconds=timeline_seconds,
            total_wait_seconds=0,
            legs=[
                Leg(
                    mode="walk",
                    kind="walk",
                    from_node_id=point.point.origin_node_id,
                    to_node_id=point.destination.physical_node_id,
                    start_at=departure,
                    end_at=arrival,
                    duration_seconds=timeline_seconds,
                    service_id=None,
                    evidence_refs=refs,
                )
            ],
            geometry=LineGeometry.model_validate(
                {**geometry.physical_geojson, "evidence_refs": geometry_refs}
            ),
            geometry_kind="actual",
            status="available",
            reason_code="CANONICAL_ACCESS_MODEL",
            snapshot_id=out.context.snapshot_id,
            completeness="partial",
            traffic_mode="static",
            time_kind="complete_chain",
            reference_id=None,
            destination_object_id=point.destination.destination_id,
            point_id=point.point.point_id,
            evidence_refs=refs,
        )
    )
    out.metric(
        "urban-model-timeline-" + point.point.point_id,
        "total_seconds",
        known(timeline_seconds),
        SubjectRef(
            kind="sample_point",
            territory_id=point.context.city_id,
            point_id=point.point.point_id,
            category="airport",
            mode="walk",
        ),
        refs,
        journey_id=identity,
        time_kind="complete_chain",
    )
    out.limitations.update(geometry.limitations)
    out.limitations.add("MODEL_TIMELINE_MICROSECOND_PRECISION")


def calculate(claim: ClaimedJob) -> AnalysisResult:
    from a2_data_pipeline.contract_v2 import load_accepted_data
    from a2_engine.urban.walk.canonical_point import project_canonical_airport_point

    if claim.kind != "analysis" or not isinstance(claim.context, V2JobContext):
        raise unavailable("CERTIFIED_WALK_MODEL_ANALYSIS_CONTEXT_REQUIRED")
    context = claim.context
    registry = ProductRegistry()
    runtime = load_binding(registry.config_hash)
    if (
        context.snapshot_hash != runtime.binding.snapshot_hash
        or context.versions.config_hash != registry.config_hash
        or context.versions.methodology_version != registry.methodology.version
        or context.code_version != RuntimeSettings().code_version
    ):
        raise unavailable("CERTIFIED_WALK_MODEL_SAVED_CONTEXT_CHANGED")
    snapshot = load_accepted_data(context.snapshot_id)
    if not isinstance(snapshot, AcceptedData):
        raise unavailable("CERTIFIED_WALK_MODEL_ACCEPTED_DATA_REQUIRED")
    _scope(runtime, context.normalized_request, snapshot)
    access, minimum, artifact_json, geometry = _verify_native(runtime)
    request = context.normalized_request
    assert isinstance(request, UrbanPassengerRequest)
    point = project_canonical_airport_point(
        snapshot.model_dump(mode="json"),
        artifact_json,
        access,
        minimum,
        compatibility_json=runtime.compatibility_json,
        compatibility_sha256=runtime.binding.compatibility.sha256,
        policy_json=runtime.policy_json,
        policy_sha256=runtime.binding.policy.sha256,
        official_sources_json=runtime.official_json,
        official_sources_sha256=runtime.binding.official_sources.sha256,
        requested_at=request.departure_at,
        selected_categories={str(key): str(value) for key, value in request.priorities.items()},
        config=load_urban_config(),
        config_hash=registry.config_hash,
    )
    if (
        point.point.point_id != runtime.binding.point_id
        or point.destination.destination_id != runtime.binding.destination_id
    ):
        raise unavailable("CERTIFIED_WALK_MODEL_SUBJECT_CHANGED")
    evidence = {
        row.evidence_id: EvidenceRef(
            evidence_id=row.evidence_id,
            source_id=row.source_id,
            url=row.url,
            retrieved_at=row.retrieved_at,
            observed_at=None,
            section="accepted-snapshot",
        )
        for row in snapshot.evidence
    }
    for row in json.loads(artifact_json)["evidence_refs"]:
        parsed = EvidenceRef.model_validate(row)
        evidence.setdefault(parsed.evidence_id, parsed)
    for row in runtime.binding.evidence:
        if row.evidence_id in evidence and row != evidence[row.evidence_id]:
            raise unavailable("CERTIFIED_WALK_MODEL_EVIDENCE_IDENTITY_CONFLICT")
        evidence[row.evidence_id] = row
    if not set(point.evidence_refs) <= evidence.keys():
        raise unavailable("CERTIFIED_WALK_MODEL_EVIDENCE_UNRESOLVED")
    out = Projection(context, list(evidence.values()))
    territory = SubjectRef(kind="territory", territory_id=request.city_id, mode="walk")
    h = out.metric(
        "urban-h", "urban_h_seconds", point.h_seconds, territory, point.area.source_refs, full=True
    )
    point_scores = []
    features = []
    cells = {cell.cell_id: cell for cell in snapshot.population_cells}
    mapped_cells = set()
    for sample in snapshot.sample_points:
        if sample.territory_id != request.city_id:
            continue
        subject = SubjectRef(
            kind="sample_point",
            territory_id=request.city_id,
            point_id=sample.point_id,
            category="airport",
            mode="walk",
        )
        selected = sample.point_id == point.point.point_id
        refs = point.evidence_refs if selected else tuple(sample.source_refs)
        cell = cells[sample.cell_id]
        if cell.cell_id not in mapped_cells:
            features.append(
                MapFeature(
                    entity_kind="cell",
                    entity_id=cell.cell_id,
                    cell_id=cell.cell_id,
                    territory_id=request.city_id,
                    geometry=None,
                    geometry_kind="none",
                    geometry_ref=cell.geometry.version,
                    reason_code="MAP_DATA_UNAVAILABLE",
                    population=cell.population,
                    population_year=cell.population_year,
                    population_method=cell.population_method,
                    source_refs=cell.source_refs,
                )
            )
            mapped_cells.add(cell.cell_id)
        basis = PopulationBasis(
            population=float(cell.population) if cell.population is not None else None,
            population_method=cell.population_method or "unknown",
            year=cell.population_year,
            coverage=1 if cell.population is not None else 0,
            unit="persons",
            unknown_positive_weight=cell.population is None,
        )
        features.append(
            MapFeature(
                entity_kind="sample_point",
                entity_id=sample.point_id,
                territory_id=request.city_id,
                point_id=sample.point_id,
                cell_id=sample.cell_id,
                geometry=PointGeometry(
                    type="Point", coordinates=(sample.location.lon, sample.location.lat)
                ),
                geometry_kind="actual",
                source_refs=sorted(set(sample.source_refs) | set(cell.source_refs)),
                population=cell.population,
                population_year=cell.population_year,
                population_method=cell.population_method,
            )
        )
        key = canonical_hash([sample.point_id, "airport", "walk"])[:24]
        time = out.metric(
            "urban-time-" + key,
            "minimum_journey_seconds",
            point.minimum_seconds if selected else known(None, "UNCOMPUTED_TERRITORY_POINTS"),
            subject,
            refs,
            time_kind="complete_chain",
            population_basis=basis,
        )
        category = out.score(
            "urban-category-" + key,
            "category_index",
            point.category_index if selected else known(None, "UNCOMPUTED_TERRITORY_POINTS"),
            subject,
            refs,
            metrics=(time, h),
        )
        point_scores.append(
            out.score(
                "urban-point-" + sample.point_id,
                "point_index",
                point.point_index if selected else known(None, "UNCOMPUTED_TERRITORY_POINTS"),
                subject.model_copy(update={"category": None}),
                refs,
                scores=(category,),
            )
        )
    out.score(
        "urban-territory-walk",
        "territory_index",
        point.city_index,
        territory,
        point.evidence_refs,
        scores=tuple(point_scores),
        basis=PopulationBasis(
            population=None,
            population_method="accepted_original_cell_weights",
            year=None,
            coverage=0,
            unit="persons",
            unknown_positive_weight=True,
        ),
    )
    out.limitations.update(point.limitations)
    if geometry is None:
        out.limitations.add("CERTIFIED_MODEL_DURATION_ONLY_NO_ROUTE_GEOMETRY")
    else:
        _append_geometry(out, runtime, point, minimum, geometry)
    destination = next(
        row
        for row in snapshot.destinations
        if row.destination_id == point.destination.destination_id
    )
    features.append(
        MapFeature(
            entity_kind="destination",
            entity_id=destination.destination_id,
            territory_id=request.city_id,
            categories=["airport"],
            name=point.destination_name,
            geometry=PointGeometry(
                type="Point", coordinates=(destination.location.lon, destination.location.lat)
            ),
            geometry_kind="actual",
            source_refs=destination.source_refs,
        )
    )
    result = out.finish()
    result = AnalysisResult.model_validate(
        {
            **result.model_dump(mode="json"),
            "map_data": AnalysisMapData(
                status="available",
                completeness="partial",
                reason_code="CANONICAL_ACCESS_MODEL",
                features=features,
            ).model_dump(mode="json"),
        }
    )
    return result.model_copy(
        update={
            "result_hash": canonical_hash(result.model_dump(mode="json", exclude={"result_hash"}))
        }
    )
