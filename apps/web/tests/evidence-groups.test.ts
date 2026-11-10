import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { EvidenceResolver, evidenceResolver, explanationSources, type EvidenceContext, type EvidenceGroupLike } from "../src/api/adapters/evidenceGroups";
import { decode } from "../src/api/validation";
import type { Schemas } from "../src/api/types";
import urban from "./fixtures/api/urban_passenger.json";
import scenario from "./fixtures/api/scenario.json";
import proposal from "./fixtures/api/proposal.json";
import { groupedEvidenceFixture } from "./fixtures/evidence-groups";
import { legGeometryFixture } from "./fixtures/leg-geometry";

const leaf = (id: string, section = id) => ({ evidence_id: id, source_id: "synthetic", section, url: "https://example.org/source", observed_at: null, retrieved_at: "2026-11-10T00:00:00Z" });
const group = (ids: string[]): EvidenceGroupLike => ({ group_id: `evidence-group:${createHash("sha256").update(JSON.stringify(ids)).digest("hex")}`, evidence_ids: ids });
const owner = (): EvidenceContext => ({ evidence_refs: [leaf("leaf-c"), leaf("leaf-a"), leaf("leaf-b")], evidence_groups: [group(["leaf-a", "leaf-b"])] });

describe("bounded response-owned evidence groups", () => {
  it("preserves omitted and empty-group legacy bytes, source order, and object identities", () => {
    for (const context of [{ evidence_refs: [leaf("b"), leaf("a")] }, { evidence_refs: [leaf("b"), leaf("a")], evidence_groups: [] }]) {
      const wire = JSON.stringify(context);
      expect(evidenceResolver(context).sources()).toBe(context.evidence_refs);
      expect(evidenceResolver(context).sources(["b", "a"])).toEqual(context.evidence_refs);
      expect(JSON.stringify(context)).toBe(wire);
      expect(evidenceResolver(context)).toBe(evidenceResolver(context));
    }
  });

  it("expands mixed direct and grouped citations into unique leaf sources without rewriting any row", () => {
    const context = owner(), before = JSON.stringify(context);
    const resolver = evidenceResolver(context), id = context.evidence_groups![0].group_id;
    const references = ["leaf-c", id, "leaf-a", id];
    resolver.validate(references);
    expect([...resolver.expand(references)]).toEqual(["leaf-c", "leaf-a", "leaf-b"]);
    expect(resolver.sources(references)).toEqual(context.evidence_refs);
    expect(resolver.sources(references)[0]).toBe(context.evidence_refs[0]);
    expect(resolver.sources([])).toEqual([]);
    expect(JSON.stringify(context)).toBe(before);
    expect(references).toEqual(["leaf-c", id, "leaf-a", id]);
  });

  it("reads each selected group once even when many rows cite it", () => {
    let reads = 0;
    const members = ["leaf-a", "leaf-b"], id = group(members).group_id;
    const context = { evidence_refs: members.map((item) => leaf(item)), evidence_groups: [{ group_id: id, evidence_ids: new Proxy(members, {
      get(target, property, receiver) {
        if (property === Symbol.iterator) reads++;
        return Reflect.get(target, property, receiver);
      },
    }) }] };
    const resolver = evidenceResolver(context);
    function* citations() { for (let index = 0; index < 100_000; index++) yield id; }
    expect(resolver.sources(citations())).toEqual(context.evidence_refs);
    expect(reads).toBe(1);
  });

  it("keeps the same group identity scoped to each owning result's leaf definitions", () => {
    const baseline = owner();
    const scenario = { evidence_refs: baseline.evidence_refs.map((item) => ({ ...item, section: `Scenario ${item.section}` })), evidence_groups: baseline.evidence_groups };
    const id = baseline.evidence_groups![0].group_id;
    expect(evidenceResolver(baseline).sources([id]).map((item) => item.section)).toEqual(["leaf-a", "leaf-b"]);
    expect(evidenceResolver(scenario).sources([id]).map((item) => item.section)).toEqual(["Scenario leaf-a", "Scenario leaf-b"]);
    expect(evidenceResolver(baseline)).not.toBe(evidenceResolver(scenario));
  });

  it("rejects unknown and foreign citations without exposing their IDs in errors", () => {
    const context = owner(), foreign = group(["foreign-a", "foreign-b"]).group_id;
    for (const id of ["missing-leaf", foreign]) {
      for (const resolve of [() => evidenceResolver(context).validate([id]), () => evidenceResolver(context).sources([id])]) {
        expect(resolve).toThrow("EVIDENCE_REFERENCES_INVALID");
        try { resolve(); } catch (error) { expect((error as Error).message).not.toContain(id); }
      }
    }
    const characters = evidenceResolver({ evidence_refs: [leaf("a"), leaf("b")] });
    expect(() => characters.validate("ab")).toThrow("EVIDENCE_REFERENCES_INVALID");
    expect(() => characters.sources("ab")).toThrow("EVIDENCE_REFERENCES_INVALID");
  });

  it.each([
    ["unknown member", { evidence_refs: [leaf("a"), leaf("b")], evidence_groups: [group(["a", "foreign"])] }],
    ["nested group", { evidence_refs: [leaf("a"), leaf("b")], evidence_groups: [group(["a", "b"]), group(["a", group(["a", "b"]).group_id])] }],
    ["duplicate leaf identity", { evidence_refs: [leaf("a"), leaf("a")], evidence_groups: [] }],
    ["reserved leaf namespace", { evidence_refs: [leaf(group(["a", "b"]).group_id)], evidence_groups: [] }],
    ["duplicate group identity", { evidence_refs: [leaf("a"), leaf("b")], evidence_groups: [group(["a", "b"]), group(["a", "b"])] }],
    ["duplicate members", { evidence_refs: [leaf("a"), leaf("b")], evidence_groups: [group(["a", "a"])] }],
    ["unsorted members", { evidence_refs: [leaf("a"), leaf("b")], evidence_groups: [group(["b", "a"])] }],
    ["single member", { evidence_refs: [leaf("a")], evidence_groups: [group(["a"])] }],
    ["invalid group namespace", { evidence_refs: [leaf("a"), leaf("b")], evidence_groups: [{ ...group(["a", "b"]), group_id: "group:a" }] }],
    ["invalid digest shape", { evidence_refs: [leaf("a"), leaf("b")], evidence_groups: [{ ...group(["a", "b"]), group_id: "evidence-group:ABC" }] }],
  ] as const)("rejects %s", (_name, context) => {
    expect(() => new EvidenceResolver(context)).toThrow("EVIDENCE_REFERENCES_INVALID");
  });

  it("checks Unicode member order using the server's scalar-value ordering", () => {
    const context = { evidence_refs: [leaf("\ue000"), leaf("😀")], evidence_groups: [group(["\ue000", "😀"])] };
    expect(evidenceResolver(context).sources([context.evidence_groups[0].group_id]).map((item) => item.evidence_id)).toEqual(["\ue000", "😀"]);
    expect(() => new EvidenceResolver({ ...context, evidence_groups: [group(["😀", "\ue000"])] })).toThrow();
  });

  it("enforces group-count, member-count, and total-membership bounds", () => {
    const ids = Array.from({ length: 8194 }, (_, index) => `leaf-${index.toString().padStart(5, "0")}`);
    const evidence_refs = ids.map((id) => leaf(id));
    expect(() => new EvidenceResolver({ evidence_refs, evidence_groups: Array.from({ length: 8193 }, (_, index) => group([ids[0], ids[index + 1]])) })).toThrow();
    expect(() => new EvidenceResolver({ evidence_refs, evidence_groups: [group(ids.slice(0, 4097))] })).toThrow();
    const atLimit = Array.from({ length: 16 }, (_, index) => group([...ids.slice(0, 4095), ids[4096 + index]]));
    const context = { evidence_refs, evidence_groups: atLimit };
    expect(() => new EvidenceResolver(context)).not.toThrow();
    expect(() => new EvidenceResolver({ ...context, evidence_groups: [...atLimit, group(ids.slice(0, 2))] })).toThrow();
  });

  it("decodes all three canonical containers without expanding their grouped reference rows", () => {
    for (const [name, fixture] of [["AnalysisResult", urban], ["ScenarioResult", scenario], ["ScenarioProposal", proposal]] as const) {
      const data = groupedEvidenceFixture(fixture as Schemas[typeof name]);
      const before = JSON.stringify(data);
      expect(decode(name, data)).toBe(data);
      expect(JSON.stringify(data)).toBe(before);
    }
  });

  it("keeps canonical legacy containers unchanged and rejects group citations without their owning table", () => {
    for (const [name, fixture] of [["AnalysisResult", urban], ["ScenarioResult", scenario], ["ScenarioProposal", proposal]] as const) {
      const data = structuredClone(fixture) as Schemas[typeof name], before = JSON.stringify(data);
      expect(decode(name, data)).toBe(data);
      expect(JSON.stringify(data)).toBe(before);
      const grouped = groupedEvidenceFixture(data);
      const refs = [grouped.evidence_groups![0].group_id];
      if ("fixed_parameters" in grouped) grouped.fixed_parameters.demand_evidence = refs;
      else if ("before" in grouped) grouped.after.metrics[0].source_refs = refs;
      else grouped.metrics[0].source_refs = refs;
      delete grouped.evidence_groups;
      expect(() => decode(name, grouped)).toThrow("EVIDENCE_REFERENCES_INVALID");
      grouped.evidence_groups = [];
      expect(() => decode(name, grouped)).toThrow("EVIDENCE_REFERENCES_INVALID");
    }
  });

  it("rejects unresolved grouped references across metrics, geometry, map, counts and last returns", () => {
    const value = groupedEvidenceFixture(legGeometryFixture());
    const paths: ((data: typeof value) => void)[] = [
      (data) => { data.metrics[0].source_refs = ["foreign-source"]; },
      (data) => { data.scores[0].evidence_refs = ["foreign-source"]; },
      (data) => { data.map_data!.features![0].source_refs = ["foreign-source"]; },
      (data) => { data.journeys[0].evidence_refs = ["foreign-source"]; },
      (data) => { data.journeys[0].legs[0].evidence_refs = ["foreign-source"]; },
      (data) => { data.journeys[0].legs[1].geometry!.evidence_refs = ["foreign-source"]; },
      (data) => { data.references = [{ reference_id: "synthetic-reference", status: "unknown", seconds: null, reason_code: "SYNTHETIC_ONLY", completeness: "partial", profile_version: "edge-calibrated-v1", graph_version: data.versions.graph_version, edges: [], boundary_node_sets: { origin_node_ids: ["synthetic-origin"], destination_node_ids: ["synthetic-destination"] }, necessary_operations: [], calibration_evidence: ["foreign-source"] }]; },
      (data) => { data.counts = [{ metric_id: "synthetic-count", status: "unknown", value: null, threshold_seconds: null, dependency_job_id: null, reason_code: "SYNTHETIC_ONLY", evidence_refs: ["foreign-source"] }]; },
      (data) => { data.last_returns = [{ point_id: "synthetic-point", destination_object_id: "synthetic-destination", journey_id: null, leave_at: null, first_departure_at: null, arrival_at: null, service_date: "2026-11-09", timezone: "Europe/Moscow", status: "unknown", reason_code: "SYNTHETIC_ONLY", evidence_refs: ["foreign-source"] }]; },
    ];
    for (const mutate of paths) {
      const data = structuredClone(value);
      mutate(data);
      expect(() => decode("AnalysisResult", data)).toThrow("EVIDENCE_REFERENCES_INVALID");
    }
  });

  it("validates scenario and proposal proof citations against their own groups", () => {
    const after = groupedEvidenceFixture(scenario as Schemas["ScenarioResult"]);
    const proposed = groupedEvidenceFixture(proposal as Schemas["ScenarioProposal"]);
    for (const mutate of [(data: typeof after) => { data.after.metrics[0].source_refs = ["foreign-source"]; },
      (data: typeof after) => { data.turnaround_evidence = ["foreign-source"]; },
      (data: typeof after) => { data.resource_evidence = ["foreign-source"]; }]) {
      const data = structuredClone(after); mutate(data);
      expect(() => decode("ScenarioResult", data)).toThrow("EVIDENCE_REFERENCES_INVALID");
    }
    proposed.fixed_parameters.resource_evidence = ["foreign-source"];
    expect(() => decode("ScenarioProposal", proposed)).toThrow("EVIDENCE_REFERENCES_INVALID");
  });

  it("resolves saved finding citations plus bounded retrieved leaves without cross-context substitutions", () => {
    const data = groupedEvidenceFixture(urban as Schemas["AnalysisResult"]), external = leaf("retrieved-document");
    const answer: Schemas["Explanation"] = { schema_version: "2", analysis_id: data.analysis_id, analysis_result_hash: data.result_hash, request_id: "synthetic-explanation", status: "template", model_id: null, reason_code: "DISABLED", findings: [{ text: "Synthetic finding", metric_ids: [data.metrics[0].metric_id], claims: [], evidence_refs: ["retrieved-document"] }], evidence_refs: [data.evidence_groups![0].group_id], proposals: [], limitations: [], retrieved_evidence: [external] };
    expect(explanationSources(data, answer).map((item) => item.evidence_id)).toEqual([external.evidence_id, ...data.evidence_groups![0].evidence_ids]);
    expect(() => explanationSources(data, { ...answer, evidence_refs: [group(["foreign-a", "foreign-b"]).group_id] })).toThrow();
    expect(() => explanationSources(data, { ...answer, retrieved_evidence: [{ ...data.evidence_refs[0], section: "Foreign replacement" }] })).toThrow();
    expect(() => explanationSources(data, { ...answer, analysis_result_hash: "foreign-result" })).toThrow();
  });
});
