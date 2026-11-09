# Независимые проверки ML1 — 2026-11-09

Актуальный результат завершения: **READY WITH EXTERNAL DEPENDENCIES**.
Первый проход ниже сохранён как история; новые oracle, benchmark и integration
gate приведены в разделе «Завершение оставшихся проверок».

Исходный HEAD: 9a2070085671bd3314e3ea8dca0e01bf406d57bc. Основной checkout
PyCharm, work/ml1. FF-проверка remote не потребовала обновления. Исходная
регрессия независимо исполнена: 872 passed / 12.41 s.

## Граница аудита

Это отчёт о выполненных проверках, а не свидетельство исчерпывающего построчного
аудита всей библиотеки. Инвентаризированы 97 Python-модулей: urban46, routing13,
scenario9, freight9, intercity8, shared6, pipeline3, comparison2, root1.
Подробно исследованы численные примитивы, reference Dijkstra, scheduled frontier,
cargo search, сценарные overlay/impact/feasibility и границы traffic evaluation.
Существующая регрессия остальных модулей выполнена; независимый повторный разбор
всех их реализаций и тестов не завершён. Нагрузочные измерения ограничены двумя
описанными ниже случаями. Полный объём запрошенного Final Independent Audit
не следует считать закрытым этим отчётом.

## Подтверждённые дефекты до исправления

1. **P1: shared cargo execution.** scenario/overlay.py менял только target job.
   Два задания с одним rail-service, snapshot и service_date после -3600 давали
   82800 и 86400 вместо двух82800. После исправления физическое изменение
   распространяется на связанные задания; несовпадение movement clocks/costs/modes
   отклоняется. scenario/feasibility.py использует context+execution+date вместо
   job ID для физической идентичности: один рейс нельзя назначить двум ресурсам
   через разные задания. Regression: test_shared_cargo_execution_is_shifted_in_every_job,
   включая неизменность исходных actions, повтор и конфликт назначений ресурсов.
2. **P1: premature floating underflow (крайние допустимые числа).** shared/scoring.py:100*2^-1075 давало0
   вместо2.47e-322. intercity/passenger.py и pipeline/utilization.py при
   numerator=5e-324, denominator=2 давали0 вместо2.47e-322. Математический итог
   представим, терялось промежуточное значение. Редкий subnormal branch теперь
   использует Decimal/Fraction; обычный путь вычислений сохранён. До исправления
   новые тесты падали. Decimal oracle,5 exponent boundaries и300 фиксированных
   случайных monotonic/half-life controls проходят. Это экстремальные численные
   входы, не оценка практического улучшения транспортных данных.
3. **P2: quadratic impact scan.** scenario/impact.py для каждой точки заново
   просматривал все точки при группировке и проверке population_share_id.
   Теперь однократная группировка и множество shared-cell IDs. Семантика unknown,
   оригинальных ячеек и дедупликации сохранена; прежние scenario regressions проходят.
4. **P2: устаревший вводный README** утверждал отсутствие Scenario Engine и237
   exports после его поставки. Вводная часть исправлена, исторические разделы сохранены.

## Матрица независимых проверок и пробелов

| Подсистема | Инвариант | Existing tests | Новое | Пробел |
|---|---|---|---|---|
| Формулы | единицы, unknown, диапазон, знаки | urban/passenger/freight/pipeline | Decimal subnormal и300 controls | не все комбинации IEEE |
| Reference | explicit states, nonnegative cost, operations | raw-path/state-operation oracles |40 случайных6-узловых графов с циклами, raw DFS и permutations | large dense graphs не измерены |
| Timetable | wait budget, boarding/alighting, calendars | tiny exhaustive oracle | повторная регрессия | нового независимого temporal oracle нет |
| Intercity | одна chain, first-departure/mode frontier | whole-chain exhaustive oracle | повторная регрессия | большие mode-frontiers не измерены |
| Freight | readiness, completion, compatibility | raw-action oracle | shared execution cross-job regression | branching frontier stress не выполнен |
| Traffic | entry-time, snapshot, minimum proof | static/typical/live/historical tests | повторная регрессия | реальный executor не проверен |
| τ/districts | directed pairs,28 slices, weights | Fraction/roster/coverage tests | повторная регрессия | large OD/memory stress не выполнен |
| Scenario | immutable inputs, all worsening |11 attacks, independent manual reruns | cross-job update/resource conflict | exhaustive arbitrary multi-profile execution composition не доказана |

