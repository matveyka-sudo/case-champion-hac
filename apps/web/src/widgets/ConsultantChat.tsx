import { useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { askConsultant, type ConsultantContext } from "../api/adapters/consultant";
import { Icon } from "../shared/ui";

interface Message { question: string; text: string; failed?: boolean }

export function ConsultantChat({ client, context, suggestions, hint }: {
  client: ApiClient;
  context: ConsultantContext;
  suggestions: string[];
  hint: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [messages, busy]);

  async function send(text: string) {
    const question = text.trim();
    if (!question || question.length > 2000 || busy) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setDraft("");
    setBusy(true);
    try {
      const answer = await askConsultant(client, question, context, controller.signal);
      setMessages((items) => [...items, { question, text: answer.text }]);
    } catch (error) {
      if (controller.signal.aborted) return;
      setMessages((items) => [...items, {
        question, failed: true,
        text: error instanceof Error ? error.message : "Консультант сейчас недоступен. Повторите вопрос.",
      }]);
    } finally {
      if (abort.current === controller) setBusy(false);
    }
  }

  return <section className="consultant" aria-label="Консультант Waydex">
    <div className="consultant__head">
      <span className="ch-ava"><Icon name="sparkles" /></span>
      <div><b>Консультант Waydex</b><span>{hint}</span></div>
      {messages.length > 0 && <button type="button" className="iconbtn" onClick={() => { abort.current?.abort(); setMessages([]); setBusy(false); }}>Новый диалог</button>}
    </div>
    <div className="consultant__log" ref={log} aria-live="polite">
      {messages.map((message, index) => <div className="consultant__pair" key={index}>
        <div className="msg u">{message.question}</div>
        <div className="msg a" role={message.failed ? "alert" : undefined}><p className="lead">{message.text}</p></div>
      </div>)}
      {busy && <p role="status" className="consultant__busy">Консультант отвечает…</p>}
    </div>
    {!messages.length && !busy && <div className="sugs">
      {suggestions.map((question) => <button type="button" className="sug" key={question} onClick={() => void send(question)}>
        <Icon name="message-circle-question" />{question}
      </button>)}
    </div>}
    <form className="composer" onSubmit={(event) => { event.preventDefault(); void send(draft); }}>
      <label htmlFor="consultant-input" className="visually-hidden">Вопрос консультанту</label>
      <textarea id="consultant-input" rows={1} value={draft} maxLength={2000} disabled={busy}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(draft); } }}
        placeholder="Спросите про адрес или район" />
      <button className="send" type="submit" aria-label="Отправить" disabled={busy || !draft.trim()}><Icon name="arrow-up" /></button>
    </form>
  </section>;
}
