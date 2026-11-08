import React, { type ReactNode } from "react";
import { useAtlas, currentStatus } from "../features/state";
import {
  fixtures,
  categoryById,
  cellScore,
  aggregate,
  type CatId,
} from "../mocks/model";
import { Icon, Demo, Notice } from "../shared/ui";
import { css, f1, f0, roundPop } from "../shared/format";
import { mapColor } from "../shared/mapColor";
export function ResultCard({ analyze }: { analyze: () => void }) {
  const { state: s, dispatch } = useAtlas(),
    p = s.applied,
    st = currentStatus(s);
  const active = s.p;
  const close = () => dispatch({ type: "ui", patch: { cardHidden: true } });
  const head = (label: ReactNode, title: ReactNode) => (
    <div className="ctx-h">
      <div style={{ minWidth: 0 }}>
        <span className="k">{label}</span>
        <h3>{title}</h3>
      </div>
      <button className="ctx-x" aria-label="Закрыть карточку" onClick={close}>
        <Icon name="x" />
      </button>
    </div>
  );
  const kv = (title: string, value: ReactNode, wide = false) => (
    <div className="kv" style={wide ? { gridColumn: "1/-1" } : undefined}>
      <span>{title}</span>
      <b>{value}</b>
    </div>
  );
  const ask = (
    <div className="ctx-actions">
      <button
        className="iconbtn"
        onClick={() =>
          dispatch({
            type: "ui",
            patch: { chatOpen: true, collapsed: window.innerWidth < 1520 },
          })
        }
      >
        <Icon name="sparkles" />
        Спросить агента
      </button>
    </div>
  );
  let content: ReactNode;
  if (active.mode === "city") {
    const city = active.city;
    if (!city) {
      content = (
        <>
          {head("Обзор · Россия", "Городская детализация — в трёх городах")}
          <div className="ctx-b">
            <p
              style={{
                margin: 0,
                fontSize: 13.5,
                color: "var(--text-2)",
                lineHeight: 1.55,
              }}
            >
              Для остальных территорий городской индекс не рассчитывается — они
              нейтральные. Межгородские связи — в режиме «Пассажирские связи».
            </p>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 8,
                marginTop: 14,
              }}
            >
              {(["msk", "kgd", "nvr"] as const).map((k, i) => (
                <button
                  key={k}
                  className="iconbtn"
                  style={{
                    justifyContent: "space-between",
                    height: "auto",
                    minHeight: 58,
                    padding: "10px 14px",
                    textAlign: "left",
                  }}
                  onClick={() => dispatch({ type: "city", city: k })}
                >
                  <span
                    style={{ display: "flex", flexDirection: "column", gap: 2 }}
                  >
                    <b style={{ fontSize: 15 }}>{fixtures.city[k].name}</b>
                    <span
                      style={{
                        fontSize: 12,
                        color: "var(--text-3)",
                        fontWeight: 600,
                        whiteSpace: "normal",
                      }}
                    >
                      {
                        [
                          "демо-сетка 1 км · общественный транспорт, пешком, авто",
                          "демо-сетка · пример частичного результата",
                          "нет данных в прототипе · есть грузовой пример",
                        ][i]
                      }{" "}
                      · {fixtures.city[k].tz}
                    </span>
                  </span>
                  <Icon name="arrow-right" />
                </button>
              ))}
            </div>
          </div>
        </>
      );
    } else {
      const M = fixtures.cities[city];
      const blocked = ["idle", "nd", "srcerr", "baddate"].includes(st);
      if (blocked || !M) {
        content = (
          <>
            {head(fixtures.city[city].name, "Результата нет")}
            <div className="ctx-b">
              <Notice kind={st === "srcerr" ? "err" : "nd"}>
                {st === "idle"
                  ? "Расчёт ещё не выполнялся."
                  : st === "srcerr"
                    ? "Новых значений нет: источник расписаний недоступен. Это не плохая доступность."
                    : "Для выбранных условий нет подтверждённых данных — значения не показываются."}
              </Notice>
            </div>
          </>
        );
      } else {
        const scn = s.scenario === "done" && s.scnView === "scn",
          pending = st === "pending";
        const ids = Object.keys(p.cats) as CatId[];
        const focus = (
          s.metric === "overall"
            ? ids.sort((a, b) => (p.cats[b] || 0) - (p.cats[a] || 0))[0]
            : s.metric
        ) as CatId;
        const c =
          s.selection?.type === "cell"
            ? M.cells.find((c) => c.id === s.selection?.id)
            : s.selection?.type === "obj"
              ? null
              : M.canonCell;
        if (s.selection?.type === "obj" && s.selection.cat) {
          const cat = s.selection.cat;
          const served = M.cells.filter(
              (c) => c.near[cat].obj.id === s.selection?.id,
            ),
            ts = served
              .map((c) => c.values[p.travel][cat].time)
              .sort((a, b) => a - b);
          content = (
            <>
              {head(
                <>
                  Объект · {categoryById[cat].short} <Demo />
                </>,
                categoryById[cat].name,
              )}
              <div className="ctx-b">
                <p style={{ margin: 0, fontSize: 13, color: "var(--text-3)" }}>
                  Название и адрес — из справочника категории{" "}
                  {cat.toUpperCase()}. Государственное или муниципальное
                  учреждение.
                </p>
                <div className="kvs tnum">
                  {kv("Ячеек, для которых ближайший", served.length)}
                  {kv(
                    "Медиана времени до него",
                    ts.length ? `${ts[Math.floor(ts.length / 2)]} мин` : "—",
                  )}
                  {kv(
                    "Жители этих ячеек · модельная оценка",
                    `≈ ${f0(roundPop(served.reduce((a, c) => a + c.pop, 0)))}`,
                  )}
                </div>
                <Notice kind="info">
                  Ячейки, для которых объект ближайший по полному времени,
                  выделены на карте. Это транспортная доступность, а не запись к
                  врачу или прикрепление.
                </Notice>
              </div>
            </>
          );
        } else if (!c) {
          content = (
            <>
              {head(fixtures.city[city].name, "Средний индекс города")}
              <div className="ctx-b">
                <div className="bigrow">
                  <span className="bignum tnum">
                    {pending
                      ? "—"
                      : f1(aggregate(M.cells, s.metric, p.travel, p.cats, scn))}
                  </span>
                  <span className="bigcap">
                    {s.metric === "overall"
                      ? "общий индекс"
                      : categoryById[focus]?.name.toLowerCase()}
                    <br />
                    по жителям · демо
                  </span>
                </div>
                <p
                  style={{
                    fontSize: 13,
                    color: "var(--text-2)",
                    margin: "12px 0 0",
                  }}
                >
                  Город считается из точек с весами по населению, а не как
                  среднее районов.
                  {city === "kgd"
                    ? " На западе города часть ячеек без расписания — они нейтральные и не входят в среднее."
                    : ""}
                </p>
                <Notice kind="info">
                  Нажмите на ячейку, чтобы увидеть ближайший объект, время и
                  разложение пути.
                </Notice>
              </div>
            </>
          );
        } else if (c.ndPartial) {
          content = (
            <>
              {head("Ячейка 1 км", "Нет результата")}
              <div className="ctx-b">
                <Notice>
                  Для этой ячейки нет расписания на выбранную дату. Это не
                  плохая доступность: ячейка нейтральная и не входит в средние
                  значения.
                </Notice>
              </div>
            </>
          );
        } else if (!focus) {
          content = (
            <>
              {head("Ячейка 1 км", "Категории не выбраны")}
              <div className="ctx-b">
                <Notice>
                  Выберите хотя бы одну категорию — без неё ближайший объект и
                  общий индекс не вычисляются.
                </Notice>
              </div>
            </>
          );
        } else {
          const v = c.values[p.travel][focus],
            T = scn ? v.scenarioTime : v.time,
            I = scn ? v.scenarioScore : v.score,
            legs = scn ? v.scenarioLegs : v.legs;
          const transfers = legs.filter((l) => l[0] === "xfer").length,
            xw = legs
              .filter((l) => l[0] === "hot")
              .reduce((a, l) => a + l[1], 0);
          const zone = M.cells.filter((c) => c.zone);
          const zonePop = zone.reduce((a, c) => a + c.pop, 0);
          content = (
            <>
              {head(
                <>
                  Ячейка 1 км <Demo />
                </>,
                `${categoryById[focus].name}: ближайший по времени`,
              )}
              <div className="ctx-b">
                {st === "noroute" && c.zone ? (
                  <Notice kind="err">
                    В пределах известного покрытия и окна поиска маршрута нет —
                    подтверждённое отсутствие, индекс 0. Это отличается от сбоя
                    источника.
                  </Notice>
                ) : (
                  <>
                    <div className="bigrow">
                      <span className="bignum tnum">
                        {T}
                        <small>мин</small>
                      </span>
                      {pending ? (
                        <span className="badge-s nd">
                          индекс: формула не утверждена
                        </span>
                      ) : (
                        <span
                          className="idxpill tnum"
                          title="Индекс категории, демо"
                        >
                          <i style={css({ "--c": mapColor(I) })} />
                          {f1(I)}
                        </span>
                      )}
                      <span className="bigcap">
                        {p.travel === "pt"
                          ? "Общ. транспорт"
                          : p.travel === "walk"
                            ? "Пешком"
                            : "Автомобиль"}
                        {scn ? " · сценарий" : ""}
                      </span>
                    </div>
                    <div
                      className="mbar"
                      role="img"
                      aria-label={legs
                        .map((l) => `${l[2]} ${l[1]} мин`)
                        .join(", ")}
                    >
                      {legs.map(([kind, m, label], i) => (
                        <span
                          key={i}
                          className={`s ${kind}`}
                          style={css({ "--m": m })}
                          title={`${label} · ${m} мин`}
                        >
                          {m >= 4 ? m : ""}
                        </span>
                      ))}
                    </div>
                    <div className="mlist tnum">
                      {legs.map(([kind, m, label], i) => (
                        <React.Fragment key={i}>
                          <i
                            className={`k mbar-${kind}`}
                            style={{
                              background: (
                                {
                                  walk: "#8C93BD",
                                  wait: "#B4A4FF",
                                  ride: "#2F5BFF",
                                  ride2: "#7A52FF",
                                  xfer: "#5D6390",
                                  hot: "#7A52FF",
                                } as Record<string, string>
                              )[kind],
                            }}
                          />
                          <span className={kind === "hot" ? "hot" : ""}>
                            {label}
                          </span>
                          <b className={kind === "hot" ? "hot" : ""}>{m} мин</b>
                        </React.Fragment>
                      ))}
                    </div>
                    <div className="kvs tnum">
                      {kv("Пересадки", transfers)}
                      {kv(
                        "Ожидание пересадок",
                        `${T ? Math.round((xw / T) * 100) : 0} %`,
                      )}
                      {kv("По прямой до объекта", `${f1(c.near[focus].km)} км`)}
                      {kv("Жители ячейки · модель", `≈ ${f0(roundPop(c.pop))}`)}
                    </div>
                  </>
                )}
                <div className="sect">
                  <h4>Категории и приоритеты</h4>
                  {ids.length ? (
                    <>
                      <table className="ctable tnum">
                        <tbody>
                          {ids.map((id) => {
                            const v = c.values[p.travel][id];
                            return (
                              <tr key={id}>
                                <td>
                                  {categoryById[id].short}
                                  <span className="p">
                                    {["Н", "С", "В"][(p.cats[id] || 1) - 1]}
                                  </span>
                                </td>
                                <td
                                  style={{
                                    textAlign: "right",
                                    color: "var(--text-3)",
                                  }}
                                >
                                  {scn ? v.scenarioTime : v.time} мин
                                </td>
                                <td>
                                  {pending
                                    ? "—"
                                    : f1(scn ? v.scenarioScore : v.score)}
                                </td>
                              </tr>
                            );
                          })}
                          <tr>
                            <td>
                              <b>Общий индекс</b>
                            </td>
                            <td />
                            <td>
                              {pending
                                ? "—"
                                : f1(
                                    cellScore(
                                      c,
                                      "overall",
                                      p.travel,
                                      p.cats,
                                      scn,
                                    ),
                                  )}
                            </td>
                          </tr>
                        </tbody>
                      </table>
                      <p className="fhint">
                        Общий — взвешенное среднее выбранных категорий (веса
                        1/2/3). Способы передвижения не усредняются.
                      </p>
                    </>
                  ) : (
                    <Notice>
                      Категории не выбраны — общий индекс не вычисляется.
                    </Notice>
                  )}
                </div>
                {c.zone ? (
                  <div className="sect">
                    <h4>Зона пересадки</h4>
                    <div className="kvs tnum">
                      {kv(
                        "Ячеек · жителей (модель)",
                        `${zone.length} · ≈ ${f0(roundPop(zonePop))}`,
                      )}
                      {kv(
                        "Отставание от города",
                        `${f1((aggregate(zone, "c01", p.travel, p.cats, scn) || 0) - (aggregate(M.cells, "c01", p.travel, p.cats, scn) || 0))} балла`,
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="sect">
                    <h4>Город</h4>
                    <p className="fhint">
                      Среднее по городу для категории:{" "}
                      {f1(aggregate(M.cells, focus, p.travel, p.cats, scn))}{" "}
                      (демо, по жителям).
                    </p>
                  </div>
                )}
                {s.scenario === "done" && c.zone && (
                  <div className="sect">
                    <h4>Проверенный сценарий</h4>
                    <Notice kind="info">
                      Согласование пересадки:{" "}
                      {scn ? "показан сценарий" : "показана база"}.
                      Переключатель «База / Сценарий» — вверху карты.
                    </Notice>
                  </div>
                )}
                <div className="ctx-actions">
                  <button className="iconbtn primary" onClick={analyze}>
                    <Icon name="sparkles" />
                    Проанализировать
                  </button>
                </div>
                <details className="more">
                  <summary>
                    Как считается <Icon name="chevron-down" />
                  </summary>
                  <ul>
                    <li>
                      Полное время включает пешие участки, первое ожидание,
                      движение, переходы и ожидание пересадок; каждый интервал —
                      один раз.
                    </li>
                    <li>
                      Показаны заранее подготовленные демо-значения. Численная
                      методика ещё утверждается.
                    </li>
                    <li>
                      Население — модельная оценка по растру, а не жители
                      конкретного дома.
                    </li>
                    <li>
                      Источники (демо): расписание общественного транспорта,
                      справочник объектов {focus.toUpperCase()}, сетка 1 км.
                    </li>
                  </ul>
                </details>
              </div>
            </>
          );
        }
      }
    }
  } else if (active.mode === "pax") {
    const D = fixtures.passenger[`${active.paxA}>${active.paxB}`],
      modes = Object.keys(fixtures.passengerMeta).filter(
        (m) => active.paxModes[m],
      );
    const tab = modes.includes(s.paxTab) ? s.paxTab : modes[0] || "air",
      m = D?.[tab];
    const title = `${fixtures.places[active.paxA][active.paxScale === "subj" ? "subj" : "n"]} → ${fixtures.places[active.paxB][active.paxScale === "subj" ? "subj" : "n"]}`;
    content = (
      <>
        {head(
          <>
            Пассажирская связь
            {active.paxScale === "subj" ? " · через столицы" : ""} <Demo />
          </>,
          title,
        )}
        <div className="ctx-b">
          {!D ? (
            <>
              <Notice>
                Для этой пары в прототипе нет демо-расчёта. Наличие городов в
                справочнике не означает полного расписания между ними.
              </Notice>
              <p className="fhint">
                Демо-пример: Москва → Калининград и обратно.
              </p>
            </>
          ) : (
            <>
              <div
                className="mtabs"
                role="tablist"
                aria-label="Виды транспорта"
              >
                {modes.map((k) => (
                  <button
                    role="tab"
                    aria-selected={k === tab}
                    key={k}
                    onClick={() =>
                      dispatch({ type: "ui", patch: { paxTab: k } })
                    }
                  >
                    <Icon name={fixtures.passengerMeta[k][0]} />
                    {fixtures.passengerMeta[k][1]}
                    <span className={`dot ${D[k].st}`} />
                  </button>
                ))}
              </div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginTop: 12,
                }}
              >
                <b style={{ fontSize: 15 }}>
                  {fixtures.passengerMeta[tab]?.[1]}
                </b>
                <span className={`badge-s ${m?.st}`}>
                  {m?.st === "ok"
                    ? "есть"
                    : m?.st === "unk"
                      ? "неизвестно"
                      : "нет данных"}
                </span>
              </div>
              {!modes.length ? (
                <Notice>Не выбран ни один вид транспорта.</Notice>
              ) : m?.st === "ok" ? (
                <>
                  <div className="timeline-local tnum">
                    <div>
                      <b>{m.ex?.[0]}</b>
                      <span>{m.ex?.[1]}</span>
                    </div>
                    <Icon name="arrow-right" />
                    <div>
                      <b>{m.ex?.[2]}</b>
                      <span>{m.ex?.[3]}</span>
                    </div>
                  </div>
                  <div className="kvs tnum">
                    {kv("Время между узлами", m.time)}
                    {kv("Отправлений в день", m.dep)}
                    {kv("Макс. перерыв", m.gap)}
                    {kv(
                      "Прямое сообщение",
                      `${m.direct} · пересадок ${m.xfer}`,
                    )}
                    {kv(
                      "Отправлений за неделю · по расписанию всей недели",
                      m.depW,
                      true,
                    )}
                  </div>
                  <p className="fhint">
                    {m.nodes}. Кодшеринговые номера не считаются отдельными
                    рейсами. Подвоз к аэропорту не входит.
                  </p>
                </>
              ) : (
                <Notice kind={m?.st === "unk" ? "unk" : "nd"}>{m?.msg}</Notice>
              )}
            </>
          )}
          <div className="sect">
            <h4>Сводный индекс</h4>
            <Notice>
              <b style={{ color: "var(--text)" }}>Методика не утверждена.</b>{" "}
              Показаны исходные демонстрационные показатели по видам транспорта;
              сводная оценка не рассчитывается по случайным весам.
            </Notice>
          </div>
          {ask}
        </div>
      </>
    );
  } else if (active.cargoScale === "city") {
    const st = fixtures.cargo.stats,
      wh =
        fixtures.cargo.objs.find((o) => o.id === s.selection?.id) ||
        fixtures.cargo.objs[0];
    const rows = fixtures.cargo.nodes
      .map((n) => ({
        n,
        ok: n.h.includes(active.profile.handling),
        t:
          wh.t[n.id] != null
            ? wh.t[n.id] + (active.cargoDir === "in" ? 2 : 0)
            : null,
      }))
      .sort(
        (a, b) => Number(b.ok) - Number(a.ok) || (a.t || 999) - (b.t || 999),
      );
    content = (
      <>
        {head(
          <>
            Грузы · Новороссийск <Demo />
          </>,
          `${wh.name} → подходящие узлы`,
        )}
        <div className="ctx-b">
          <p style={{ margin: 0, fontSize: 13, color: "var(--text-3)" }}>
            {active.cargoDir === "out"
              ? "Отправка: склад → узел"
              : "Приём: узел → склад"}{" "}
            · профиль:{" "}
            {active.profile.group === "food"
              ? "продовольствие"
              : active.profile.group === "raw"
                ? "сырьё"
                : "непродовольственные"}
            ,{" "}
            {active.profile.handling === "container"
              ? "контейнерный"
              : active.profile.handling === "liquid"
                ? "наливной"
                : active.profile.handling === "bulk"
                  ? "навалочный"
                  : "генеральный"}
          </p>
          <table className="ctable tnum" style={{ marginTop: 10 }}>
            <tbody>
              {rows.map(({ n, ok, t }) => (
                <tr key={n.id}>
                  <td>{n.name}</td>
                  <td style={{ textAlign: "right" }}>
                    <span className={`badge-s ${ok ? "ok" : "nd"}`}>
                      {ok ? "подходит" : "не по профилю"}
                    </span>
                  </td>
                  <td>{ok && t != null ? `${t} мин` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="fhint">
            Время по грузовой дорожной сети. Ближайший по карте узел не
            считается подходящим без подтверждённой функции.
            {active.profile.limits.temp
              ? " Совместимость с температурным режимом у узлов неизвестна — требует данных."
              : ""}
          </p>
          <div className="sect">
            <h4>Город · все объекты</h4>
            <div className="kvs tnum">
              {kv(
                "С допустимым подъездом",
                `${f1((st.ok / (st.ok + st.no)) * 100)} %`,
              )}
              {kv("Определено · неизвестно", `${st.ok + st.no} · ${st.unk}`)}
            </div>
            <p className="fhint">
              Доля = {st.ok} с подъездом / {st.ok + st.no} с определённым
              результатом. {st.unk} объектов без результата показаны отдельно.
              Веса — по объектам, не по жителям или тоннам.
            </p>
          </div>
          <div className="sect">
            <h4>Сводный грузовой индекс</h4>
            <Notice>
              Формула не утверждена — показаны исходные показатели.
            </Notice>
          </div>
          {ask}
        </div>
      </>
    );
  } else {
    const modes = Object.keys(fixtures.cargoMeta).filter(
        (m) => active.cargoModes[m],
      ),
      tab = modes.includes(s.cargoTab) ? s.cargoTab : modes[0] || "road";
    const valid = ["msk>nvr", "nvr>msk"].includes(
      `${active.cargoA}>${active.cargoB}`,
    );
    const rows =
      tab === "road"
        ? [
            ["Связь подходящих узлов", "есть"],
            ["Расстояние", "1 450 км"],
            ["Время движения", "19 ч 30 мин"],
            ["Ожидание и обработка на узлах", "нет данных"],
          ]
        : tab === "rail"
          ? [
              ["Связь терминалов по профилю", "есть"],
              ["Расстояние", "1 640 км"],
              ["Грузовые отправления за период", "нет расписания"],
              ["Перегрузки", "нет данных"],
            ]
          : null;
    const notes: Record<string, string> = {
      road: "Время движения — не полное время доставки: ожидание и обработка неизвестны.",
      rail: "Без грузового расписания время доставки не выводится.",
      air: "Подтверждённого грузового аэропортового терминала у узла Б в данных нет.",
      water:
        "Водный путь между узлами пары и сезон навигации требуют проверки. Грузооборот порта не равен потоку пары.",
      pipe:
        active.profile.handling === "liquid"
          ? `Продукт: ${active.pipeProduct === "oil" ? "нефть" : active.pipeProduct === "gas" ? "газ" : "нефтепродукты"}. Подтверждённого соединения узлов приёма и сдачи нет в данных. Транзит линии через регион — не подключение. Мощность и объём — только из сведений оператора для нужной связи.`
          : "Неприменимо к профилю груза: трубопровод только для наливных продуктов. Отсутствие трубы не снижает оценку.",
    };
    content = (
      <>
        {head(
          <>
            Грузовая связь · узлы <Demo />
          </>,
          `${fixtures.places[active.cargoA].n} → ${fixtures.places[active.cargoB].n}`,
        )}
        <div className="ctx-b">
          {!valid ? (
            <Notice>
              Для этой пары в прототипе нет демо-расчёта. Демо-пример: Москва →
              Новороссийск.
            </Notice>
          ) : (
            <>
              <div
                className="mtabs"
                role="tablist"
                aria-label="Виды грузового транспорта"
              >
                {modes.map((k) => (
                  <button
                    key={k}
                    role="tab"
                    aria-selected={k === tab}
                    onClick={() =>
                      dispatch({ type: "ui", patch: { cargoTab: k } })
                    }
                  >
                    <Icon name={fixtures.cargoMeta[k][0]} />
                    {fixtures.cargoMeta[k][1]}
                    <span className={`dot ${k === "road" ? "ok" : "unk"}`} />
                  </button>
                ))}
              </div>
              {!modes.length ? (
                <Notice>Не выбран ни один вид транспорта.</Notice>
              ) : (
                <>
                  <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                    <b style={{ fontSize: 15 }}>{fixtures.cargoMeta[tab][1]}</b>
                    <span
                      className={`badge-s ${tab === "road" ? "ok" : "unk"}`}
                    >
                      {tab === "road"
                        ? "есть"
                        : tab === "pipe" && active.profile.handling !== "liquid"
                          ? "неприменимо"
                          : "неизвестно"}
                    </span>
                  </div>
                  {rows ? (
                    <>
                      <table className="ctable tnum" style={{ marginTop: 8 }}>
                        <tbody>
                          {rows.map(([k, v]) => (
                            <tr key={k}>
                              <td>{k}</td>
                              <td />
                              <td>{v}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p className="fhint">{notes[tab]}</p>
                    </>
                  ) : (
                    <Notice kind="unk">{notes[tab]}</Notice>
                  )}
                </>
              )}
            </>
          )}
          <div className="sect">
            <h4>Сводный грузовой индекс</h4>
            <Notice>
              Формула, профили и пороги срочности не утверждены — индекс не
              рассчитывается.
            </Notice>
          </div>
          {ask}
        </div>
      </>
    );
  }
  return (
    <section
      className={`ctx panelc ${s.chatOpen && !s.collapsed && window.innerWidth < 1600 ? "compact" : ""}`}
      aria-label="Выбранный объект"
    >
      {content}
    </section>
  );
}