## Производительность

Воспроизводимый bounded script: `examples/benchmark_audit.py`; фиксированная chain
и синтетические точки. Время perf_counter, память tracemalloc (Python allocations,
**не RSS всего процесса**). Один замер, без статистической гарантии.

| Вход | До, s | После, s | До peak bytes | После peak bytes |
|---|---:|---:|---:|---:|
| impact500 points |0.04466|0.01437|90032|132384|
| impact2000 points |0.47883|0.05657|323744|495464|
| walk chain500 edges |0.00658|0.00657|1123160|1122152|
| walk chain2000 edges |0.04018|0.04004|16541592|16541592|

Impact grouping O(P) вместо O(P²), ценой дополнительного словаря.
Walk processed501/2001 states. Функция возвращает полный path tuple для каждого
достигнутого узла: суммарная длина результата на цепи O(V²), поэтому нельзя
обещать O(V) memory для данного API. Чужой интерфейс не менялся ради оптимизации.
Большие national graphs, schedule/cargo/OD/scenario loads **не измерены**.

## Алгоритмические гарантии и стоимость

- routing/reference/search.py:build_reference — multi-source Dijkstra с Fraction
  и explicit phase/mode/conditions state. Zero-cycle tie-break: cost,hops,arc IDs.
  O((V+E)log V) описывает только heap operations; Fraction arithmetic и копирование
  path tuples добавляют стоимость, вплоть до O(V²) памяти на длинных путях.
- routing/timetable.py:solve_timetable — labels arrival/wait budget на event states.
  Более ранний arrival доминирует только если ожидание до времени второй метки
  остаётся не больше. Стоимость зависит от количества недоминируемых labels.
- routing/scheduled.py:search_scheduled_chains — frontier first boarding, mode set,
  onboard/alight/node; arrival и canonical action order. Не оптимизирует только
  одно earliest arrival для всех пассажирских индексов. Mode sets могут расти
  экспоненциально, retained labels/переходы — практические параметры стоимости.
- freight/delivery/search.py:analyze_intercity_cargo — finite priority search,
  state/time/used actions/modes/first departure. Used-action subsets сохраняют
  обязательства и делают поиск конечным, но worst-case экспоненциальным.
- urban/tau/calculation.py:calculate_urban_tau — точные Fraction weights и28
  равновесных срезов; O(28*N²) pair visits плюс стоимость rational arithmetic.
- routing/traffic/evaluation.py:evaluate_traffic_path — последовательная оценка
  supplied directed path по времени входа; не собственный global shortest path.
  Полный минимум требует accepted executor receipt.
- scenario/engine.py:simulate_scenario — before validation и full after rerun;
  validate_scenario_result повторяет расчёт перед публикацией. Стоимость зависит
  от суммы solver calls по всей области, а не только от изменившегося пути.

## Интеграционная граница

Проверены опубликованные HEAD: BACKEND6ce41795572b3708dc09f49868a3cefaa7e77fb3,
ML2216676a4e9ee9a4d9bc81386a4b719ca947aefc4. Ветки не слиты.
ML2 уже сообщает accepted bundles трёх городов и справочники РФ; этот аудит
**не выполнял их ingestion/E2E**, поэтому не утверждает ни отсутствия всех реальных
данных, ни готовности их подсоединения к engine.

| Input | Function | Required supplements | Result/completeness | Dependency |
|---|---|---|---|---|
| urban_passenger | analyze_urban_candidates/search_urban_transit | grid/context, catalog, minimum proofs, population/τ | UrbanAnalysis, full/partial | accepted data + road executor |
| intercity_passenger | analyze_intercity_passenger | dated timetable, physical aliases, reference scope/calibration, readiness | rich modes/winner/frequency, optimum_proven | timetable/reference adapter |
| urban_cargo | analyze_urban_cargo | physical sites, directed truck receipts, compatibility/profile | site/type aggregates, roster proof | freight executor/adapter |
| intercity_cargo | analyze_intercity_cargo | states/actions, readiness, processing/wait/calendar receipts | completed paths, independent data/search completeness | accepted cargo services |
| pipeline | analyze_pipeline_connections | directed end-to-end connectivity, product/period/unit V/Q | connectivity separate from utilization | accepted connection/quantity data |
| scenario | simulate_scenario/validate_scenario_result | dated event/hash/timing model + resource blocks | all effects, separate feasibility/impact | DTO supplement + atomic publication |

