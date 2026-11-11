import { useEffect, useRef, useState } from "react";
import { BackendGateway, backend } from "../../api/gateway";
import { ApiError } from "../../api/client";
import { parseSavedPointer } from "./savedPointer";
import type {
  AnalysisRequest,
  AnalysisResult,
  Schemas,
  Selection,
} from "../../api/types";
function canonicalRequest(request: AnalysisRequest) {
  const sort = (value: unknown): unknown => Array.isArray(value) ? value.map(sort) : value && typeof value === "object"
    ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, child]) => [key, sort(child)])) : value;
  return JSON.stringify(sort(request));
}
export interface ChatMessage {
  question: string;
  answer: Schemas["Explanation"];
}
export interface RestoredSnapshot { result: AnalysisResult; scenario: Schemas["ScenarioResult"] | null }
export function useAnalysisSession(gateway: BackendGateway = backend) {
  const [result, setResult] = useState<AnalysisResult | null>(null),
    [scenario, setScenario] = useState<Schemas["ScenarioResult"] | null>(null),
    [proposals, setProposals] = useState<Schemas["ScenarioProposal"][]>([]),
    [job, setJob] = useState<
      Schemas["JobAccepted"] | Schemas["JobView"] | null
    >(null),
    [phase, setPhase] = useState("idle"),
    [error, setError] = useState<Error | null>(null),
    [explanationError, setExplanationError] = useState<Error | null>(null),
    [warning, setWarning] = useState(""),
    [messages, setMessages] = useState<ChatMessage[]>([]),
    [chatBusy, setChatBusy] = useState(false),
    [dirty, setDirty] = useState(false);
  const revision = useRef(0),
    active = useRef<AbortController | null>(null),
    chat = useRef<AbortController | null>(null),
    currentResult = useRef<AnalysisResult | null>(null),
    proposalAbort = useRef<AbortController | null>(null),
    busy = useRef(false),
    pendingSubmission = useRef<{ fingerprint: string; key: string } | null>(null);
  useEffect(
    () => () => {
      active.current?.abort();
      chat.current?.abort();
      proposalAbort.current?.abort();
    },
    [],
  );
  const changed = () => {
    revision.current++;
    pendingSubmission.current = null;
    active.current?.abort();
    chat.current?.abort();
    proposalAbort.current?.abort();
    busy.current = false;
    setJob(null);
    setPhase("idle");
    setDirty(!!currentResult.current);
    setScenario(null);
    setProposals([]);
    setMessages([]);
    setChatBusy(false);
    setError(null);
    setExplanationError(null);
    setWarning("");
  };
  const reset = () => {
    changed();
    currentResult.current = null;
    setResult(null);
    setDirty(false);
  };
  const loadProposals = (value: AnalysisResult) => {
    const controller = new AbortController();
    proposalAbort.current?.abort();
    proposalAbort.current = controller;
    const rev = revision.current;
    void gateway
      .proposals(value.analysis_id, controller.signal)
      .then((items) => {
        if (rev === revision.current && !controller.signal.aborted) {
          setProposals(
            items.filter(
              (p) =>
                p.baseline_analysis_id === value.analysis_id &&
                p.baseline_result_hash === value.result_hash,
            ),
          );
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted && rev === revision.current)
          setWarning(
            "Предложения сценариев недоступны. " +
              (e instanceof Error ? e.message : ""),
          );
      });
  };
  const run = async (request: AnalysisRequest, navigate?: (kind: "job" | "analysis", id: string) => boolean | void) => {
    if (busy.current) return;
    busy.current = true;
    active.current?.abort();
    setJob(null);
    chat.current?.abort();
    setChatBusy(false);
    setExplanationError(null);
    setMessages([]);
    setScenario(null);
    setProposals([]);
    setWarning("");
    proposalAbort.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const rev = revision.current;
    setPhase("running");
    setError(null);
    const fingerprint = canonicalRequest(request);
    if (pendingSubmission.current?.fingerprint !== fingerprint)
      pendingSubmission.current = { fingerprint, key: crypto.randomUUID() };
    const submission = pendingSubmission.current;
    let terminalFailure = false;
    let accepted = false;
    try {
      const value = await gateway.analyze(
        request,
        submission.key,
        controller.signal,
        (j) => {
          if (rev === revision.current && !controller.signal.aborted) {
            if (!accepted) {
              accepted = true;
              if (navigate?.("job", j.job_id) === false) { controller.abort(); return; }
            }
            terminalFailure = j.status === "failed" || j.status === "cancelled";
            setJob(j);
          }
        },
      );
      if (rev !== revision.current || controller.signal.aborted) return;
      pendingSubmission.current = null;
      if (!value) {
        setPhase("cancelled");
        return;
      }
      if (navigate?.("analysis", value.analysis_id) === false) { controller.abort(); return; }
      currentResult.current = value;
      setResult(value);
      setDirty(false);
      setPhase("done");
      loadProposals(value);
    } catch (e) {
      if (!controller.signal.aborted && rev === revision.current) {
        const recoverable = e instanceof ApiError && (e.retryable || e.code === "network_error" || e.code === "RUNTIME_INPUTS_BUSY");
        if (terminalFailure || !recoverable) pendingSubmission.current = null;
        setError(e instanceof Error ? e : new Error("Ошибка расчёта"));
        setPhase("failed");
      }
    } finally {
      if (active.current === controller) {
        busy.current = false;
      }
    }
  };
  const restore = async (hash: string, isCurrent = () => true): Promise<RestoredSnapshot | null> => {
    reset();
    const rev = revision.current;
    const controller = new AbortController();
    active.current = controller;
    busy.current = true;
    const current = () => rev === revision.current && !controller.signal.aborted && isCurrent();
    try {
      const pointer = parseSavedPointer(hash);
      if (!pointer) return null;
      setPhase("running");
      let id = pointer.id;
      const scenarioPointer = pointer.kind === "scenario" || pointer.kind === "scenario_job";
      if (pointer.kind === "job" || pointer.kind === "scenario_job") {
        const initial = await gateway.job(id, controller.signal);
        if (initial.job_id !== id) throw new ApiError(0, "invalid_response", "Ответ относится к другому заданию");
        const completed = await gateway.wait(initial, controller.signal, (j) => {
          if (current()) setJob(j);
        });
        if (!current()) return null;
        if (completed.status === "cancelled") { setPhase("cancelled"); return null; }
        if (scenarioPointer ? !completed.scenario_run_id || !!completed.analysis_id : !completed.analysis_id || !!completed.scenario_run_id)
          throw new ApiError(0, "invalid_saved_pointer", scenarioPointer ? "Эта ссылка не относится к заданию сценария." : "Эта ссылка не относится к заданию анализа.");
        id = (scenarioPointer ? completed.scenario_run_id : completed.analysis_id)!;
      }
      let scenarioValue: Schemas["ScenarioResult"] | null = null;
      if (scenarioPointer) {
        scenarioValue = await gateway.scenarioResult(id, controller.signal);
        if (!current()) return null;
        if (scenarioValue.run_id !== id) throw new ApiError(0, "invalid_response", "Ответ относится к другому сценарию");
        id = scenarioValue.baseline_analysis_id;
      }
      const value = await gateway.result(id, controller.signal);
      if (!current()) return null;
      if (value.analysis_id !== id) throw new ApiError(0, "invalid_response", "Ответ относится к другому анализу");
      if (scenarioValue && scenarioValue.baseline_result_hash !== value.result_hash)
        throw new ApiError(409, "baseline_mismatch", "Сценарий относится к другой базе");
      currentResult.current = value;
      setResult(value);
      setScenario(scenarioValue);
      setDirty(false);
      setPhase("done");
      loadProposals(value);
      return { result: value, scenario: scenarioValue };
    } catch (e) {
      if (current()) {
        const unavailable = e instanceof ApiError && [404, 410].includes(e.status);
        setError(unavailable ? new ApiError(e.status, e.code, e.status === 410
          ? "Срок доступа к сохранённому расчёту истёк."
          : "Сохранённый расчёт недоступен в этой сессии.") : e instanceof Error ? e : new Error("Не удалось открыть сохранённый расчёт"));
        setPhase("failed");
      }
      return null;
    } finally {
      if (active.current === controller) busy.current = false;
    }
  };
  const runScenario = async (proposal: Schemas["ScenarioProposal"], navigate?: (kind: "scenario_job" | "scenario", id: string) => boolean | void) => {
    const base = currentResult.current;
    if (
      busy.current ||
      dirty ||
      !base ||
      !proposal.editable_local_graph ||
      proposal.baseline_analysis_id !== base.analysis_id ||
      proposal.baseline_result_hash !== base.result_hash
    )
      return;
    busy.current = true;
    active.current?.abort();
    setJob(null);
    const controller = new AbortController();
    active.current = controller;
    const rev = revision.current;
    setPhase("running");
    setError(null);
    try {
      const accepted = await gateway.scenario(
        proposal.scenario_id,
        crypto.randomUUID(),
        controller.signal,
      );
      if (controller.signal.aborted || rev !== revision.current) return;
      if (navigate?.("scenario_job", accepted.job_id) === false) { controller.abort(); return; }
      const j = await gateway.wait(accepted, controller.signal, (j) => {
        if (rev === revision.current && !controller.signal.aborted) setJob(j);
      });
      if (controller.signal.aborted || rev !== revision.current) return;
      if (j.status === "cancelled") {
        setPhase("cancelled");
        return;
      }
      if (!j.scenario_run_id) {
        setError(
          new ApiError(
            0,
            "invalid_response",
            "Задание не содержит идентификатор сценария",
          ),
        );
        setPhase("failed");
        return;
      }
      const value = await gateway.scenarioResult(
        j.scenario_run_id,
        controller.signal,
      );
      if (controller.signal.aborted || rev !== revision.current) return;
      if (
        value.run_id !== j.scenario_run_id ||
        value.baseline_analysis_id !== base.analysis_id ||
        value.baseline_result_hash !== base.result_hash
      ) {
        setError(
          new ApiError(
            409,
            "baseline_mismatch",
            "Сценарий относится к другой базе",
          ),
        );
        setPhase("failed");
        return;
      }
      if (navigate?.("scenario", value.run_id) === false) { controller.abort(); return; }
      setScenario(value);
      setPhase("done");
    } catch (e) {
      if (!controller.signal.aborted && rev === revision.current) {
        setError(e instanceof Error ? e : new Error("Ошибка сценария"));
        setPhase("failed");
      }
    } finally {
      if (active.current === controller) busy.current = false;
    }
  };
  const cancel = async () => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const rev = revision.current, controller = active.current;
    try {
      const value = await gateway.cancel(job.job_id);
      if (rev !== revision.current || active.current !== controller) return;
      setJob(value);
      if (value.status === "cancelled") {
        pendingSubmission.current = null;
        active.current?.abort();
        busy.current = false;
        setPhase("cancelled");
      }
    } catch (e) {
      if (rev === revision.current && active.current === controller)
        setError(e instanceof Error ? e : new Error("Не удалось отменить"));
    }
  };
  const clearExplanation = () => {
    chat.current?.abort();
    setMessages([]);
    setChatBusy(false);
    setExplanationError(null);
  };
  const explain = async (question: string, selection: Selection | null) => {
    const base = currentResult.current;
    const q = question.trim();
    if (!base || !q || q.length > 2000 || chatBusy) return;
    chat.current?.abort();
    const controller = new AbortController();
    chat.current = controller;
    const rev = revision.current;
    setChatBusy(true);
    setExplanationError(null);
    try {
      const answer = await gateway.explain(
        base.analysis_id,
        {
          schema_version: "2",
          question: q,
          ...(selection ? { selection } : {}),
        },
        controller.signal,
      );
      if (controller.signal.aborted || rev !== revision.current) return;
      if (
        answer.analysis_id !== base.analysis_id ||
        answer.analysis_result_hash !== base.result_hash
      ) {
        setExplanationError(
          new ApiError(
            409,
            "analysis_mismatch",
            "Объяснение относится к другому результату",
          ),
        );
        return;
      }
      setMessages((m) => [...m, { question: q, answer }]);
    } catch (e) {
      if (!controller.signal.aborted && rev === revision.current)
        setExplanationError(e instanceof Error ? e : new Error("Объяснение недоступно"));
    } finally {
      if (chat.current === controller) setChatBusy(false);
    }
  };
  return {
    result,
    scenario,
    proposals,
    job,
    phase,
    error,
    explanationError,
    warning,
    messages,
    chatBusy,
    dirty,
    changed,
    reset,
    run,
    restore,
    runScenario,
    cancel,
    explain,
    clearExplanation,
  };
}
