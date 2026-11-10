import { createHash } from "node:crypto";
import type { Schemas } from "../../src/api/types";

type EvidenceResult = Schemas["AnalysisResult"] | Schemas["ScenarioResult"] | Schemas["ScenarioProposal"];
const referenceFields = new Set(["source_refs", "evidence_refs", "calibration_evidence", "resource_evidence", "turnaround_evidence", "demand_evidence"]);

// Synthetic test-only grouping; existing source definitions and calculated values stay intact.
export function groupedEvidenceFixture<T extends EvidenceResult>(source: T, label = "Synthetic grouped source") {
  const value = structuredClone(source), first = value.evidence_refs[0];
  const second = { ...first, evidence_id: "synthetic-group-leaf", section: label, url: "https://example.org/group-leaf" };
  value.evidence_refs.push(second);
  const evidence_ids = [first.evidence_id, second.evidence_id].sort();
  const group_id = `evidence-group:${createHash("sha256").update(JSON.stringify(evidence_ids)).digest("hex")}`;
  const visit = (node: unknown) => {
    if (!node || typeof node !== "object") return;
    for (const [key, item] of Object.entries(node)) {
      if (referenceFields.has(key) && Array.isArray(item) && item.every((entry) => typeof entry === "string")) {
        item.forEach((id, index) => { if (id === first.evidence_id) item[index] = group_id; });
      } else if (Array.isArray(item)) item.forEach(visit);
      else visit(item);
    }
  };
  visit(value);
  value.evidence_groups = [{ group_id, evidence_ids }];
  return value;
}