**DEVOPS:** packages/engine/pyproject.toml содержит package=false. Проверенный
`uv run --frozen python -I -c 'import a2_engine'` падает ModuleNotFoundError.
С PYTHONPATH импорт/регрессия работают. Нужны build backend/package discovery и
установка engine в backend image; acceptance: isolated interpreter import без
cwd/PYTHONPATH и config из explicit deployment path. Манифесты не изменялись.

**BACKEND:** packages/contracts/src/a2_contracts/v2/scenarios.py Feasibility пока
verified/unverified; добавить infeasible или согласованное lossless отображение.
Дополнить FixedParameters dated event/expected hash/incoming pair/timing model;
сохранять полный before/after/evidence, semantic IDs, unknown и all worsening;
apps/api worker должен вызвать publication validator и опубликовать атомарно.

**ML2:** execution identity должна быть физической и одинаковой между jobs;
нужны полные clocks, profile-specific compatibility/processing, dates/calendars,
resource coverage/turnaround, original population и expected site roster.
Не подменять отсутствие данных утверждением отсутствия связи.

Статусы продуктовой интеграции: CONTRACT_ADAPTER_PENDING, EXTERNAL_DATA_PENDING
(проверка достаточности/подключение), EXECUTOR_PENDING, E2E_PENDING. ENGINE_READY
для всего объёма запрошенного финального аудита этим ограниченным проходом
не сертифицирован. Остаток: независимый разбор остальных реализаций и расширенные
bounded stress-проверки schedule/cargo/OD/scenario, затем совместный E2E.

## Итог выполненной регрессии

881 passed in13.10s, skipped0. Ruff check/format clean; strict mypy src/tests/examples
137 files clean. uv lock --check resolved157; uv pip check56 compatible; product
v3 valid; git diff --check clean. Все16 README controls и12 synthetic examples
исполнены, дополнительно benchmark_audit. Прежнее предупреждение pytest-asyncio
о default fixture scope остаётся. Public API268, старые signature snapshots и
архитектурные import guards проходят. Bare installed-package import — FAIL,
передан DEVOPS, не обозначен PASS. Полный requested audit остаётся открытым.

# Завершение оставшихся проверок — 2026-11-09

**Актуальный вердикт: READY WITH EXTERNAL DEPENDENCIES.** Предыдущие разделы
сохранены как история первого прохода. Ниже закрыты перечисленные там пробелы
независимых oracle и bounded нагрузочных проверок. Это не формальная верификация
всех возможных графов и не подтверждение production E2E.

Исходный HEAD этого прохода:8f2e3bbf31ac5232481d91158491b98f45e09b73;
work/ml1 в основном PyCharm checkout. Remote FF-проверка: already up to date.
Независимый baseline:881 passed/12.19s. Текущие BACKEND6ce41795572b3708dc09f49868a3cefaa7e77fb3
и ML2216676a4e9ee9a4d9bc81386a4b719ca947aefc4 повторно fetched/read-only, без merge.

## Новый подтверждённый P1

`scenario/overlay.py:apply_scenario_overlay`, additional_departure: новый физический
рейс добавлялся только в target network. Два jobs с двумя представлениями одного
расписания получали разные after при одинаковых запросах: второй job не видел
новое отправление. Новый самостоятельный тест воспроизвёл это до исправления
(36 passed/1 failed в первоначальном наборе).

Исправление: closure принятых физических aliases определяется для обоих templates;
additional departure добавляется во все timetable datasets, содержащие выбранное
исполнение или принятый alias. Источник, service date, trip-copy validation и
feasibility gate сохранены. Рейсы других дат не попадают в closure через один
trip_id. Нет расширения allowlist и нет изменения готовых индексов.

`test_additional_departure_updates_all_shared_timetable_views` проверяет наличие
рейса во всех связанных views, равенство вычисленных after, baseline без добавленного
рейса, повторный вызов, publication validation и отказ при подмене операции/удалении
эффекта. Прежние cross-job cargo/resource regressions также проходят.

## Новые независимые проверки

Генераторы и raw oracles: `examples/audit_completion_workloads.py`.
Тесты: `tests/test_audit_completion.py`,41 cases.

