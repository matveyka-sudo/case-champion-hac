import { Fragment, type ReactNode, type RefObject } from "react";
import { Icon } from "../shared/ui";
export function AgentChatView({ open, onClose, onNewDialog, subtitle, contextLabel, contextRows, messages, hasMessages,
  error, suggestions, onSuggestion, draft, onDraftChange, onSend, disabled, busy, note, logRef, inputRef, maxLength, notice }: {
  open: boolean; onClose: () => void; onNewDialog: () => void; subtitle: string; contextLabel: ReactNode;
  contextRows: { label: string; value: ReactNode }[]; messages: ReactNode; hasMessages: boolean; error?: ReactNode;
  suggestions: string[]; onSuggestion: (question: string) => void; draft: string; onDraftChange: (value: string) => void;
  onSend: () => void; disabled: boolean; busy: boolean; note: string;
  logRef?: RefObject<HTMLDivElement | null>; inputRef?: RefObject<HTMLTextAreaElement | null>;
  maxLength?: number;
  notice?: ReactNode;
}) {
  const send = () => { if (!disabled && !busy && draft.trim() && (maxLength === undefined || draft.length <= maxLength)) onSend(); };
  return <aside className={`at-chat ${open ? "m-open" : "is-closed"}`} id="chat" aria-label="Агент Waydex" inert={!open}>
    <div className="ch-in">
      <div className="ch-head"><span className="ch-ava"><Icon name="sparkles" /></span>
        <div style={{ flex: 1, minWidth: 0 }}><b>Агент Waydex</b><span>{subtitle}</span></div>
        <button className="ctx-x" aria-label="Новый диалог" onClick={onNewDialog}><Icon name="square-pen" /></button>
        <button className="ctx-x" aria-label="Свернуть чат" onClick={onClose}><Icon name="panel-right-close" /></button>
      </div>
      <details className="ch-ctx"><summary><Icon name="crosshair" /><span className="t">{contextLabel}</span><Icon name="chevron-down" /></summary>
        <dl>{contextRows.map((row, index) => <Fragment key={index}><dt>{row.label}</dt><dd>{row.value}</dd></Fragment>)}</dl>
      </details>
      <div className="ch-log" ref={logRef} aria-live="polite">
        {notice && <div className="stmsg unk" role="note"><strong>{notice}</strong></div>}
        {!hasMessages && <div className="ch-empty"><h3>Спросите о выбранной области</h3><p>Агент видит контекст выше: режим, территорию, условия и текущий результат. Он объясняет причину и предлагает сценарий, который можно проверить отдельно.</p></div>}
        {messages}{error}
      </div>
      <div className="ch-foot">
        {!hasMessages && <div className="sugs">{suggestions.map((question) => <button className="sug" key={question} onClick={() => onSuggestion(question)}><Icon name="message-circle-question" />{question}</button>)}</div>}
        <form className="composer" onSubmit={(event) => { event.preventDefault(); send(); }}>
          <label htmlFor="chatInput" className="visually-hidden">Вопрос агенту</label>
          <textarea ref={inputRef} id="chatInput" rows={1} value={draft} maxLength={maxLength} disabled={disabled || busy}
            onChange={(event) => { onDraftChange(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(120, event.target.scrollHeight)}px`; }}
            onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }}
            placeholder="Спросите о выбранной области или связи" />
          <button className="send" type="submit" aria-label="Отправить" disabled={disabled || busy || !draft.trim()}><Icon name="arrow-up" /></button>
        </form><p className="ch-note">{note}</p>
      </div>
    </div>
  </aside>;
}
