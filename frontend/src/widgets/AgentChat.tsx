import {
  Fragment,
  useState,
  useEffect,
  useRef,
  useImperativeHandle,
  type Ref,
  type FormEvent,
} from "react";
import { useAtlas, currentStatus } from "../features/state";
import { mockGateway } from "../mocks/gateway";
import { fixtures, aggregate } from "../mocks/model";
import { Icon, Demo } from "../shared/ui";
import { f1, f0, roundPop, timeLabel } from "../shared/format";
export interface ChatHandle {
  analyze: () => void;
}
interface Message {
  id: number;
  kind:
    | "user"
    | "pending"
    | "free"
    | "city"
    | "pax"
    | "cargo"
    | "scenario"
    | "scenariopending"
    | "wrong"
    | "unavailable";
  text?: string;
}
export function AgentChat({ handle }: { handle: Ref<ChatHandle> }) {
  const { state: s, dispatch } = useAtlas();
  const [messages, setMessages] = useState<Message[]>([]),
    [draft, setDraft] = useState(""),
    [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null),
    counter = useRef(0),
    log = useRef<HTMLDivElement>(null),
    input = useRef<HTMLTextAreaElement>(null);
  const off = currentStatus(s) === "agentoff";
  const p = s.p;
  const resultAvailable = ["ok", "partial", "agentoff"].includes(currentStatus(s));
  const scenarioAvailable = !off && resultAvailable && p.mode === "city" && p.city === "msk" && p.travel === "pt" && !!p.cats.c01;
  useEffect(() => () => abort.current?.abort(), [s.revision]);
  useEffect(() => {
    log.current?.scrollTo({
      top: log.current.scrollHeight,
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  }, [messages]);
  const ctx =
    p.mode === "city"
      ? p.city
        ? fixtures.city[p.city].name
        : "Россия"
      : `${fixtures.places[p.mode === "pax" ? p.paxA : p.cargoA].n} → ${fixtures.places[p.mode === "pax" ? p.paxB : p.cargoB].n}`;
  const suggestions =
    p.mode === "city"
      ? [
          "Почему здесь низкая доступность поликлиник?",
          "Какая часть пути занимает больше всего времени?",
          "Какие изменения можно проверить?",
        ]
      : p.mode === "pax"
        ? [
            "Как отличаются частота и время по видам транспорта?",
            "Что известно о железнодорожной связи?",
            "Какие данные нужны для оценки?",
          ]
        : [
            "Какие узлы подходят этому типу груза?",
            "Что известно о трубопроводной связи?",
            "Почему время движения — не доставка?",
          ];
  const append = (kind: Message["kind"], text?: string) => {
    const id = ++counter.current;
    setMessages((m) => [...m, { id, kind, text }]);
    return id;
  };
  const answer = async (question?: string) => {
    if (busy || off) return;
    const text = question ?? draft.trim();
    if (!text) return;
    if (question == null) setDraft("");
    append("user", text);
    setBusy(true);
    const id = append("pending");
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    try {
      await mockGateway.analyze(controller.signal);
      const known =
        suggestions.includes(text) ||
        text === "Проанализировать выбранную зону";
      const kind: Message["kind"] = !known
        ? "free"
        : !resultAvailable
          ? "unavailable"
          : p.mode === "pax" && (!p.paxModes.air || !fixtures.passenger[`${p.paxA}>${p.paxB}`])
            ? "unavailable"
        : p.mode === "city"
          ? p.city === "msk" && p.travel === "pt" && p.cats.c01
            ? "city"
            : "wrong"
          : p.mode === "pax"
            ? "pax"
            : "cargo";
      if (controller.signal.aborted) return;
      setMessages((m) => m.map((x) => (x.id === id ? { ...x, kind } : x)));
    } catch (error) {
      setMessages((m) => m.filter((x) => x.id !== id));
      if (!(error instanceof DOMException && error.name === "AbortError"))
        setDraft(text);
    } finally {
      if (abort.current === controller) setBusy(false);
    }
  };
  useImperativeHandle(handle, () => ({
    analyze: () => {
      dispatch({
        type: "ui",
        patch: { chatOpen: true, collapsed: window.innerWidth < 1520 },
      });
      void answer("Проанализировать выбранную зону");
    },
  }));
  const scenario = async () => {
    if (
      s.scenario !== "none" ||
      busy ||
      !scenarioAvailable
    )
      return;
    setBusy(true);
    dispatch({ type: "ui", patch: { scenario: "run" } });
    const id = append("scenariopending");
    const controller = new AbortController();
    abort.current = controller;
    try {
      await mockGateway.scenario(controller.signal);
      if (controller.signal.aborted) return;
      dispatch({ type: "ui", patch: { scenario: "done", scnView: "scn" } });
      setMessages((m) =>
        m.map((x) => (x.id === id ? { ...x, kind: "scenario" } : x)),
      );
    } catch (error) {
      setMessages((m) => m.filter((x) => x.id !== id));
      if (!(error instanceof DOMException && error.name === "AbortError"))
        dispatch({ type: "ui", patch: { scenario: "none" } });
    } finally {
      if (abort.current === controller) setBusy(false);
    }
  };
  const setChat = (open: boolean) =>
    dispatch({
      type: "ui",
      patch: {
        chatOpen: open,
        ...(window.innerWidth < 1520 ? { collapsed: open } : {}),
      },
    });
  const map = () => {
    const id = fixtures.cities.msk?.canonCell?.id;
    if (id)
      dispatch({
        type: "ui",
        patch: { selection: { type: "cell", id }, cardHidden: false },
      });
  };
  const M = fixtures.cities.msk!,
    zone = M.cells.filter((c) => c.zone),
    pop = zone.reduce((a, c) => a + c.pop, 0),
    up = M.cells.filter((c) => c.eff === "up"),
    dn = M.cells.filter((c) => c.eff === "dn");
  const actions = (
    <div className="acts">
      <button onClick={map}>
        <Icon name="map" />
        Показать на карте
      </button>
      <button
        onClick={() => dispatch({ type: "ui", patch: { cardHidden: false } })}
      >
        <Icon name="panel-right-open" />
        Подробнее
      </button>
    </div>
  );
  const render = (m: Message) => {
    if (m.kind === "user") return <div className="msg u">{m.text}</div>;
    if (m.kind === "pending" || m.kind === "scenariopending")
      return (
        <div className="msg a">
          <div className="steps-run">
            <div>
              <Icon name="loader-circle" className="spin" />
              {m.kind === "pending"
                ? "Смотрю результат расчёта для выбранной зоны"
                : "Проверяю сценарий на тех же точках, жителях и условиях"}
            </div>
          </div>
          {m.kind === "scenariopending" && (
            <div className="progress">
              <i />
            </div>
          )}
        </div>
      );
    if (m.kind === "free")
      return (
        <div className="msg a">
          <span className="demo-tag">прототип</span>
          <p className="lead" style={{ marginTop: 8 }}>
            В прототипе агент не подключён к модели.
          </p>
          <p>
            Свободные вопросы не обрабатываются, чтобы не выдавать выдуманный
            ответ за настоящий. Доступны демо-ответы на вопросы-подсказки.
          </p>
          <div className="acts">
            <button onClick={() => void answer(suggestions[0])}>
              <Icon name="play" />
              Показать пример диалога
            </button>
          </div>
        </div>
      );
    if (m.kind === "unavailable") return <div className="msg a">
      <Demo /><p className="lead">Для выбранных условий нет готового демо-результата.</p>
      <p>Дождитесь расчёта или вернитесь к поддерживаемому примеру. Агент не может подтвердить числа и эффект сценария без результата для этих условий.</p>
    </div>;
    if (m.kind === "wrong")
      return (
        <div className="msg a">
          <Demo />
          <p className="lead" style={{ marginTop: 8 }}>
            Демо-диалог подготовлен для Москвы и общественного транспорта.
          </p>
          <p>
            Включите категорию «Взрослые поликлиники» — там есть пример зоны с
            долгим ожиданием пересадки.
          </p>
          <div className="acts">
            <button onClick={() => dispatch({ type: "city", city: "msk" })}>
              <Icon name="map-pin" />
              Открыть Москву
            </button>
          </div>
        </div>
      );
    if (m.kind === "city")
      return (
        <div className="msg a">
          <span className="demo-tag">демо-диалог</span>
          <p className="lead" style={{ marginTop: 8 }}>
            Главная причина — ожидание пересадки: 11 из 44 минут пути.
          </p>
          <p>
            В этой зоне низкая доступность взрослых поликлиник. Здесь проживает
            примерно {f0(roundPop(pop))} человек (модельная оценка,{" "}
            {zone.length} ячеек). До ближайшей поликлиники 2,4 км по прямой, но
            полный путь — 44 мин: пешком 10, ожидание автобуса 4, автобус 9,
            переход 2, ожидание пересадки 11, трамвай 8.
          </p>
          <div className="facts tnum">
            <div>
              <span>Индекс категории · демо</span>
              <b>36,2</b>
            </div>
            <div>
              <span>Отставание зоны от города</span>
              <b>
                {f1(
                  (aggregate(zone, "c01", "pt", p.cats, false) || 0) -
                    (aggregate(M.cells, "c01", "pt", p.cats, false) || 0),
                )}
              </b>
            </div>
            <div>
              <span>Ожидание пересадок</span>
              <b>25 %</b>
            </div>
            <div>
              <span>Пересадки</span>
              <b>1</b>
            </div>
          </div>
          <details className="src">
            <summary>
              <Icon name="book-open" />
              Источники и условия
            </summary>
            <ul>
              <li>
                Расписание общественного транспорта — демо-набор на 10 ноября
                2026, 08:30
              </li>
              <li>
                Справочник C01: государственные и муниципальные поликлиники
                (пример)
              </li>
              <li>Население: модельная сетка 1 км</li>
              <li>Методика не утверждена; значения из прототипа</li>
            </ul>
          </details>
          {actions}
          <div className="proposal">
            <span className="pk">Предложение · можно проверить</span>
            <b>Согласовать отправления на пересадке автобус → трамвай</b>
            <p>
              До проверки эффект неизвестен. Пересчёт выполнит расчётный модуль
              на тех же точках и жителях; параметры сценария не редактируются.
            </p>
            <div className="acts" style={{ marginTop: 8 }}>
              <button
                className="go"
                disabled={s.scenario !== "none" || busy || !scenarioAvailable}
                onClick={() => void scenario()}
              >
                <Icon name="flask-conical" />
                {s.scenario === "done"
                  ? "Сценарий проверен"
                  : "Проверить сценарий"}
              </button>
            </div>
          </div>
        </div>
      );
    if (m.kind === "scenario")
      return (
        <div className="msg a">
          <span className="demo-tag">результат проверки · демо</span>
          <p className="lead" style={{ marginTop: 8 }}>
            В модели время до поликлиники изменилось с 44 до 36 минут, индекс —
            с 36,2 до 43,5.
          </p>
          <div className="scn">
            <div className="cmp tnum">
              <div>
                <span>База</span>
                <b>44 мин</b>
              </div>
              <Icon name="arrow-right" />
              <div>
                <span>Сценарий</span>
                <b>36 мин</b>
              </div>
            </div>
            <div className="eff tnum">
              <div className="up">
                <span>Улучшилось</span>
                <b>
                  {up.length} ячеек · ≈{" "}
                  {f0(roundPop(up.reduce((a, c) => a + c.pop, 0)))} жит.
                </b>
              </div>
              <div className="dn">
                <span>Ухудшилось</span>
                <b>
                  {dn.length} ячейки · ≈{" "}
                  {f0(roundPop(dn.reduce((a, c) => a + c.pop, 0)))} жит.
                </b>
              </div>
              <div style={{ gridColumn: "1/-1" }}>
                <span>Индекс зоны · по жителям</span>
                <b>
                  {f1(aggregate(zone, "c01", "pt", p.cats, false))} →{" "}
                  {f1(aggregate(zone, "c01", "pt", p.cats, true))}
                </b>
              </div>
            </div>
          </div>
          <p>
            Ухудшение — у двух ячеек рядом: сдвиг отправления удлиняет их
            пересадку на 3 мин. Жители пересекающихся зон не суммируются дважды.
            Оборот транспорта и выпуск на линию — допущения сценария, их нужно
            проверить у перевозчика.
          </p>
          {actions}
        </div>
      );
    return (
      <div className="msg a">
        <span className="demo-tag">демо-диалог</span>
        <p className="lead" style={{ marginTop: 8 }}>
          {m.kind === "pax"
            ? "Подтверждена только авиасвязь в демонстрационном наборе."
            : "Совместимость узла зависит от профиля груза."}
        </p>
        <p>
          {m.kind === "pax"
            ? "Самолёт: 2 ч 10 мин между аэропортами. Для поезда и автомобиля нужны подтверждённые условия транзита через иностранную территорию. Отсутствие расписания не означает отсутствие связи."
            : "Грузооборот порта не равен потоку выбранной пары. Время движения не равно полной доставке: обработка и ожидание требуют отдельных данных. Транзит трубопровода через регион не доказывает подключение."}
        </p>
        <details className="src">
          <summary>
            <Icon name="book-open" />
            Источники и условия
          </summary>
          <p>
            Заранее подготовленный демонстрационный набор, не сведения реального
            перевозчика.
          </p>
        </details>
        <div className="acts"><button onClick={() => dispatch({ type: "ui", patch: { cardHidden: false } })}>
          <Icon name="panel-right-open" />Подробнее
        </button></div>
      </div>
    );
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void answer();
  };
  return (
    <aside
      className={`at-chat ${s.chatOpen ? "m-open" : "is-closed"}`}
      id="chat"
      aria-label="Агент Waydex"
      inert={!s.chatOpen}
    >
      <div className="ch-in">
        <div className="ch-head">
          <span className="ch-ava">
            <Icon name="sparkles" />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <b>Агент Waydex</b>
            <span>Объясняет по результатам расчёта · демо-режим</span>
          </div>
          <button
            className="ctx-x"
            aria-label="Новый диалог"
            onClick={() => {
              abort.current?.abort();
              setMessages([]);
              setBusy(false);
              dispatch({
                type: "ui",
                patch: { scenario: s.scenario === "done" ? "done" : "none" },
              });
            }}
          >
            <Icon name="square-pen" />
          </button>
          <button
            className="ctx-x"
            aria-label="Свернуть чат"
            onClick={() => setChat(false)}
          >
            <Icon name="panel-right-close" />
          </button>
        </div>
        <details className="ch-ctx">
          <summary>
            <Icon name="crosshair" />
            <span className="t">
              {ctx} ·{" "}
              {p.mode === "city"
                ? "В городе"
                : p.mode === "pax"
                  ? "Пассажиры"
                  : "Грузы"}
            </span>
            <Icon name="chevron-down" />
          </summary>
          <dl>
            <dt>Контекст</dt>
            <dd>{ctx}</dd>
            <dt>Условия</dt>
            <dd>
              {p.date} · {timeLabel(p.t)} · демо
            </dd>
            <dt>Результат</dt>
            <dd>
              {s.status === "stale"
                ? "Для предыдущих условий"
                : "Демонстрационный набор"}
            </dd>
          </dl>
        </details>
        <div className="ch-log" ref={log} aria-live="polite">
          {!messages.length && (
            <div className="ch-empty">
              <h3>Спросите о выбранной области</h3>
              <p>
                Агент видит контекст выше: режим, территорию, условия и текущий
                результат. Он объясняет причину и предлагает сценарий, который
                можно проверить отдельно.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <Fragment key={m.id}>{render(m)}</Fragment>
          ))}
          {off && (
            <div className="ch-err" role="alert">
              <b>
                <Icon name="bot-off" /> Агент временно недоступен
              </b>
              <span>
                Карта, фильтры и сохранённый результат продолжают работать.
                Попробуйте ещё раз позже.
              </span>
              <div>
                <button
                  className="iconbtn"
                  onClick={() =>
                    dispatch({ type: "ui", patch: { demoState: null } })
                  }
                >
                  <Icon name="refresh-cw" />
                  Повторить
                </button>
              </div>
            </div>
          )}
        </div>
        <div className="ch-foot">
          {!messages.length && (
            <div className="sugs">
              {suggestions.map((q) => (
                <button
                  className="sug"
                  key={q}
                  onClick={() => {
                    setDraft(q);
                    input.current?.focus();
                  }}
                >
                  <Icon name="message-circle-question" />
                  {q}
                </button>
              ))}
            </div>
          )}
          <form className="composer" onSubmit={submit}>
            <label htmlFor="chatInput" className="visually-hidden">
              Вопрос агенту
            </label>
            <textarea
              ref={input}
              id="chatInput"
              rows={1}
              value={draft}
              disabled={off || busy}
              onChange={(e) => {
                setDraft(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height =
                  Math.min(120, e.target.scrollHeight) + "px";
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void answer();
                }
              }}
              placeholder="Спросите о выбранной области или связи"
            />
            <button
              className="send"
              type="submit"
              aria-label="Отправить"
              disabled={off || busy || !draft.trim()}
            >
              <Icon name="arrow-up" />
            </button>
          </form>
          <p className="ch-note">
            Числа берутся только из результата расчёта. Прототип: ответы —
            заранее подготовленный демо-диалог.
          </p>
        </div>
      </div>
    </aside>
  );
}