| Область | Независимый контроль | Дополнительные границы |
|---|---|---|
| Urban timetable |30 seeds ×16 рейсов, полный DFS raw departures, независимый wait budget, минимум arrival | resource_limit0, прежние dominance/calendar/pickup/dropoff/repeated-stop tests |
| Intercity | тот же raw DFS без urban wait cap, собственная формула целой цепочки, максимум по каждому mode/mixed/public, сравнение фактических времён победителя | частота как unique first departures с полной цепью; first departure ровно7д исключён,7д−1с включён с arrival после7д; partial reference/modes/aliases в прежней регрессии |
| Cargo | независимый DFS конечного action graph при widths1/2/3/5; выбор полного total и action IDs, самостоятельная формула индекса | конечная обработка, unknown unload с сохранением best-known, resource limit,31д−1с/31д, completion после окна; existing compatibility/required-actions tests |
| OD/τ | собственная Fraction сумма P_iP_j для всех directed pairs и28 равновесных срезов | обратный порядок batches, idempotent replay, конфликтующий ответ, foreign mode, missing pair; existing huge-rational tests |
| Last return | два независимых календарных fixture: последнее отправление до полуночи с arrival после; повторённый час Europe/Berlin25.10.2026 | выбор более позднего UTC departure, resource_limit0 не proven |
| Scenario | новая операция additional departure, реальные route searches для двух jobs | immutable baseline, повтор, publication tampering; existing tail/date/new/lost/unknown/population/resource tests |

Raw oracles не вызывают engine solver для получения ожидаемого ответа.
Fixtures используют нормализованные модели и существующие synthetic inputs для
контекста; ожидаемый ScenarioResult вручную не конструируется. Конечные семейства
не доказывают отсутствие всех ошибок вне покрытых условий.

## Нагрузочные измерения оставшихся областей

Команда: `PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/benchmark_completion.py`.
Seed314159; последовательные ограниченные размеры, без сети и национальных графов.
Измерены wall time и tracemalloc peak Python allocations, не process RSS.

| Workload | Размер | Время,s | Peak bytes | Состояния/объём |
|---|---:|---:|---:|---:|
| urban timetable |60 рейсов|0.0061|24646|5 expanded |
| urban timetable |240 рейсов|0.0355|100242|7 expanded |
| intercity |60 рейсов|0.4318|837407|396 expanded |
| intercity |240 рейсов|4.1500|7071834|6858 expanded |
| freight branching |width3|0.0523|111836|20 states,9 completed paths |
| freight branching |width8|0.7391|354085|90 states,64 completed paths |
| OD/τ |12 точек|0.3050|2010761|3696 directed pair-slices |
| OD/τ |32 точки|2.3072|16464045|27776 directed pair-slices |
| scenario |4 jobs|0.5849|2302197|84 effects |
| scenario |16 jobs|3.0323|8350853|336 effects |

Scenario time включает baseline construction, repeated baseline verification и
after recompute. Shared physical cells сохраняют affected population100 независимо
от числа jobs. В timetable/cargo тестируются прерывания по budget и отсутствие
ложного optimum_proven. В urban генераторе мало expanded states благодаря раннему
достижению цели: это не worst-case branching benchmark. У intercity заметен
сверхлинейный рост frontier; требуются лимиты области, CPU/RAM/time в worker.
Цифры единичные, не статистическое SLA.

Повторный `benchmark_audit.py`: walk500 —0.0063s/1122152B/501 states;
walk2000 —0.0402s/16541592B/2001 states. `walk_access_paths` возвращает materialized
path tuple для КАЖДОГО достигнутого узла. На цепи длина результата минимум
1+2+...+V=O(V²). Predecessor map не уберёт нижнюю границу итоговой памяти при
сохранении API; на этой цепи frontier мал. Существенный выигрыш для проверенного
случая не подтверждён, поэтому рискованный rewrite не выполнен. Возможное будущее
ускорение требует отдельного compact/lazy API, сохраняя нынешний фасад.

## Интеграционный gate

Сохранены268 публичных exports/signatures/defaults и прежние request identities;
только исправленные after outputs shared-additional сценария закономерно меняются.
Архитектурные guards и fresh-process imports/config outside cwd проходят.
Матрица четырёх analysis_kind/pipeline/scenario выше остаётся актуальной.

Проверены текущие canonical `v2/results.py` и `v2/scenarios.py`:
- Metric.value nullable; unknown/not_applicable требуют reason. Mapper обязан
  переносить engine status/reason и отдельно rich completeness, subject/evidence,
  population/site basis. Scalar MetricResult сам по себе не является wire Metric.
- Score ограничен0..100. Pipeline utilization выше100 допустима, отображается
  Metric, не Score и не cargo delivery index.
