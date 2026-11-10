import type { Schemas } from "../types";

type Evidence = Schemas["EvidenceRef"];
type EvidenceResult = Schemas["AnalysisResult"] | Schemas["ScenarioResult"] | Schemas["ScenarioProposal"];
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
    if (typeof references === "string") throw invalid();
    for (const id of references) if (!this.leaves.has(id) && !this.groups.has(id)) throw invalid();
  }

  leaf(id: string) { return this.leaves.get(id); }

  expand(references: Iterable<string>) {
    if (typeof references === "string") throw invalid();
    const leaves = new Set<string>(), visited = new Set<string>();
    for (const id of references) {
      if (visited.has(id)) continue;
      visited.add(id);
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

export function* journeyEvidenceReferences(journey: Schemas["JourneyResult"]) {
  yield* journey.evidence_refs;
  if (journey.geometry) yield* journey.geometry.evidence_refs;
  for (const leg of journey.legs) {
    yield* leg.evidence_refs;
    if (leg.geometry) yield* leg.geometry.evidence_refs;
  }
}

function* resultEvidenceReferences(value: EvidenceResult) {
  if ("fixed_parameters" in value) {
    yield* value.fixed_parameters.demand_evidence;
    yield* value.fixed_parameters.resource_evidence;
    yield* value.fixed_parameters.turnaround_evidence;
    return;
  }
  for (const values of "before" in value ? [value.before, value.after] : [value]) {
    for (const metric of values.metrics) yield* metric.source_refs;
    for (const score of values.scores) yield* score.evidence_refs;
  }
  for (const journey of "journeys" in value ? value.journeys : value.after_journeys || []) yield* journeyEvidenceReferences(journey);
  if ("journeys" in value) {
    for (const feature of value.map_data?.features || []) yield* feature.source_refs;
    for (const reference of value.references) yield* reference.calibration_evidence;
    for (const count of value.counts) yield* count.evidence_refs;
    for (const lastReturn of value.last_returns) yield* lastReturn.evidence_refs;
  } else {
    yield* value.resource_evidence || [];
    yield* value.turnaround_evidence;
  }
}

export function validateGroupedEvidence(value: EvidenceResult) {
  if (value.evidence_groups?.length) evidenceResolver(value).validate(resultEvidenceReferences(value));
  else {
    // Legacy source inventories and rows retain their identities and order.
    if (value.evidence_refs.some((source) => source.evidence_id.startsWith(groupPrefix))) throw invalid();
    for (const id of resultEvidenceReferences(value)) if (id.startsWith(groupPrefix)) throw invalid();
  }
}

function sameLeaf(left: Evidence, right: Evidence) {
  return left.source_id === right.source_id && left.url === right.url && left.section === right.section &&
    left.observed_at === right.observed_at && left.retrieved_at === right.retrieved_at;
}

export function explanationSources(result: Schemas["AnalysisResult"], answer: Schemas["Explanation"]) {
  if (answer.analysis_id !== result.analysis_id || answer.analysis_result_hash !== result.result_hash) throw invalid();
  const resolver = evidenceResolver(result), retrieved = answer.retrieved_evidence || [];
  const extra = new Map<string, Evidence>();
  for (const source of retrieved) {
    if (source.evidence_id.startsWith(groupPrefix) || extra.has(source.evidence_id)) throw invalid();
    const known = resolver.leaf(source.evidence_id);
    if (known && !sameLeaf(known, source)) throw invalid();
    extra.set(source.evidence_id, source);
  }
  const sources = new Map<string, Evidence>();
  const add = (source: Evidence) => {
    const previous = sources.get(source.evidence_id);
    if (previous && !sameLeaf(previous, source)) throw invalid();
    sources.set(source.evidence_id, source);
  };
  for (const source of retrieved) add(source);
  function* citations() {
    yield* answer.evidence_refs;
    for (const finding of answer.findings) {
      yield* finding.evidence_refs;
      for (const id of finding.metric_ids) yield* result.metrics.find((metric) => metric.metric_id === id)?.source_refs || [];
      for (const id of finding.score_ids || []) yield* result.scores.find((score) => score.score_id === id)?.evidence_refs || [];
    }
    for (const proposal of answer.proposals) yield* proposal.evidence_refs;
  }
  function* ownedCitations() {
    // Retrieved documents add only bounded leaf definitions, never a foreign group table.
    for (const id of citations()) if (!extra.has(id) || resolver.leaf(id)) yield id;
  }
  for (const source of resolver.sources(ownedCitations())) add(source);
  return [...sources.values()];
}
