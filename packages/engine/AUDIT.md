# Независимые проверки ML1 — 2026-11-09

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
