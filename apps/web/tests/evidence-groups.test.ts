import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { EvidenceResolver, evidenceResolver, type EvidenceContext, type EvidenceGroupLike } from "../src/api/adapters/evidenceGroups";

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
});
