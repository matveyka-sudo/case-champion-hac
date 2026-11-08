import { Select } from "../shared/Select";
import { categories, fixtures, type CatId, type CityId } from "../mocks/model";
import { useAtlas, type Params } from "../features/state";
import { Icon } from "../shared/ui";

export function Seg({
  name,
  items,
  value,
  onChange,
  stack = false,
}: {
  name: string;
  items: readonly (readonly [string, string, string?, boolean?])[];
  value: string | null;
  onChange: (v: string) => void;
  stack?: boolean;
}) {
  return (
    <div
      className={`segc ${stack ? "stack" : ""}`}
      role="group"
      aria-label={name}
    >
      {items.map(([v, label, icon, disabled]) => (
        <button
          key={v}
          type="button"
          disabled={disabled}
          aria-pressed={v === value}
          onClick={() => onChange(v)}
        >
          {icon && <Icon name={icon} />}
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}
const cityItems = [
  ["msk", "Москва"],
  ["kgd", "Калининград"],
  ["nvr", "Новороссийск"],
] as const;
export function FiltersPanel({ run }: { run: () => void }) {
  const { state: s, dispatch } = useAtlas(),
    p = s.p;
  const set = (patch: Partial<Params>) => dispatch({ type: "params", patch });
  const field = (title: string, children: React.ReactNode, hint?: string) => (
    <div className="fgroup">
      <p className="flabel">{title}</p>
      {children}
      {hint && <p className="fhint">{hint}</p>}
    </div>
  );
  const chips = (
    items: string[][],
    values: Record<string, boolean>,
    onChange: (v: string) => void,
  ) => (
    <div className="chipset">
      {items.map(([v, n, i]) => (
        <button
          key={v}
          type="button"
          className="fchip"
          aria-pressed={values[v]}
          onClick={() => onChange(v)}
        >
          {i && <Icon name={i} />}
          {n}
        </button>
      ))}
    </div>
  );
  const pair = (kind: "pax" | "cargo") => {
    const a = kind === "pax" ? p.paxA : p.cargoA,
      b = kind === "pax" ? p.paxB : p.cargoB;
    const change = (side: "A" | "B", v: string) => {
      let nextA = side === "A" ? v : a,
        nextB = side === "B" ? v : b;
      if (nextA === nextB) {
        if (side === "A") nextB = v === "kgd" ? "msk" : "kgd";
        else nextA = v === "kgd" ? "msk" : "kgd";
      }
      set(
        kind === "pax"
          ? { paxA: nextA, paxB: nextB }
          : { cargoA: nextA, cargoB: nextB },
      );
    };
    return (
      <div className="pair">
        {(["A", "B"] as const).map((side, i) => (
          <div className="fsel" key={side}>
            <span className="k">{i ? "Б" : "А"}</span>
            <Select
              label={`Территория ${i ? "Б" : "А"}`}
              value={i ? b : a}
              onChange={(v) => change(side, v)}
              options={Object.entries(fixtures.places).map(([value, v]) => ({
                value,
                label: kind === "pax" && p.paxScale === "subj" ? v.subj : v.n,
              }))}
            />
          </div>
        ))}
        <button
          className="swap"
          aria-label="Поменять направление"
          onClick={() =>
            set(
              kind === "pax" ? { paxA: b, paxB: a } : { cargoA: b, cargoB: a },
            )
          }
        >
          <Icon name="arrow-up-down" />
        </button>
      </div>
    );
  };
  const toggle = (key: "paxModes" | "cargoModes", v: string) =>
    set({ [key]: { ...p[key], [v]: !p[key][v] } });
  const setProfile = (key: "group" | "handling", v: string) =>
    set({ profile: { ...p.profile, [key]: v } });
  return (
    <aside
      className={`at-filters ${s.collapsed ? "is-collapsed" : ""} ${s.mobileFilters ? "m-open" : ""}`}
      id="filters"
      aria-label="Фильтры и условия"
    >
      <div className="m-head">
        <b>Фильтры</b>
        <button
          className="ctx-x"
          aria-label="Закрыть фильтры"
          onClick={() =>
            dispatch({ type: "ui", patch: { mobileFilters: false } })
          }
        >
          <Icon name="x" />
        </button>
      </div>
      <div className="f-head">
        <h2>Условия</h2>
        <button
          className="ctx-x d-only"
          aria-label={s.collapsed ? "Развернуть фильтры" : "Свернуть фильтры"}
          aria-expanded={!s.collapsed}
          onClick={() =>
            dispatch({
              type: "ui",
              patch: {
                collapsed: !s.collapsed,
                ...(s.collapsed && window.innerWidth < 1360
                  ? { chatOpen: false }
                  : {}),
              },
            })
          }
        >
          <Icon name={s.collapsed ? "panel-left-open" : "panel-left"} />
        </button>
      </div>
      <div className="f-rail">
        {(p.mode === "city"
          ? [
              "map-pin",
              p.travel === "pt"
                ? "tram-front"
                : p.travel === "walk"
                  ? "footprints"
                  : "car-front",
              "list-checks",
            ]
          : ["sliders-horizontal"]
        ).map((i) => (
          <button
            className="zbtn"
            key={i}
            aria-label="Развернуть условия"
            onClick={() =>
              dispatch({ type: "ui", patch: { collapsed: false } })
            }
          >
            <Icon name={i} />
          </button>
        ))}
      </div>
      <div className="f-scroll">
        {p.mode === "city" && (
          <>
            {field(
              "Город",
              <Seg
                name="Город"
                items={cityItems}
                value={p.city}
                onChange={(v) => dispatch({ type: "city", city: v as CityId })}
              />,
              p.city
                ? `Часовой пояс: ${fixtures.city[p.city].tz}. Ближайший объект ищется без учёта границ районов.`
                : "Выберите город или нажмите на него на карте России.",
            )}
            {field(
              "Передвижение",
              <Seg
                name="Передвижение"
                items={[
                  ["pt", "Общ. транспорт", "tram-front"],
                  ["walk", "Пешком", "footprints"],
                  ["car", "Авто", "car-front"],
                ]}
                value={p.travel}
                onChange={(v) => set({ travel: v as Params["travel"] })}
              />,
              p.travel === "pt"
                ? "Метро и электрички входят в общественный транспорт там, где есть данные. Такси и каршеринг не учитываются."
                : p.travel === "car"
                  ? "Пробки — только если источник их поддерживает; сейчас расчёт без пробок. Поиск парковки не моделируется. Такси и каршеринг не учитываются."
                  : "Весь путь по пешеходной сети. Такси и каршеринг не учитываются.",
            )}
            <div className="fgroup">
              <p className="flabel">
                Категории{" "}
                <small>{Object.keys(p.cats).length} из 9 · приоритет</small>
              </p>
              <div role="group" aria-label="Категории объектов и приоритеты">
                {categories.map((c) => (
                  <div className={`cat ${p.cats[c.id] ? "on" : ""}`} key={c.id}>
                    <button
                      className="cat-tog"
                      aria-pressed={!!p.cats[c.id]}
                      onClick={() => {
                        const cats = { ...p.cats };
                        if (cats[c.id]) delete cats[c.id];
                        else cats[c.id] = 2;
                        set({ cats });
                        if (s.metric === c.id)
                          dispatch({
                            type: "ui",
                            patch: { metric: "overall" },
                          });
                      }}
                    >
                      <span className="box">
                        <Icon name="check" />
                      </span>
                      <span>{c.name}</span>
                    </button>
                    <div
                      className="prio"
                      role="group"
                      aria-label={`Приоритет: ${c.name}`}
                    >
                      {[1, 2, 3].map((w, i) => (
                        <button
                          key={w}
                          aria-pressed={p.cats[c.id] === w}
                          aria-label={`${["Низкий", "Средний", "Высокий"][i]} приоритет`}
                          onClick={() =>
                            set({ cats: { ...p.cats, [c.id]: w } })
                          }
                        >
                          {["Н", "С", "В"][i]}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <div className="prio-legend">
                <span>Н — низкий · 1</span>
                <span>С — средний · 2</span>
                <span>В — высокий · 3</span>
              </div>
              {!Object.keys(p.cats).length && (
                <div className="disabled-note">
                  <Icon name="info" />
                  Не выбрано ни одной категории — общий индекс не вычисляется.
                </div>
              )}
              <p className="fhint">
                Медицина и образование — государственные и муниципальные
                учреждения.
              </p>
            </div>
            <div className="fgroup">
              <p className="flabel">Цвет карты</p>
              <div className="fsel">
                <span className="k">
                  <Icon name="palette" />
                </span>
                <Select
                  label="Показатель, которым окрашена карта"
                  value={s.metric}
                  onChange={(metric) =>
                    dispatch({ type: "ui", patch: { metric } })
                  }
                  options={[
                    {
                      value: "overall",
                      label: "Общий индекс",
                      disabled: !Object.keys(p.cats).length,
                    },
                    ...Object.keys(p.cats).map((id) => ({
                      value: id,
                      label:
                        categories.find((c) => c.id === (id as CatId))?.short ||
                        id,
                    })),
                  ]}
                />
              </div>
              <p className="flabel" style={{ marginTop: 14 }}>
                Слои
              </p>
              {chips(
                [
                  ["index", "Индекс", "grid-3x3"],
                  ["objects", "Объекты", "map-pin"],
                  ["pop", "Население", "users"],
                ],
                s.layers,
                (v) =>
                  dispatch({
                    type: "ui",
                    patch: { layers: { ...s.layers, [v]: !s.layers[v] } },
                  }),
              )}
              <p className="fhint">
                Население — модельная оценка по ячейке, не число пассажиров.
              </p>
            </div>
          </>
        )}
        {p.mode === "pax" && (
          <>
            {field(
              "Масштаб",
              <Seg
                name="Масштаб"
                items={[
                  ["city", "Город — город"],
                  ["subj", "Субъект — субъект"],
                ]}
                value={p.paxScale}
                onChange={(v) => set({ paxScale: v })}
              />,
              p.paxScale === "subj"
                ? "Связь считается через административные центры субъектов — она не представляет все населённые пункты региона."
                : undefined,
            )}
            {field(
              "Направление",
              pair("pax"),
              `Время старта — по поясу отправления: ${fixtures.places[p.paxA].tz}. Прибытие подписывается местным временем.`,
            )}
            {field(
              "Виды транспорта",
              chips(
                [
                  ["air", "Самолёт", "plane"],
                  ["rail", "Поезд", "train-front"],
                  ["bus", "Автобус", "bus-front"],
                  ["car", "Автомобиль", "car-front"],
                  ["sea", "Судно и паром", "ship"],
                ],
                p.paxModes,
                (v) => toggle("paxModes", v),
              ),
              "Подвоз от дома до вокзала или аэропорта в межгородское время не входит.",
            )}
          </>
        )}
        {p.mode === "cargo" && (
          <>
            {field(
              "Масштаб",
              <Seg
                name="Масштаб"
                items={[
                  ["city", "В городе"],
                  ["inter", "Между территориями"],
                ]}
                value={p.cargoScale}
                onChange={(v) => set({ cargoScale: v })}
              />,
            )}
            {p.cargoScale === "city" ? (
              <>
                {field(
                  "Город",
                  <Seg
                    name="Город"
                    items={[
                      ["msk", "Москва", undefined, true],
                      ["kgd", "Калининград", undefined, true],
                      ["nvr", "Новороссийск"],
                    ]}
                    value="nvr"
                    onChange={() => {}}
                  />,
                  "Демо-пример подготовлен для Новороссийска.",
                )}
                {field(
                  "Направление",
                  <>
                    <Seg
                      name="Направление"
                      items={[
                        ["out", "Отправка", "arrow-up-right"],
                        ["in", "Приём", "arrow-down-left"],
                      ]}
                      value={p.cargoDir}
                      onChange={(v) => set({ cargoDir: v })}
                    />
                    <p className="fhint">
                      Объект → узел и узел → объект считаются отдельно.
                    </p>
                    <p className="flabel" style={{ marginTop: 14 }}>
                      Слои
                    </p>
                    {chips(
                      [
                        ["ent", "Предприятия", "factory"],
                        ["wh", "Склады", "warehouse"],
                        ["port", "Порты", "anchor"],
                        ["term", "Терминалы", "container"],
                        ["rail", "Грузовые станции", "train-front-tunnel"],
                      ],
                      s.cLayers,
                      (v) =>
                        dispatch({
                          type: "ui",
                          patch: {
                            cLayers: { ...s.cLayers, [v]: !s.cLayers[v] },
                          },
                        }),
                    )}
                  </>,
                )}
              </>
            ) : (
              <>
                {field(
                  "Направление",
                  pair("cargo"),
                  "Только подтверждённые грузовые узлы — не столицы субъектов.",
                )}
                {field(
                  "Виды транспорта",
                  chips(
                    [
                      ["road", "Авто", "truck"],
                      ["rail", "Ж/д", "train-front-tunnel"],
                      ["air", "Воздух", "plane-takeoff"],
                      ["water", "Вода", "anchor"],
                      ["pipe", "Трубопровод", "droplet"],
                    ],
                    p.cargoModes,
                    (v) => toggle("cargoModes", v),
                  ),
                )}
              </>
            )}
            <div className="fgroup">
              <p className="flabel">
                Профиль груза <small>утверждается</small>
              </p>
              <p className="fhint" style={{ margin: "0 0 6px" }}>
                Товарная группа
              </p>
              <Seg
                name="Товарная группа"
                items={[
                  ["food", "Продовольствие"],
                  ["nonfood", "Непрод."],
                  ["raw", "Сырьё"],
                ]}
                value={p.profile.group}
                onChange={(v) => setProfile("group", v)}
              />
              <p className="fhint" style={{ margin: "12px 0 6px" }}>
                Обработка
              </p>
              <Seg
                stack
                name="Обработка"
                items={[
                  ["container", "Контейнер"],
                  ["general", "Генеральный"],
                  ["bulk", "Навалочный"],
                  ["liquid", "Наливной"],
                ]}
                value={p.profile.handling}
                onChange={(v) => setProfile("handling", v)}
              />
              <p className="fhint" style={{ margin: "12px 0 6px" }}>
                Ограничения
              </p>
              {chips(
                [
                  ["temp", "Температурный режим"],
                  ["perish", "Скоропортящийся"],
                  ["urgent", "Срочный"],
                ],
                p.profile.limits,
                (v) =>
                  set({
                    profile: {
                      ...p.profile,
                      limits: {
                        ...p.profile.limits,
                        [v]: !p.profile.limits[v],
                      },
                    },
                  }),
              )}
              <p className="fhint">
                «Продовольствие» и «скоропортящийся» — разные признаки.
                Численные параметры профилей ещё не утверждены.
              </p>
            </div>
            {p.cargoScale === "inter" ? (
              field(
                "Продукт трубопровода",
                <>
                  <Seg
                    name="Продукт трубопровода"
                    items={[
                      [
                        "oil",
                        "Нефть",
                        undefined,
                        p.profile.handling !== "liquid" || !p.cargoModes.pipe,
                      ],
                      [
                        "oilprod",
                        "Нефтепродукты",
                        undefined,
                        p.profile.handling !== "liquid" || !p.cargoModes.pipe,
                      ],
                      [
                        "gas",
                        "Газ",
                        undefined,
                        p.profile.handling !== "liquid" || !p.cargoModes.pipe,
                      ],
                    ]}
                    value={p.pipeProduct}
                    onChange={(v) => set({ pipeProduct: v })}
                  />
                  <p className="fhint">
                    {p.profile.handling === "liquid"
                      ? "Нефть, нефтепродукты и газ считаются отдельно."
                      : "Трубопровод применим только к наливному профилю. Для текущего груза отключён — без штрафа к индексу."}
                  </p>
                </>,
              )
            ) : (
              <div className="fgroup">
                <div className="disabled-note">
                  <Icon name="info" />
                  Трубопроводы учитываются только между территориями.
                </div>
              </div>
            )}
          </>
        )}
      </div>
      <div className="f-foot">
        <button
          className="iconbtn primary runbtn"
          onClick={run}
          disabled={s.status === "run"}
        >
          <Icon name="play" />
          <span>
            {p.mode === "pax" ||
            (p.mode === "cargo" && p.cargoScale === "inter")
              ? "Проанализировать"
              : "Рассчитать"}
          </span>
        </button>
        <small>
          {p.mode === "city"
            ? "Демо-результат: вт, 10 ноября, 08:30"
            : "Расчёт пары запускается этой кнопкой"}
        </small>
      </div>
    </aside>
  );
}