- Full territory требует expected population/site roster. Partial optimum/reference,
  missing modes/processing и best-known нельзя повышать до full при сериализации.
- Engine evidence IDs требуют разрешения в canonical EvidenceRef с source URL и
  датами; строка synthetic-proof не является production evidence record.
- Feasibility canonical всё ещё verified/unverified; engine infeasible требует
  lossless дополнения DTO. ServiceChange validator требует whole_trip=True:
  departure_tail нельзя публиковать как whole_trip. До изменения DTO backend
  должен ограничить эту операцию либо явно поддержать модель tail.
- Для операций нужны concrete dated execution/event, expected hash, incoming pair,
  timing model и accepted resource blocks. Canonical FixedParameters их полностью
  не представляет. Before/after/effects и worsening roster сохраняются целиком;
  publication guard вызывается до атомарного сохранения.

Неподдерживаемые операции (handling-duration/truck/pipeline edits/cargo additional)
отклоняются; две независимые операции разрешены как отдельные симуляции от baseline.
Неявное stacking overlay не поддерживается. Composite operation template не добавлен.

### Минимальная передача DEVOPS по упаковке

Подтверждено повторно: `uv run --frozen python -I -c 'import a2_engine'` —
ModuleNotFoundError. Это НЕ исправлено PYTHONPATH и остаётся внешней зависимостью.

В `packages/engine/pyproject.toml` DEVOPS должен заменить package=false на true
(или удалить false при явном build-system) и добавить, например:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
include = ["a2_engine*"]
```

Это минимальный совместимый вариант; конкретную build dependency/pin принимает
DEVOPS. Основание: [официальная конфигурация setuptools](https://setuptools.pypa.io/en/stable/userguide/pyproject_config.html).
Затем владелец обновляет lock при необходимости, выполняет `uv build --package
 a2-engine` и устанавливает wheel в чистое Python3.12 окружение. Backend image
должен устанавливать engine как dependency; возможная workspace source запись и
apps/api manifest также принадлежат DEVOPS. `uv sync --frozen --all-packages` после
согласованного изменения проверяет workspace installation, wheel install отдельно
проверяет отсутствие зависимости от checkout.

Acceptance из другого cwd без PYTHONPATH: `python -I -c "import a2_engine; assert
len(a2_engine.__all__)==268"`; затем `load_urban_config(absolute_product_path)` и
контроль category_index(1200,1200)==50. Config/product.json находится вне wheel:
в deployment нужен явный путь/согласованная доставка конфигурации. Не ожидать, что
repository-relative default loader найдёт файл в произвольном image.
Если требуется type checking установленного wheel, включить PEP561 marker
`a2_engine/py.typed` и проверить импорт из отдельного typed consumer.

### Внешние условия

ENGINE_READY в проверенной нормализованной области; CONTRACT_ADAPTER_PENDING,
EXTERNAL_DATA_PENDING (ingestion/достаточность accepted данных), EXECUTOR_PENDING,
E2E_PENDING. Реальные HTTP/Valhalla и совместный PostgreSQL/backend E2E здесь не
запускались; это не скрытые PASS. Manifests, lock, DTO и чужие ветки не менялись.
Подтверждённых неисправленных P0/P1 в выполненных критических проверках нет.

## Проверки завершения

Фактически выполнены: полный engine pytest **922 passed/13.69s,0 skipped**;
Ruff check/format (144 files), strict mypy src/tests/examples (140 files),
architecture/public API compatibility включая прежние268 names, lock resolved157,
pip56 compatible, product v3 valid, diff-check. Все16 README executable controls
и12 synthetic examples PASS; оба benchmark scripts исполнены отдельно.
`check_walk_bundle.py` и `check_district_bundle.py` требуют ML2 data/bundles,
которых в этом checkout нет: реальные readiness-запуски этих двух CLI не выполнены,
не названы PASS. Синтетические проверки соответствующих адаптеров входят в pytest.
Предсуществующее pytest-asyncio fixture-scope warning не изменялось.

Команды:
```sh
PYTHONPATH=packages/engine/src uv run --frozen pytest packages/engine/tests -q
uv run --frozen ruff check packages/engine
uv run --frozen ruff format --check packages/engine
PYTHONPATH=packages/engine/src uv run --frozen mypy --strict packages/engine/src packages/engine/tests packages/engine/examples
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/benchmark_completion.py
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/benchmark_audit.py
uv lock --check
uv pip check
uv run --frozen python scripts/check_product_config.py
git diff --check
```
