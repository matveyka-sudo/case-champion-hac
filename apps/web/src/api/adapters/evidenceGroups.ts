import type { Schemas } from "../types";

type Evidence = Schemas["EvidenceRef"];
export interface EvidenceGroupLike {
  readonly group_id: string;
  readonly evidence_ids: readonly string[];
}
export interface EvidenceContext {
  readonly evidence_refs: readonly Evidence[];
  readonly evidence_groups?: readonly EvidenceGroupLike[];
}

const groupPrefix = "evidence-group:";
const groupPattern = /^evidence-group:[a-f0-9]{64}$/;
const invalid = () => new Error("EVIDENCE_REFERENCES_INVALID");

// The server sorts Unicode scalar values when building canonical group identities.
function compareIds(left: string, right: string) {
  let a = 0, b = 0;
  while (a < left.length && b < right.length) {
    const x = left.codePointAt(a)!, y = right.codePointAt(b)!;
    if (x !== y) return x - y;
    a += x > 0xffff ? 2 : 1;
    b += y > 0xffff ? 2 : 1;
  }
  return left.length - a - (right.length - b);
}

export class EvidenceResolver {
  private readonly context: EvidenceContext;
  private readonly leaves = new Map<string, Evidence>();
  private readonly groups = new Map<string, readonly string[]>();

  constructor(context: EvidenceContext) {
    this.context = context;
    for (const leaf of context.evidence_refs) {
      if (!leaf.evidence_id || leaf.evidence_id.startsWith(groupPrefix) || this.leaves.has(leaf.evidence_id)) throw invalid();
      this.leaves.set(leaf.evidence_id, leaf);
    }
    const groups = context.evidence_groups || [];
    if (groups.length > 8192) throw invalid();
    let members = 0;
    for (const group of groups) {
      if (!groupPattern.test(group.group_id) || this.groups.has(group.group_id) || group.evidence_ids.length < 2 || group.evidence_ids.length > 4096) throw invalid();
      members += group.evidence_ids.length;
      if (members > 65536) throw invalid();
      for (let index = 0; index < group.evidence_ids.length; index++) {
        const id = group.evidence_ids[index];
        if (!this.leaves.has(id) || (index > 0 && compareIds(group.evidence_ids[index - 1], id) >= 0)) throw invalid();
      }
      // Keep the already bounded member list; never expand it into every referencing row.
      this.groups.set(group.group_id, group.evidence_ids);
    }
  }

  validate(references: Iterable<string>) {
    for (const id of references) if (!this.leaves.has(id) && !this.groups.has(id)) throw invalid();
  }

  expand(references: Iterable<string>) {
    const leaves = new Set<string>();
    for (const id of references) {
      if (this.leaves.has(id)) leaves.add(id);
      else {
        const group = this.groups.get(id);
        if (!group) throw invalid();
        for (const member of group) leaves.add(member);
      }
    }
    return leaves;
  }

  sources(references?: Iterable<string>): readonly Evidence[] {
    if (references === undefined) return this.context.evidence_refs;
    return [...this.expand(references)].map((id) => this.leaves.get(id)!);
  }
}

// Saved responses are immutable. Separate response objects never share a source namespace.
const resolvers = new WeakMap<EvidenceContext, EvidenceResolver>();
export function evidenceResolver(context: EvidenceContext) {
  let resolver = resolvers.get(context);
  if (!resolver) {
    resolver = new EvidenceResolver(context);
    resolvers.set(context, resolver);
  }
  return resolver;
}
