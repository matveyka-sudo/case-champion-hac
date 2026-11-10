# ML1

Ветка work/ml1. Пути: packages/engine/.

**Текущая готовность (09.11.2026):** самостоятельный A, пассажирский межгород,
городская грузовая инфраструктура, поиск полной доставки и отдельный анализ
принятых pipeline connections, а также Scenario Engine v3 реализованы и проверены
на нормализованной синтетике. Реальные accepted данные/HTTP/handler/deployment
остаются интеграции. Последний сценарный этап и точный handoff — в конце файла
и engine README.
История предыдущих этапов сохранена.

Источник: ../PRODUCT_SPEC.md; контракты ../../packages/contracts/specs/README.md.

Расчёт времени, выбор ближайшего объекта, пересадки, направленные связи и сценарии. Реализовать FORMULAS.md, включая границы H, веса, τ и эталон A из IMPLEMENTATION.md. Разделить население и грузовые объекты; трубопроводы по продуктам.

Зависимости: BACKEND: DTO; ML2: accepted snapshots.

Приёмка: Контрольные пути/ожидание/дедупликация; сценарий не меняет базу. Методика a2-methodology-v3 утверждена: индексы рассчитываются при корректных известных входах; нехватка данных сохраняет unknown/null, а не всеобщий methodology_pending.

Передача: commit, пути, команды/результаты проверок, контрактные изменения и ограничения. Формулы реализуются по FORMULAS.md и config/product.json; данные проверяются отдельно.

## Финализация 08.11.2026
Обязательны PRODUCT_SPEC.md, FORMULAS.md, IMPLEMENTATION.md и ASSISTANT.md. Начать с этапа 1 таблицы IMPLEMENTATION, сохранить существующие изменения коллег. Контракты версии 2: численные методики утверждены, readiness данных отдельно. Передать в PR фактические проверки и зависимости.

## Этап 1: чистые городские пассажирские индексы

В `packages/engine/src/a2_engine/` реализованы загрузка/валидация городской
конфигурации, H по административной площади в секундах, индекс категории,
строгий порог низкой доступности, индекс выбранных категорий точки и отдельные
результаты режимов, агрегат исходных ячеек/долей по населению.
Unknown выбранного компонента блокирует полный агрегат; неизвестный индекс
нулевого населения не влияет на остальных. Нулевой знаменатель —
not_applicable/ZERO_WEIGHT. Подтверждённый no-route передаёт caller явно;
ошибка провайдера и пустой набор не подтверждают недостижимость.

Интерфейс, синтетический пример, команды проверок и точные handoff к BACKEND,
ML2 и DEVOPS: `packages/engine/README.md`. До изменения DEVOPS упаковки
`package=false` проверки выполняются с `PYTHONPATH=packages/engine/src` из корня.
Параметры методики читаются только из текущего `config/product.json`, исторический
`profiles/p1-v1.toml` не используется. Промежуточного округления нет.

Это математический блок этапа 1, не готовый городской анализ. Маршрутизация,
реальные объекты/население, τ, последний обратный рейс, межгород/грузы,
сценарии и HTTP-интеграция остаются следующим этапам.

## Этап 2: расширение чистого математического слоя

Добавлены отдельные PassengerConfig/FreightConfig из текущего product.json,
пассажирский межгород одной принятой цепочки (path/wait/route) и отдельный car path,
городские грузовые индексы с равными весами уникальных физических площадок,
индекс полной известной доставки по срочности, процент загрузки подтверждённого
трубопровода и сравнения уже рассчитанных сопоставимых результатов.

Версия, полнота и применимость эталона поступают явно; partial reference даёт
отдельный completeness=partial, не новый status и не полный рейтинг.
REFERENCE_INCONSISTENCY сохраняется с cap 100. Scope грузового агрегата фиксирует
тип узла, inbound/outbound и профиль; дубли площадок отклоняются.
Неизвестные операции не исключаются из полного времени. Загрузка свыше 100%
сохраняется с QA-диагностикой и не включается в delivery score.

Для сравнения нужны ComparisonContext и ссылка на внешнюю проверку сопоставимости.
Отрицательные дельты представлены DeltaResult; MetricResult первого этапа остаётся
неотрицательным. Появление/потеря связи обозначается причиной без численной
экономии времени. Все импорты абсолютные, старый публичный интерфейс сохранён.

Публичные сигнатуры, синтетические исполнимые примеры, проверки и точные handoff
к BACKEND/ML2/DEVOPS находятся в `packages/engine/README.md`.
Это вычисления по готовым входам. Поиск маршрутов/лучшей цепочки, построение A,
реальные данные, графовые изменения расписаний и исполнимость сценариев не
реализованы; следующий интеграционный шаг — канонические DTO и принятые входы.

## Структурный рефакторинг engine

Реализации распределены по shared, urban, intercity, freight, pipeline и comparison.
Городские и межгородские грузы находятся в freight; urban/intercity содержат
пассажирскую логику. Конфигурации и численная валидация — shared, общая
экспонента — shared/scoring.py. Межгород/грузы больше не импортируют urban.
Корневой интерфейс сохраняет все 37 экспортов с прежними сигнатурами/defaults.
Реэкспорты ведут к единственным каноническим объектам, frozen dataclass сохранены.

Пути a2_engine.urban/freight/pipeline/comparison сохранены пакетными __init__.py.
Внешних потребителей старых config/results/passenger в репозитории не найдено;
эти реализации перенесены без дополнительных модулей совместимости.
Для shared/config.py учтена новая глубина checkout (parents[5]); default и явный
путь конфигурации остаются независимыми от cwd. Упаковка по-прежнему у DEVOPS.

Новые проверки переноса: tests/test_public_api.py. Таблица путей, структура,
правила зависимостей и команды находятся в README engine. Прежние математические
тесты и пять примеров сохранены. Новых продуктовых функций, изменений формул,
статусов, причин, диагностик и округления этот этап не добавляет.

## Городской анализ по переданным кандидатам

Публичный analyze_urban_candidates объединяет проверки UTC-интервалов, выбор
пригодного объекта по минимальному полному времени, дедупликацию object_identity,
прежние H/category/point/territory индексы и count по переданному принятому τ.
Нормализованные frozen-типы в urban/models.py, временная проверка в journeys.py,
выбор в selection.py, объединение/результаты в analysis.py.

UrbanJourneyRules читает общий лимит ожиданий из product.json без изменения
UrbanConfig. Некорректная структура — ValueError; превышение лимита ожиданий —
отдельный недопустимый кандидат с сохранёнными временами. Полное время само по себе
четырьмя часами не ограничено. Геометрия не синтезируется, access connector не исчезает.

Полнота поиска требует явного scope, evidence и совпадения catalog roster.
Полнота территории дополнительно проверяет roster точек, потерянное население
и популяционные веса. При partial поиске лучший известный маршрут сохраняется,
индекс unknown. Подтверждённая недостижимость даёт только индекс 0, не время 0.
Unknown τ не блокирует H-индекс; fake dependency jobs не создаются.

Реальные DTO BACKEND прочитаны из d2cfe9a без слияния; их нет в текущем checkout.
Внутренний UrbanAnalysis пока не канонический AnalysisResult. Точная таблица
отображения и handoff находятся в README. BACKEND нужно подключить адаптер,
сохранение доказательств охвата и согласовать фиксированный лимит 14400 в JourneyResult
с конфигурацией. Engine не импортирует a2_api/FastAPI/jobs или отсутствующий a2_contracts.

Синтетический сквозной пример: H=1200с, T=1200/2400с, население 90/10,
индексы точек 50/25, территории 47.5. Это готовый вычислительный слой по принятым
маршрутным входам; реальные Valhalla/OTP адаптеры, графы, приём городского набора,
расчёт τ, последний возврат, эталон A и сценарные изменения не реализованы этим этапом.

## Точный τ по принятым OD-матрицам

calculate_urban_tau реализует утверждённое двухступенчатое среднее: веса P_i P_j
по доказанно достижимым направленным парам в каждом срезе, затем равновесное
среднее обязательных срезов. UrbanTauRules читает tau_days/tau_times и лимит
ожидания из product.json; восемь завершённых ISO-недель — правило IMPLEMENTATION.
Никаких текущего системного времени, выборки пар и подмены τ числом H нет.

urban/tau_models.py задаёт отдельное OD-покрытие, календарь, состояния пар и
матрицы с evidence. urban/tau.py выбирает ближайшую пригодную неделю, сохраняет
отклонённые кандидаты/причины, результаты срезов и точные веса Fraction.
Unknown пара/население, потерянные жители, неподтверждённый профиль или обязательный
срез без положительного достижимого веса не дают полного τ. Диагональ исключена,
подтверждённая недостижимость учитывается отдельно, её доля имеет явный знаменатель.

UrbanTauResult.to_threshold() сохраняет baseline versions и подключается к
прежнему analyze_urban_candidates. Ползунок времени переиспользует порог,
сценарий должен хранить baseline reference отдельно от overlay. При неизвестном
τ H-индекс работает, count unknown. Hashes/кеш/jobs остаются у BACKEND.
Все прежние 60 публичных сигнатур зафиксированы и сохранены; новые экспорты явные.

Проверена текущая work/ml2 (02221bf): walk привязки и AcceptedData v2 adapter уже
есть в bundle kaliningrad-urban-2026-11-08.2, но OD-времён нет. В исключённых
ячейках QA указывает 277.8 жителей — это блокирует полный городской τ по остатку.
Реальный τ Калининграда не рассчитан. Форматы матриц, исполнимый синтетический
пример и точные handoff ML2/маршрутному слою/BACKEND/DEVOPS приведены в README.

Проверки этапа: 383 pytest-теста (320 прежних и 63 новых), Ruff check/format,
mypy, uv lock --check, uv pip check, check_product_config.py, git diff --check
и все семь исполнимых примеров README прошли. Публичных экспортов теперь 72.

## Пешая интеграция walk/static

UrbanDestination расширен необязательным keyword-only physical_node_id; старые
вызовы сохранены. Финальный подход теперь проверяется отдельным конечным walk leg.
urban/walk.py отображает канонический AcceptedData v2 (после валидации BACKEND),
учитывает всё население и partial catalogs, строит детерминированный план всех
нужных объектов и собирает полный путь через прежний analyze_urban_candidates.
urban/valhalla.py поддерживает закреплённый route JSON Valhalla 3.5.1, отдельные
source graph/build/profile/correlation identities, внешние connector times и
проверку response envelope. HTTP/worker/foreign DTO imports в engine отсутствуют.

Сквозной синтетический контроль: 60+1620+120=1800 секунд, H=1800, индекс=50.
Геометрия сетевой части не объявляется полной; минимум времени без отдельного
аудита не доказан. Ошибки, неполные ответы и непроверенные подходы остаются unknown.
72 прежних имени сохранены (единственное расширение сигнатуры — destination
keyword-only endpoint), добавлено 15 экспортов. Все импорты абсолютные.

Реальный bundle .2 проверен существующим load_accepted_data из ML2@02221bf с
каноническими contracts BACKEND@d2cfe9a во временных внешних копиях: состав
1/286/216/39/255/1228 подтверждён. Исключённое население по каноническим числам
277.772 (QA:277.8), calendar=not_collected. Pilot airport на трёх точках из
examples/kaliningrad_walk_readiness.json заблокирован отсутствием build,
подтверждённых connectors и correlations. Сетевой прогон не выполнен, реальных
времён/индексов нет. Docker daemon недоступен; в соседних ветках executor не найден.
Готовность вычислительного адаптера, инфраструктуры и полного городского результата
различаются. Точные поля/handoff и порядок внешнего выполнения — README engine;
исполняемые synthetic replay и real readiness — examples/. Чужие исходники,
манифесты, lock и a2-dev.bundle в поставку не включаются.

Проверки: 433 теста (383 прежних + 50 новых), Ruff check/format, mypy,
uv lock --check, uv pip check, check_product_config.py, git diff --check и все
8 примеров README прошли. Ruff/mypy также проверили новые examples.

## Исправление дробных длительностей walk

Воспроизведён LEG_DURATION_MISMATCH для connector 60.123456789с. При сборке
UTC-интервалов UrbanLeg теперь получает фактическую микросекундную длительность;
исходные секунды connector/network result сохранены. Положительное значение,
обнуляющееся в timestamps, явно отклоняется WALK_DURATION_BELOW_TIMESTAMP_RESOLUTION.
Строгий валидатор цепочек, формулы и τ не менялись. Регрессии проверяют оба
подхода, сетевой участок, непрерывность и исходные значения.

## Районные агрегаты и сравнение

urban/district_models.py задаёт узкие неизменяемые ячейки, каталог районов,
абсолютные веса и их покрытие/точность. district_inputs.py сверяет producer
supplement с canonical той же версии (snapshot/population/geometry/grid/point IDs).
districts.py агрегирует готовые точечные/категорийные индексы прежним territory_index,
сохраняет городской H и независимый город из исходных ячеек, Gap/Deficit и рейтинг
с полным scope, причинами исключения и явным available_subset. Ранги 1,1,3,
сортировка до округления. Unknown τ/count не блокирует готовый H-индекс.

Контроль: A=70, B=25, C=80, город=51.25, Gap=55, Deficit B=26.25, порядок C/A/B.
При unknown p3 A/C сохраняются, B/город/Gap/Deficit unknown, список C/A — subset.
Остатки не перераспределяются; документированная точность и excluded различаются.
Теперь 104 публичных экспорта; все 87 прежних сигнатур сохранены.

Реальный .4 из work/ml2@e631929 проверен существующими загрузчиками с contracts:
286 ячеек/216 точек/3 района/262 объекта. Суммы районов: Центральный 138478.673,
Ленинградский 193507.398, Московский 153790.202; excluded 193.203/63.411/21.140.
Город 485776.410, доли 485776.273, residual 0.137, городской excluded 277.772.
Исходный floor/round rule подтверждён build_city.py:_cells; 133 ячейки с
документированным остатком. Валидность весов готова; реальных районных индексов
и рейтинга нет: маршруты отсутствуют, excluded положителен во всех районах.
Статус и команды — examples/kaliningrad_district_readiness.json и README;
синтетический пример — examples/synthetic_districts.py. Точный handoff BACKEND/ML2
описан в README. Исторические .2 fixtures, геометрии, чужие исходники,
манифесты/lock/a2-dev.bundle сохранены.

Проверки двух частей: 500 тестов (433 прежних + 4 регрессии времени + 63 районных),
Ruff check/format, mypy, uv lock --check, uv pip check, check_product_config.py,
git diff --check и все 9 примеров README прошли. Новые examples также проверены
Ruff/mypy. Real .4 readiness выполнен отдельно через соседние загрузчики.

## Поиск расписанных поездок и последнего возврата

routing/timetable_models.py и timetable.py — независимые от urban неизменяемые
исполнения/события/пешие связи/правила/область и точный событийный поиск. Метка
учитывает UTC-время, бюджет ожидания и текущий рейс/якорь последней высадки.
Доминирование только совместимых состояний: t1<=t2 и w1+(t2−t1)<=w2. Это
сохраняет единственный допустимый вариант B из контрпримера 08:05/12:15.
Скрытых лимитов пересадок/полного времени нет; resource_limit явный и возвращает
unknown/interrupted с best-known. Нулевые циклы эквивалентны и отсекаются.

urban/transit.py создаёт прежний UrbanCandidate от physical point к конкретному
physical destination, сравнивая PT и walk. initial wait не исчезает. urban/last_return.py
перебирает все события первой посадки выбранного местного дня и оптимизирует
physical leave_at. Обратная цепочка — отдельный UrbanReturnJourney; строгие
проверки legs переиспользуются из journeys.py без ослабления прежнего API.
Учитываются pickup/dropoff, пребывание внутри рейса/стоянки, service date отдельно
от UTC events, исключения и направленные transfer rules. Пересадочный minimum
явно alight_to_departure, ходьба и ожидание не складываются повторно.

Контроль: 5walk+7wait+15ride+5walk=1920с, wait=420, share=.21875; walk40 проигрывает,
walk28 выигрывает. Контрпример сохраняет B с ожиданием 14400с. Возврат A/B даёт
23:45→23:50→00:15 следующих суток; 24:05 не считается первой посадкой прошлого дня.
Пример examples/synthetic_transit.py выполняет поиск→анализ→возврат от выбранного
объекта; H=1800, индекс 47.74208019552083, count unknown без τ. Все данные тестовые,
не реальный расчёт Калининграда. Наличие реальных данных/OTP не блокировало этап.

Публичных экспортов 120, все прежние 104 сигнатуры сохранены, абсолютные импорты.
Архитектурный тест разрешает urban→routing, routing не импортирует urban. Матрицы τ,
формулы, районы, манифесты/lock/config и a2-dev.bundle не менялись. Нормализация GTFS
и canonical DTO — следующая общая интеграция. Точный mapping и ограничения
canonical LastReturn best-known/unknown описаны в README.

Проверки этапа: 546 тестов (500 прежних + 46 новых), Ruff check/format, mypy,
uv lock --check, uv pip check, check_product_config.py, git diff --check, публичный
импорт и все 10 исполнимых примеров README прошли. Отдельный независимый DFS
сравнил алгоритм на 80 крошечных расписаниях; replay ID проверен в разных
PYTHONHASHSEED. Пример дополнительно проверен Ruff/mypy.

## 09.11.2026 — static автомобиль и directed OD → τ

Исходный опубликованный SHA: `c6531ad18176052bcdc11e856f076c78df587606`.
Этап реализован в отдельной копии work/ml1; исходный checkout и a2-dev.bundle
сохранены. Изменения ограничены engine и этим статусом; backend PR #8 не менялся.

Добавлены `routing/road.py`, `urban/car.py`, `urban/od.py` и 21 additive export
(141 public names; прежние 120 сохранены). Узкие frozen road types отдельно
фиксируют source snapshot/graph, фактический build/graph, engine 3.5.1, auto/foot
costing/options/profile hash, направленные entry/exit и correlation evidence.
Автомобильная цепочка проходит существующие UrbanCandidate/journey validators,
затем общий analyze_urban_candidates; formulas/районы/τ не дублируются.

Valhalla 3.5.1 проверен по официальным исходникам: speed_types явно freeflow,
depart-at type=1 и отдельное network entry после подхода. Static не отменяет
calendar/access/turn restrictions. Используются только парные /route, matrix API
не принимается: у каждой пары сохраняется своё время входа и независимый hash.
Ferry в этом узком road-пути не подтверждается: filter исключает ferry correlation,
summary с ferry/неизвестным ferry даёт unknown, а не пеший/автомобильный минимум.
Дата upstream имеет минутную точность: subminute entry и ambiguous fold дают явный
issue/unknown без округления. Raw дробные durations сохраняются; leg durations
равны timestamp-разностям, submicrosecond positive durations не исчезают.

Минимум требует независимого executor attestation для exact request/profile/time;
summary elapsed/generalized cost не подменяют minimum-time proof. Без proof
сохраняется best-known, полный индекс unknown. Геометрия полной цепочки unknown,
без выдуманных внешних подходов. Нет connector — нет синтетического нуля.

OD planner лениво выдаёт n*(n-1) directed tasks на каждом из 28 срезов конфигурации.
Week/reference_at/IANA calendar явные; roster/population сохраняются, только
доказанный zero product позволяет пропуск. Категории/каталог учреждений не участвуют.
Collector принимает части/перестановки/идентичный replay, отвергает чужие задачи и
конфликтующие retries. Хранит compact pair/proof/best-known, а не всю неделю путей.
Для walk используется отдельный pedestrian RoadBuild (старый walk API не изменён),
для car auto, для PT реально вызывается solve_timetable на физическом departure
каждого среза с общим wait budget и pure-walk альтернативой. Срезы static никогда
не переиспользуются автоматически; typical/live/historical не поддержаны.

Контроли через новые planner/normalizer/collector:
- car 60 walk +1620 ride +120 walk =1800s; H1800 → index50;
- directed A→B600/B→A1800 в 28 срезах →τ1200;
- первые14 means900/последние14 means3600 →τ2250 (не1800);
- OD→τ→threshold→car count, unknown τ при сохранённом H;
- concrete PT time/initial wait и interrupted proof;
- missing/null/timeout/cancelled, roster/profile/boundary mismatch, fractional time,
  conflicting replay, unknown/excluded population, equal coordinates/access IDs;
- canonical task IDs совпадают в отдельных Python процессах с разными hash seeds.

Проверки: полный engine suite **591 passed**; Ruff src/tests/new examples;
format check; strict mypy src/tests/new examples (52 source files); 10 прежних
README examples и 2 новых replay; frozen lock check/pip check; product config;
git diff --check. Полные команды и точные новые сигнатуры — engine README.

Реальные автомобильные индексы/τ Калининграда не вычислялись: нет принятых car
connectors/build и внешнего Valhalla executor. Backend handler/DTO/ML2/infra не
изменены; параллельному аудиту передаётся additive ML1 API, не новый canonical DTO.
Следующий шаг — принятые typical directional profiles (graph/date/bin/day/season,
coverage QA), затем live/historical observed_at/valid_until и path-entry-time;
никакого static fallback под другим traffic mode.

## 09.11.2026 — структурный перенос urban без изменения поведения

Исходный SHA: `cd7572ebb0a2d1f054c974d304ab37a0bf9a81f1`; опубликованная ветка
перед началом совпадала, рабочая копия `/tmp/a2-ml1-car-20261109` была чистой.
Перенос выполнялся последовательно в этой актуальной копии. Старый checkout с
`a2-dev.bundle` не использовался как база и не изменялся.

Восемь крупных модулей превращены в пакеты с явными реэкспортами:
analysis (models/validation/calculation), valhalla (models/requests/responses/codec),
walk (models/inputs/analysis), car (models/analysis), transit
(models/journeys/search/last_return), tau (models/calendar/calculation),
od (models/planning/collection), districts (models/inputs/calculation).
Старые одноимённые .py удалены; tests/examples/fixtures не перемещались.
В корне оставлены общие модели/индексы/journeys/selection и validation.

Четыре узких файла совместимости: district_models.py, district_inputs.py,
tau_models.py и last_return.py. Только aliases, без повторных классов или логики.
Фактически используемые старые прямые импорты, в том числе private helpers,
сохранены. Все они возвращают канонические объекты по identity `is`.

Три общие зависимости выделены явно:
- `_check_points` — urban/validation.py, используется analysis и tau;
- `_local_instant` — tau/calendar.py, импортируется OD напрямую;
- `check_urban_inputs`, `path_legs`, identity JSON helper — transit/journeys.py,
  общие для прямого поиска и последнего возврата.
Внутренние реализации используют конкретные модули и абсолютные импорты;
фасады — только экспорт. Routing/shared и shared/config.py не изменялись.

До изменений сняты snapshot всех 141 root exports, сигнатур/defaults/frozen и
fingerprints полей dataclass; inventory прямых импортов. CANONICAL_MODULES в
существующем test_public_api.py указывает реальные новые места определений;
проверки identity/frozen/архитектуры/загрузки конфигурации сохранены и расширены.
В новых процессах проверены три порядка загрузки старых и новых модулей.

Проверка AST до/после: **94 перенесённых определения идентичны**, включая тела
функций, поля/декораторы dataclass, defaults и причины ошибок. Изменены только
размещение, module docstrings и абсолютные imports. Строки __module__ закономерно
показывают новый canonical owner; они не подделывались.

Контрольные результаты получены из исходного cd7572e и сравниваются компактными
hashes 14 полных нормализованных структур: walk, districts, transit/return, car,
request payload, OD plan/observations, week, tau, threshold и partial/unknown.
Все совпали: значения, причины, доказательства, порядок выбора, payload/profile/
request/task identities. Slots/classmethod/новые продуктовые функции не добавлены.

Фактически выполнено:
- исходный suite: 591 passed;
- итоговый `PYTHONPATH=packages/engine/src uv run --frozen pytest packages/engine/tests -q`:
  **596 passed**;
- Ruff src/tests, formatter src/tests: PASS (78 файлов);
- strict mypy src/tests: PASS (77 файлов);
- strict mypy src/tests/examples: PASS (84 файла);
- Ruff/format всех семи examples: PASS;
- десять executable README controls и пять synthetic scripts
  (walk/districts/transit/car/od): PASS;
- оба bundle CLI `--help`: PASS; реальный ingest/recalculation не выполнялся;
- uv lock --check, uv pip check, product config validation, git diff --check: PASS.

Дополнительный запуск mypy только по examples без указания source files дал
import-untyped из-за прежнего package=false/отсутствующего py.typed. Команда с
явным src/tests/examples проходит; manifests и упаковка в этом структурном этапе
не менялись. Это не исправление/изменение packaging.

README обновлён: актуальное дерево, 141 экспорт, canonical owners, таблица старых
и новых путей, назначение совместимости и правила зависимостей. Все прежние
функциональные ограничения (реальные adapters/данные, minimum proof, субминутная
граница Valhalla 3.5.1, отсутствие typical/live/historical) сохранены.
Backend/DTO/ветки коллег и a2-dev.bundle не изменялись. Следующий функциональный
этап — принятые профили трафика; здесь он не реализовывался.

## 09.11.2026 — traffic typical/live/historical и typical OD → τ

Работа выполнена **в основной папке пользователя, открытой в PyCharm**:
`/Users/matveyyuri/Desktop/Общее/хакатоны/case-champion-hac`, ветка work/ml1.
Исходный HEAD и опубликованная ветка: f4c8ae476c431ef18501d7aa4e11bd7f1cbea783.
Перед работой проверены status/diff/remotes; `pull --ff-only origin work/ml1`
подтвердил актуальность. a2-dev.bundle сохранён; отдельная копия для изменений
не использовалась. Исходная самостоятельно выполненная регрессия: 596 passed.

Добавлен независимый `routing/traffic/`:
- models.py: immutable scope/edge/calendar/provenance/observation/profile/snapshot;
  graph/source/build/calendar/engine/costing/content fingerprints, явные единицы,
  режимы, периоды и состояние closure;
- profiles.py: реальная медиана сопоставимых наблюдений, >=5 различных локальных
  дней по инженерному правилу IMPLEMENTATION.md v1; QA accepted aggregates без
  заявления пересчёта отсутствующих raw samples; календарь observation/applicability,
  duplicate/conflict handling, live/historical applicability;
- evaluation.py: последовательная оценка переданного directed full-edge path,
  точное время въезда после подхода/перехода, отдельные raw и timestamp durations,
  entry_speed_v1, known time/profile/search/minimum separation и immutable receipt;
- __init__.py: явный публичный фасад; routing не импортирует urban.

`urban/car/traffic.py`: новые TrafficCarInputs и TrafficCarAnalysisResult,
traffic_car_requests и analyze_traffic_car. Ни динамический результат, ни его proof
не переименовываются в static RoadBuild/RoadResult. Прежний UrbanCandidate,
validate_urban_journey, H/index/population/district semantics переиспользуются.
Known-but-unproven маршрут сохраняется, полный индекс unknown; противоречивый
receipt медленного пути при известной более быстрой альтернативе отклоняется.
Live/historical count остаётся unknown без принятой baseline policy, typical τ
не подставляется.

`urban/od/traffic.py`: plan_typical_od_week, traffic_od_request,
traffic_od_observation и TypicalODCollector. Старый planner остаётся static-only;
общая подготовка недели выделена в приватный `_planning.py` с сохранением старой
сигнатуры и IDs. Новый profile key связывает content hash **и search_scope_id**;
проверяются graph/calendar/mode/timezone/границы/query time. Передаются только
проверенные typical acceptance/evidence. Все 28 срезов и исходный roster сохранены,
части и одинаковый replay принимаются, конфликт отклоняется. Missing/unknown,
потерянное население и недоказанный минимум не превращаются в время0 или no-route.
Формула calculate_urban_tau и Fraction-арифметика не менялись.

Temporal policy явно versioned: L/v(entry) на полном ребре, а не интегрирование
скорости при смене bin. Каждый следующий entry использует своё фактическое время;
physical approach/exit и физические transitions учитываются один раз. Generalized
routing penalty не добавляется во время. Scope/path/receipt связывают costing
options, policy, graph/build и данные. Fractional raw seconds сохранены; duration
legs соответствует UTC timestamps; submicrosecond positive durations отвергаются.
Пеший boundary и RoadBuild 3.5.1 не изменены. Чистый evaluator не расширяет минутный
HTTP протокол: exact subminute minimum требует отдельного executor proof.

Live: observed_at не заменяется fetched_at, проверяются explicit checked_at и
source TTL для всего использования ребра. Historical: отдельный реальный source
UTC interval и period, полный source interval должен быть получен не раньше его
завершения; «похожий день»/широкая applicability не расширяют историю. Никаких
now(), придуманных TTL/скоростей/зимних коэффициентов/static fallback.
Подлинность/принятие evidence остаётся внешним QA; engine проверяет согласованность.

Численные synthetic controls:
- медиана20/30/40/50/60 =40; шесть значений10..60 →35;
- 08:13 +walk60 +edge60 +edge600 +walk60 =780s (второе entry08:15);
- typical/live/historical car60+1620+120 =1800s, H1800→50 при отдельном
  SYNTHETIC trusted minimum receipt; без него best-known и unknown индекс;
- typical directed A→B600/B→A1800 на28 срезах →τ1200, threshold реально даёт
  count0 для school1800; unknown τ оставляет H50;
- исходный двухступенчатый τ-контроль2250 сохранён; path closure сам по себе
  не выдаёт confirmed-unreachable pair;
- midnight/DST, wrong direction/graph/costing/content/search scope, профиль4дня,
  пять записейодногодня, conflicts, expiry, unknown observed_at, историческое
  source coverage и неполное население проверены отдельно.

Итоговые проверки: **656 passed**, 60 новых traffic cases поверх исходных596.
Ruff src/tests/examples PASS; format PASS (94 файла); strict mypy
src/tests/examples PASS (93 файла); 10 прежних executable README examples и все
6 synthetic replay (walk/districts/transit/car/od/traffic) PASS. Frozen uv lock
check, pip check (56 compatible packages), product config и git diff --check PASS.
Сохранены исходные141 public signatures/defaults/fields и14 прежних структурных
fingerprints, включая static payload/profile/request/OD task IDs. Всего168 exports,
публичные фасады traffic/car/od проверены на identity. Исправления промежуточных
отрицательных фикстур не ослабляли guards: hash substitution проверяется на
валидном costing объекте, а не на более ранней ошибке его формы.

README содержит конкретный handoff ML2/BACKEND/DEVOPS/executor, новый пример
`packages/engine/examples/synthetic_traffic.py`, signatures, QA и границы времени.
Требуются реальные принятые directed profiles/calendar/QA/evidence/car connectors
от ML2; immutable context/result adapters от BACKEND; profile application и exact
search/minimum receipts от executor/DEVOPS. Эти интеграции не подменены своей
загрузкой/HTTP/map matching/вторым router. Manifests, lock, config, canonical DTO,
backend PR#8 и ветки коллег не изменялись. Реальные пробки, τ и рейтинг Калининграда
не вычислялись и не объявляются готовыми.


## Самостоятельное построение межгородского эталона A — 09.11.2026

Работа выполнена в основной папке PyCharm:
`/Users/matveyyuri/Desktop/Общее/хакатоны/case-champion-hac`, `work/ml1`.
Исходный HEAD `d6d454ccfccf109a8ef3d6d4c844e538c866467c`; tracked tree был чист,
`git pull --ff-only origin work/ml1` — Already up to date. Старый `a2-dev.bundle`
оставлен untracked, его содержимое не менялось и в коммит он не включается.
Исходная регрессия проверена самостоятельно: **656 passed**.

### Реализация и границы

- `routing/reference/models.py`: frozen accepted Network/Scope/State/Calibration/
  Edge/Operation/Graph; current method/profile, snapshot/graph/acceptance/
  applicability/compatibility, направленные states и fixed boundary sets.
  Compatibility у edges/operations передаётся явно обязательным keyword:
  True/False/None = подтверждена/запрещена/неизвестна. Никаких бесплатных связей
  по совпавшим physical nodes, географии или владению автомобилем.
- `routing/reference/calibration.py`: минимум подтверждённых edge-specific
  running samples rail/air/water; road только физические static/freeflow elapsed
  seconds одного fixed Valhalla 3.5.1 build/options. Conflicting fixed road costs,
  чужие snapshots/versions/directions и несогласованные ссылки отклоняются.
  Physical edge отличается от явно ограниченной logical connection; pairwise
  RoadResult не раскладывается на выдуманные сегменты. Геодезическая длина air
  только справочная, отсутствие геометрии не уничтожает известное время.
- `routing/reference/search.py`: multi-source/target Dijkstra по departure/
  arrival/transfer states; обязательные последовательные внутренние операции,
  direction/parallel edges, восстановление ordered arcs и выбранной пары,
  movement/operations/total seconds, все calibration/coverage evidence, omitted
  IDs/reasons и fingerprint. Сравнение через Fraction без промежуточного
  округления; (cost,hops,arc IDs,start state) обеспечивает детерминизм и
  завершение на чистых zero-operation cycles. Переполнение candidate суммы
  отклоняется до очереди. Начало только departure, конец только arrival:
  pre-/post-boundary operations не добавляются по ошибке. Полнота состояний
  означает, что normalizer сохраняет все существенные ограничения продолжения.
- `intercity/reference.py`: ReferenceComparison и reference_time_from_result
  сверяют независимо переданный actual comparison context и config. Exact pair
  отличается от city-pair best-node; direction/graph/snapshot/boundaries/profile/
  comparison kind/allowed modes/compatibility должны совпадать. Actual traffic
  может быть static/typical/live/historical; static A от него не меняется.
- `examples/synthetic_reference.py`: builder самостоятельно получает A=3600,
  затем через адаптер вызывает прежние passenger и car formulas.

Старые ReferenceTime/PassengerChain и обе index functions не изменены. Все прежние
168 exports/order/signatures/fields/defaults/frozen проверяются новым baseline
snapshot `tests/fixtures/public_api_168.json`, снятым до изменений; исторический
141 snapshot сохранён. Добавлено 15 symbols, всего 183. Архитектурный guard
сохранён: дополнительно разрешён intercity → routing.reference.*, reverse
routing → intercity/urban запрещён, shared не зависит от доменных слоёв.
Все новые импорты абсолютные.

Unknown edges/operations/compatibility и неполнота alternatives дают partial,
а не 0. Известный partial A остаётся numeric и даёт preliminary index с
PARTIAL_REFERENCE; full ranking запрещён. `no_path_proven` полной области
отличается от `known_subnetwork_no_path` неполной. A=None сохраняет actual path
и waiting index. A>actual не исправляется: cap100 + REFERENCE_INCONSISTENCY.
Ресурсных отсечек/прерывания в этом API нет: незавершённый вызов не публикует A;
будущий executor budget потребует отдельный best-known/search-incomplete result.

### Контроли и выполненные проверки

- direct5000 против rail1800 + transfer300 + air1500 → **A=3600**;
  actual path7200/wait43200 → path50, wait50, passenger≈50, car50.
- AB samples1000/4000, BC samples4000/1000, transfer300 → **A=2300**.
- unknown transfer → **partial5000**, без direct → **None**;
  forbidden transfer → **full5000** по оставшейся полной допустимой сети.
- Неодинаковые arrival states одного physical node сохраняют совместимый
  медленный приход; нет неявной пересадки или разрешения car carriage.
- Common A1/A2→B1/B2 → **600**, chosen A1/B1; exact A2/B2 → **900**;
  несовместимые scopes не смешиваются. A9000/actual7200 → **100** + диагностика.
- Дополнительно: zero cycles (в том числе только операции), несколько внутренних
  operations, parallel/equal paths, precise sum/tie counterexample, input
  permutations, fresh processes/PYTHONHASHSEED, evidence/versions/numbers/IDs/FK.
  Независимый exhaustive oracle: 45 малых movement/state графов с операциями
  и 20 внутренних operation-state графов. Oracle не использует Dijkstra engine.

Реально выполнены из корня проекта:

```sh
PYTHONPATH=packages/engine/src uv run --frozen pytest packages/engine/tests -q
uv run --frozen ruff check packages/engine
uv run --frozen ruff format --check packages/engine
PYTHONPATH=packages/engine/src uv run --frozen mypy --strict packages/engine/src packages/engine/tests packages/engine/examples
uv lock --check
uv pip check
uv run --frozen python scripts/check_product_config.py
git diff --check
```

Итог: **742 passed**, Ruff check/format clean (103 files), strict mypy clean
(100 Python files), frozen lock resolved157 / pip checked56 compatible,
product config valid. Прежние static structure controls и API guards прошли
в полном suite. Отдельно исполнены все **11 README controls** и **7 examples**:
synthetic_walk/districts/transit/car/od/traffic/reference. До изменений suite
уже выдавал предупреждение pytest-asyncio о fixture loop scope; это не test failure.
Manifests/lock/config, canonical DTO, чужие модули и ветки не менялись.

### Конкретный handoff

**ML2 — packages/data_pipeline/**: accepted directed reference edges с edge-level
running-time samples, scope/snapshot/versions/evidence и coverage; обязательные
minimum operations и их совместимость, state IDs с платформой/входящим ребром/
car possession; unknown/forbidden не смешиваются. Полная область — подтверждение
всех допустимых alternatives/operations, а не только выбранного пути. В прочитанном
`work/ml2@0e6dbca005674f86c5cde7c2f2087477d509e7df` contract_v2.py массивы
reference_edges/minimum_transfers пока пустые. Приёмка: реальные принятые записи
нормализуются в ReferenceGraph без придуманных seconds и скрытой неполноты.

**BACKEND — packages/contracts/src/a2_contracts/v2/journeys.py и apps/api/**:
прочитан опубликованный `work/backend@6ce41795572b3708dc09f49868a3cefaa7e77fb3`.
Точный mapping ReferenceResult приведён в README: ID/seconds/status/completeness/
reason/profile/graph/boundary sets/edges/necessary operations/calibration evidence.
Хранить rich result: текущему DTO недостаёт selected pair, snapshot/acceptance/
compatibility/applicability/methodology, mode/comparison scope, interleaved order,
breakdown, selected samples, omissions и search_status. До расширения DTO эти
поля нельзя терять во внутреннем результате. Handler нормализует canonical inputs,
вызывает build_reference и проверенный adapter с одним common scope, сохраняет
nullable A/partial diagnostics. При отказе applicability canonical seconds=None.
Приёмка: mixed3600 → indices50; partial5000 → numeric preliminary, no full ranking;
unknown A → raw times/wait index сохранены; чужой scope → not_applicable.

**DEVOPS/executor — infra и принятый road build**: fixed version/build/graph/
options/evidence, accepted static physical elapsed edge seconds или явно
ограниченных logical connections. Доказать, что cost — seconds, а не generalized
preference, без live/typical/historical traffic. Подлинность документов/evidence
и приёмка исходной сети остаются во внешнем контуре. Нужен backend handler и
реальный accepted graph для совместного E2E; синтетика не объявляется эталоном
между реальными российскими городами.


## Пассажирский межгород: расписания, максимум индекса и частота — 09.11.2026

Исходная папка PyCharm:
`/Users/matveyyuri/Desktop/Общее/хакатоны/case-champion-hac`, `work/ml1`.
Проверен HEAD `4160a06b1c1c25733740485cc767aeb4209e77dc`, tracked tree чист,
ff-only pull — Already up to date. Самостоятельная исходная регрессия:
**742 passed**, **183 exports**. Старый a2-dev.bundle сохранён untracked.

Реализованные файлы:
- `routing/scheduled.py`: независимый event search. Перечисляет все допустимые
  первые boardings, сохраняет first identity/onboard run/event/last alight/modes,
  Pareto ready-time/action-order labels и лучшие завершения для каждого
  first departure/mode set. Использует существующие timetable models/helpers,
  не меняет urban solver и не переносит его wait budget.
- `intercity/analysis/models.py`: frozen scope, mode mapping, physical service /
  departure supplement, capital boundaries, readiness, accepted car, rich results.
- `intercity/analysis/identity.py`: evidence-bound alias normalization; строгая
  согласованность всей цепочки событий, remap run-specific transfer rules,
  conflict rejection. Identity evidence не заполняет missing calendar/event proof.
- `intercity/analysis/calculation.py`: analyze_intercity_passenger; independent
  scope сверяется с timetable graph/snapshot/transfer version и rules/config,
  затем прежний reference adapter и scalar formulas применяются к целым цепочкам.
  Отдельные by-mode/mixed/public winners и car, raw times при unknown A.
- `intercity/analysis/frequency.py`: физические первые departures с допустимым
  продолжением до target; count по union IDs, отдельные nullable completeness/reasons.
- `shared/config.py`: новый IntercityPassengerRules и mapping/path loaders читают
  departure_window_days из approved config. PassengerConfig не изменён.
- Public root/intercity facades, `tests/test_public_api.py`, новый baseline183
  fixture, `tests/test_intercity_analysis.py`, `examples/synthetic_intercity.py`.
  README содержит интерфейсы, алгоритм, границы и canonical mapping.

### Алгоритм, время и полнота

Фиксированы первое отправление и W0; при одном common A уменьшение T_path
не уменьшает индекс. Поэтому достаточно earliest final arrival для каждого
первого события и точного mode set, затем прежний intercity_passenger_index
выбирает максимум полного score. Best-of-mode не выводится из одного global winner.
Промежуточный score не является dominance criterion. При равных score выбираются
раннее прибытие, меньше canonical actions, stable IDs. Pareto labels сохраняют
нужные префиксы для этого tie-break; waits не входят в action order. Zero cycles
не создают бесконечного улучшения. Новые импорты абсолютные.

First departure window — [request,request+7 суток), из текущей конфигурации.
Continuation horizon отдельный, без произвольной замены межгородского ожидания
большим budget. Конец scope меньше конца окна или обрезанные продолжения дают
неполноту; continuations_complete — внешнее принятое подтверждение достаточности.
Внутренние пути/ожидания допускаются за 7 суток и свыше городских 4 часов.
Start — pickup в origin, end — dropoff в destination; pre-access и final walk
не создают общественный маршрут. Stay-onboard включает dwell один раз, без новой
посадки. Minimum transfer проверяется от alight до departure, не прибавляется к
walk+wait. Unknown/forbidden различаются. Readiness только из явного supplement.

Сохранены: один common A, actual chosen nodes, city-pair vs exact-node, partial
numeric score/PARTIAL_REFERENCE, cap100/REFERENCE_INCONSISTENCY, raw path/wait
при unknown/nonapplicable reference. В последнем случае индексного winner нет.
Автомобиль не входит в public maximum и не заменяет пустой public. Времена bus
не умножаются дополнительно на traffic factor. Mixed = несколько movement modes;
walk между поездами остаётся rail. Субъекты требуют accepted capital/node binding.

PhysicalService идентифицирует конкретное физическое исполнение, aliases и
sequence→departure ID с evidence. Два рейса в одно время не сливаются; повторный
визит run в origin имеет отдельный event ID. Частота — первые физические
отправления с хотя бы одним допустимым продолжением, не число плеч/комбинаций.
Общий count — union по видам. Missing identity сохраняет known subset и count=None,
не уничтожая известный score; physical_boardings_confirmed отдельно от operational
boardings/transfers. Frequency period явный [start,end); при выходе за analysis
first window запускается отдельный поиск с собственным результатом/бюджетом.

Rich result хранит исходные supplements, normalized Timetable с versions/horizon,
маршруты и interleaved steps, breakdown, chain_complete, physical boarding state,
A completeness, timetable coverage, applicable modes, optimum_proven,
frequency completeness, best-known и причины. Resource limit не превращает
прерванный поиск в full; score100 не завершает частоту. Declared data coverage
отделена от алгоритмического завершения. Полный no-route, unknown и interruption
различаются; max(empty)/0-path/Infinity не создаются. Нет SLA на национальную сеть:
размер Pareto front и нагрузка требуют отдельного production измерения.

### Проверенные контроли

- A1800: R1 start0/path3600 → 70.710678; R2 start3600/path1800 → 97.153194,
  **выигрывает позже прибывающий R2**, компоненты одной и той же цепочки.
- W043200/path7200/total50400, ride1800 + walk600 + wait1200 + ride3600;
  alight_to_departure minimum1800 не прибавляется второй раз. A3600 → 50/50/50.
- Through run 08:00→08:30–08:40→09:00: path3600, одна посадка/0 пересадок.
- Inclusive start / end−1µs / exclusive end, продолжения после7d, >4h wait,
  run-specific compatibility, unknown vs forbidden, boundaries и readiness.
- By-mode rail/mixed сохраняются; car100 не повышает общественный score.
- Два aliases одного physical departure, несколько продолжений, второй реальный
  рейс в одно время → count2. Rail/mixed пересекаются, public считается по union.
- DST/UTC/IANA, normalized >24h service dates, inactive/unknown calendar,
  event/calendar evidence не заменяются alias proof; no-route vs unknown/resource.
- Partial/missing/mismatched reference, cap/ties, zero-time walk/transfer cycles,
  permutations/hash seeds, separate frequency period и immutable inputs.
- Независимый exhaustive oracle на **35** конечных расписаниях из raw events
  перебирает целые цепочки без engine queue/dominance/scorer и проверяет максимумы
  по режимам/public, raw times и физические частоты. Это не earliest-arrival oracle.

### Фактически выполненные проверки

```sh
PYTHONPATH=packages/engine/src uv run --frozen pytest packages/engine/tests -q
uv run --frozen ruff check packages/engine
uv run --frozen ruff format --check packages/engine
PYTHONPATH=packages/engine/src uv run --frozen mypy --strict packages/engine/src packages/engine/tests packages/engine/examples
uv lock --check
uv pip check
uv run --frozen python scripts/check_product_config.py
git diff --check
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_intercity.py
```

**782 passed** против исходных742; Ruff clean/111 formatted files; strict mypy
clean/108 Python files. 12 executable README controls и все 8 standalone synthetic
examples исполнены, включая прежние walk/districts/transit/car/od/traffic/reference.
Lock resolved157, pip checked56 compatible; product config valid; diff clean.
Сохранены все прежние **183** exports/order/signatures/defaults/fields/frozen,
включая старые 141/168 guards; добавлено18, всего **201**. Архитектурный guard
добавляет ровно scheduled/timetable_models/road к intercity→routing.reference;
reverse routing→intercity/urban запрещён. Старый solver и urban не изменены.
Предсуществующее pytest-asyncio fixture-scope warning сохранено, это не failure.
Manifests/lock/config/canonical DTO/чужие модули не менялись.

### Конкретный handoff

Опубликованные ветки прочитаны через fetch/show без merge:
BACKEND `6ce41795572b3708dc09f49868a3cefaa7e77fb3`,
ML2 `0e6dbca005674f86c5cde7c2f2087477d509e7df`.

**ML2 — packages/data_pipeline/**: согласованные executions/stop events, service
calendar/exceptions/UTC timestamps, >24h преобразование и явное transport mapping;
accepted alias groups с physical event IDs/evidence, directed footpaths и
run-specific alight_to_departure rules, scope/versions и continuation coverage.
Regions — явный accepted capital mapping. Не склеивать n-е строки независимых
таблиц. В текущем adapter calendar_version=not_collected; реальные расписания и
полный межгородской scope не подключены. Приёмка: controls example и unknown
coverage воспроизводимы на принятых входах, без потери identities/версий.

**BACKEND — packages/contracts/src/a2_contracts/v2/{dataset,journeys}.py,
apps/api/**: handler AcceptedData→нормализованные inputs→analysis. README содержит
точный JourneyResult/Leg mapping: canonical departure_at — **первое отправление**,
не routing TimetablePath.departure_at=requested; total=W0+path, legs сохраняют wait.
Nullable score не скрывает available raw journey. Хранить rich result/supplements:
canonical ScheduledService/ServiceStopTime и Transfer пока не выражают physical
alias events, run-specific permission/minimum, readiness и независимые proof /
frequency states. trip_identity_confirmed не заменяет codeshare identity.
Приёмка: R2 выиграл, mixed breakdown50, frequency2 и union, unknown A оставляет
raw times, partial не становится full rating, car не входит в public.

**DEVOPS — infra/worker**: согласовать immutable snapshot/graph/calendar/transfer
versions, реальный accepted horizon и CPU/RAM/resource budget. При отмене хранить
best-known отдельно от proven winner; frequency завершать независимо от cap100.
Измерить runtime/RAM на реальных accepted данных. HTTP/API ingestion и jobs не
входят в engine. Реальные маршруты России, backend E2E и production performance
не запускались и не объявляются PASS.


## Грузовой анализ: инфраструктура, полная доставка и pipeline — 09.11.2026

Рабочая папка пользователя:
`/Users/matveyyuri/Desktop/Общее/хакатоны/case-champion-hac`, `work/ml1`.
Исходный HEAD `48d36dca0e2cfd4b9c95b29e6d920504c9ea3946`; tracked tree был чист,
ff-only pull — Already up to date. Самостоятельно подтверждены **782 passing tests**
и **201 public exports**. a2-dev.bundle оставлен untracked и не изменён.

### 1. Городские грузы

`freight/models.py`: immutable CargoProfile, CargoTemperature (включая Celsius
range), CargoVehicle, CargoContext и CargoCompatibility. Сохраняются независимые
food/nonfood, urgency, temperature/control, handling/access/vehicle constraints,
profile ID/version. Requirements hash включает полное физическое содержимое,
но не urgency. Подтверждения привязаны к ID/context/profile/hash/service/direction/
периоду/evidence. Unknown/forbidden/confirmed различаются, подмена binding
отвергается. Никакая подлинность внешнего источника самим engine не утверждается.

`freight/truck.py`: отдельный TruckRoute, network + два access elapsed time,
vehicle/costing/profile/build/graph/query time и bound TruckSearchReceipt.
Это не переименование auto RoadResult; минимальность пары подтверждает внешний
принятый executor receipt, согласованный с fingerprint/query hash/evidence.
`freight/urban_analysis.py`: analyze_urban_cargo проверяет expected physical-site
roster, дедуплицирует одинаковые site records, отвергает конфликты, выбирает
ближайший по времени совместимый узел каждого выбранного типа. Outbound site→node,
inbound node→site, без зеркалирования. Неизвестная потенциальная альтернатива,
неполный каталог/roster или неподтверждённый per-pair минимум блокируют полный
индекс, оставляя observed times и best-known compatible route.

Mapping node-specific: port→port, rail_terminal→rail_freight_station,
logistics_terminal→**один** принятый terminal или logistics_center с evidence.
Airport/pipeline_terminal не маскируются этими категориями. Profiles/directions/
node types не усредняются между собой. H — административная площадь и текущая
конфигурация. shared/area.py выделен без freight→urban: прежний public
urban.select_h_seconds остаётся canonical owner с прежней сигнатурой/поведением.
После выбора вызываются прежние urban_cargo_site_index/territory_index,
физические площадки равновесны. Scalar-модули не менялись.

Коммит подблока: `a36ca45`; контроль H1200, времена1200/2400 → **50/25/37.5**.
Быстрый запрещённый port600 не обгоняет compatible1200; unknown600 не доказывает
full minimum. Reverse1800 не подставляется из outbound600. Missing expected site
не исключается из агрегата. Passenger auto не принимается как truck proof.

### 2. Полная межгородская доставка

`freight/delivery/models.py`, `search.py`, public __init__: scope/state/action/
wait-policy/readiness/interval/path/mode/full-analysis. analyze_intercity_cargo
сам ищет допустимую цепочку до completed state, включая исходную и конечную
обработку. shared/config.py добавляет CargoDeliveryRules и approved mapping/path
loaders; старый FreightConfig не меняется.

Execution graph хранит prepared/in-transit/conditions/операции и направления.
Из начального unprepared state нет движения до явной loading либо принятой
not_required zero operation. Movement требует согласованного execution, активного
календаря и explicit fixed departure, включая road. Passenger footpaths и наличие
дороги не создают freight service. Initial readiness проверяется на общий start
для всех заявленных initial states; missing/unknown не становится absence.
Подтверждения temperature/handling/условий покрывают каждую нужную операцию,
движение и положительный gap; gap без CargoWaitPolicy не становится ожиданием.
Initial wait исключает уже учтённую погрузку, transfer wait отделён от обработки.

Алгоритм — exact priority enumeration конечного принятого графа конкретных
executions/obligations. Ключ содержит state ID, точное ready UTC time, origin,
использованные action IDs, modes и first departure. Более раннее время само по
себе не отбрасывает другое состояние с отличными допустимыми продолжениями.
Action ID используется максимум раз: повторное реальное событие требует другого
ID. Это отражает семантику уникальной execution-записи, а не произвольный лимит
пересадок. Zero cycles не улучшают tie-break. Выбирается самое раннее **completion**,
затем меньше actions и stable IDs, независимо от отображаемого округлённого score.
Best-known completed candidate сохраняется даже при прерывании до его settlement.

Все movements имеют явный scheduled_at; handling без appointment означает
обязательную немедленную обработку при ready, не свободное continuous window.
Если у источника есть другие допускаемые appointments/условия ожидания, их нужно
передать принятыми альтернативными actions. Неописанная continuous availability
не заменяется дискретизацией. Алгоритм точен для переданной конечной области;
frontier может быть экспоненциальным, national-scale SLA не измерялся.

Окно первого отправления [start,start+31d), для всех срочностей. Continuations и
terminal handling могут быть позже. D24/72/744h — half-life, не deadline. Pipeline
и reference A не входят в cargo maximum. Сохраняются road/rail/air/water/mixed
и overall, без усреднения профилей. Mode определяется движением, не handling.
Unknown handling сохраняет known movement/prefix, но total/index unknown. Known
complete path при incomplete alternatives сохраняет preliminary score без proven
winner. Missing calendar/compatibility/horizon/resource не подтверждают no-route.

Коммит подблока: `0a299f4`. Контроль от00:00: wait3600 + movement57600 +
handling18000 + transfer wait7200 = **86400, urgent index50**. Поиск строит
load→road→transfer→rail→unload. Arrival10h+unload8h проигрывает 12h+1h=**13h**.
Неизвестная unload сохраняет movement57600 без total/index. Loading2h запрещает
departure01:00 при готовности00:00 и допускает03:00. D/2D →50/25 для трёх
срочностей; microsecond boundary31d, UTC/DST, fractions, zero operations/cycles,
versions/ID conflicts и resource best-known проверены. Independent raw-action
DFS oracle на **25** малых сетях сравнивает completion/action path и breakdown.

### 3. Pipeline connections и публичный API

`pipeline/analysis.py`: PipelineContext, Endpoint, Connection, Measurement,
Observation, AnalysisScope, ConnectionAnalysis/ConnectionsAnalysis и
analyze_pipeline_connections. Проверяется выбор принятых directed **end-to-end**
connections пары с подтверждёнными intake/delivery endpoints, territory/product/
validity/version/evidence. Из A→X и X→B сквозная связь не выводится. Геометрия,
транзит или название территории не создают подключение. Ограничения композиции
сохраняются явно: для неё нужны принятые правила стыковки и end-to-end semantics,
не гидравлика/max-flow или вымышленная commercial availability.

Сопоставимые V/Q привязаны к connection/direction/product/normalized units/
точному периоду/context, затем вызывают прежний pipeline_utilization. Даже
одинаковая системная basis не подставляется на выбранную пару. Incomparable →
INCOMPARABLE_PIPELINE_DATA; Q<=0 отвергается; U>100 сохраняется с QA. Нет aggregate
U, среднего процентов, суммы Q/V участков или утверждения Q−V о свободной мощности.
Связь, quantity availability и catalogue completeness хранятся независимо.

Коммит: `7bffb31` (pipeline, общий public API и final resource/identity guards).
Контроли oil A→B: **V120/Q100 →120% + UTILIZATION_ABOVE_100**; reverse/transit C/
gas не выводятся. Unknown V/Q сохраняет connection, V0 даёт U0 при существующей
связи. Empty partial catalogue unknown, full empty confirmed_absent. Records50/80
не дают общего65. Новый API добавляет36 символов, всего **237**; все прежние201
signatures/defaults/fields/frozen/order сохранены. Snapshot201 снят до изменений,
исторические141/168/183 оставлены. Все импорты абсолютные; freight→urban и reverse
routing→freight запрещены guards. Manifests/lock/config/canonical DTO не менялись.

### Проверки

Исходные **782 → итоговые830 passing tests**. Фактически выполнены:

```sh
PYTHONPATH=packages/engine/src uv run --frozen pytest packages/engine/tests -q
uv run --frozen ruff check packages/engine
uv run --frozen ruff format --check packages/engine
PYTHONPATH=packages/engine/src uv run --frozen mypy --strict packages/engine/src packages/engine/tests packages/engine/examples
uv lock --check
uv pip check
uv run --frozen python scripts/check_product_config.py
git diff --check
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_urban_cargo.py
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_delivery.py
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_pipeline.py
```

Ruff clean, **127 files formatted**, strict mypy **124 files clean**, lock
resolved157 / pip checked56 compatible, product v3 valid, diff clean. Все **15**
README executable controls и **11** standalone synthetic examples исполнены,
включая прежние8. Hash seeds/fresh-process IDs проверяются для всех трёх новых
analyzers. Предсуществующее pytest-asyncio fixture-scope warning сохранено.
Реальные truck HTTP, грузовые executions РФ, pipeline подтверждения территорий,
совместный backend E2E и production resources не проверялись и не названы PASS.

### Точный mapping и передача другим ролям

Прочитаны и сверены remote HEAD:
BACKEND `6ce41795572b3708dc09f49868a3cefaa7e77fb3`,
ML2 `0e6dbca005674f86c5cde7c2f2087477d509e7df` (без merge).
README содержит field mapping canonical DeliveryCargoProfile/VehicleConstraints/
FreightSite/FreightNode/FreightCompatibility/PipelineConnection/Quantity и
Metric/Score/SubjectRef/evidence. Неуточнённый logistics_terminal не дублируется
в terminal/logistics_center; canonical airport/pipeline_terminal не переименованы.

**ML2 — packages/data_pipeline/**: expected physical-site roster, принятый
node-specific mapping, profile contents/version/evidence compatibility, truck
connectors и constraints; cargo executions/calendars/readiness/handling/wait
conditions, достаточный declared horizon/coverage. Pipeline — end-to-end directed
connections с intake/delivery и period-normalized V/Q. В текущем adapter
freight_compatibility/pipeline_connections пустые, calendar=not_collected.
Приёмка: реальные records проходят binding validation без guessed permissions,
seconds, urgency/temperature assumptions и скрытой потери ожидаемых площадок.

**BACKEND — contracts/API**: собрать нормализованные profiles с Celsius ranges,
vehicle/access units и недостающими width/length/class/access supplements;
контекстные receipts со service/period/hash bindings вместо raw compatible bool.
Сохранить богатые results, scopes, observed/best-known, nullable full total,
compatibility vs completeness vs proof, expected rosters и отдельный pipeline.
Приёмка: controls37.5/86400→50/120% воспроизводятся; missing/unknown не получают
full rating/0; pipeline не проходит delivery formula и U не подписывается score.

**DEVOPS/executor — infra/routing**: freight truck costing/profile и проверенные
vehicle/access rules, fixed graph/build/options, directed connectors с physical
elapsed seconds и применимыми scoped minimum receipts. Для cargo — согласованный
finite temporal model/horizon и worker CPU/RAM/resource budget. Нужны реальные
performance измерения и joint E2E; неизвестные данные не заменять подтверждением
минимума/нулевым временем. HTTP/production deployment этим этапом не выполнялись.

## Scenario Engine v3 — 09.11.2026

Работа выполнена ML1 в основной папке PyCharm
`/Users/matveyyuri/Desktop/Общее/хакатоны/case-champion-hac`, ветка `work/ml1`.
Исходный HEAD `e5104d66695e4650d19e8953d273ff0c3656574b`; remote проверен,
`pull --ff-only origin work/ml1` не потребовал обновления. Исходный чистый tracked
state и независимая регрессия **830 passed in8.61s** зафиксированы до изменений;
неотслеживаемый `a2-dev.bundle` сохранён. Snapshot всех237 API signatures/defaults/
fields/frozen снят до добавления нового API, исторические141/168/183/201 сохранены.

### Реализованный процесс

`scenario/` не содержит IO/LLM и использует существующие вычислительные функции.

- `models.py`: frozen bundles именованных timetable/urban/intercity/cargo/urban
  cargo/pipeline jobs, operation с dated execution/event/hash/evidence, полные
  before/after/metrics/effects/impact, отдельные resource constraints/feasibility.
- `identity.py`: canonical content hashes для nested dataclasses/frozen config
  mappings, детерминированные IDs, сбор provenance/evidence без live refresh.
- `engine.py`: create_scenario_baseline, simulate_scenario,
  validate_scenario_result. Before выводится из normalized inputs; проверка
  публикации повторяет расчёт и отвергает самосогласованную фальсификацию after.
- `overlay.py`: проверка актуального allowlist и содержания операции, dated
  execution hash, immutable whole_execution/departure_tail edits, согласование
  alias copies, календарей/transfer/walk proofs; constrained shifted trip copy
  для additional departure; cargo movement departure coordination.
- `recompute.py`: полный rerun ограниченной области прежними urban timetable
  search/analysis/district/last-return, intercity passenger, intercity cargo,
  urban cargo и pipeline analyzers. Выбор другого лучшего объекта/пути допустим.
- `effects.py`: semantic metric roster, прежние signed comparisons, все улучшения,
  ухудшения/потери, unchanged и unknown; непропускаемая полнота worsening IDs.
- `feasibility.py`: feasible/infeasible/unverified отдельно от математического
  эффекта, actual execution occupancy, availability и accepted turnaround,
  coverage/validity/calendar/infrastructure; общий ресурс между network extracts.
- `impact.py`: unique original cells/physical sites; известная нижняя граница и
  неизвестная часть. Несколько категорий/jobs не удваивают население ячейки.
- `examples/synthetic_scenario.py`: самостоятельный полный baseline→operation→
  overlay→search→after→effects→impact→feasibility, повтор и immutable assertions.

H/τ, common A, population/shares/priorities, scope/request/rules/config/methodology,
graph/build/snapshot/calendar/traffic identities заморожены. Меняется только
явно разрешённое расписание и его timetable version. Новые physical events имеют
before=None. Overlay не принимается вместо baseline; повтор не удваивает shift.
Низкоуровневые domains не зависят от scenario, guards включают facades; все импорты
абсолютные. Добавлен31 публичный символ, всего **268**; прежние237 сохранены.

Allowlist сверен read-only с BACKEND
`6ce41795572b3708dc09f49868a3cefaa7e77fb3`, ML2
`0e6dbca005674f86c5cde7c2f2087477d509e7df`, без merge. Фактические server templates:
`coordinate_transfer`, `additional_departure`. Engine не добавляет свои templates.
Coordination timetable/cargo реально перепоисковывает маршруты; cargo handling
seconds/profile/urgency не редактируются. Дополнительный timetable рейс требует
accepted demand/physical ID/calendar/departures и feasible resources/turnaround.
Cargo additional, handling-duration, truck/terminal и pipeline interventions
отклоняются. Urban cargo и pipeline запускаются настоящими analyzers, но их входы
при schedule-only операции не меняются, соответственно результат неизменен.

### Контроли и фактические проверки

-08:10 arrival, minimum5мин, outgoing08:12→08:15: новая поездка1500с;
для других пользователей1320→1500, ухудшение−180с сохранено. Population90+10
с двумя категориями учитывается один раз по ячейкам.
-30→35 и60→40 сохраняют−5 и+20.11 атак на omission/duplicate/IDs/units/semantic
keys/null/unknown/new_connection/route identity и скрытый worsening отвергаются.
-Downstream missed transfer даёт confirmed lost_connection, null after time и
nullable delta, присутствует в worsened IDs; unknown transitions не маскируются.
-H=τ=1200, time1500→900: индекс/count пересчитаны без замены H/τ, weight90/10
и district aggregates сохранены. Смена best destination и last return пересчитаны.
-Пассажирский межгород: допустимость/целая цепочка/индекс/частота заново, A прежний.
-Грузовая rail coordination: полная доставка86400→82800с, конечная обработка
учтена, индекс растёт; physical warehouse считается один раз. Urban cargo/pipeline
не изменяются. Без resource proofs unverified; принятые occupancy/constraints feasible.
-Ресурсный overlap подтверждает infeasible; конфликт across network extracts
не теряется. Inactive/unknown/unproven соседний рейс не создаёт подтверждённого
нарушения и не скрывает overlap двух доказанных исполнений. Out-of-period envelope unverified, conflicting physical IDs отвергаются.
-Неполные data/search/population shares остаются partial/unknown/lower bound;
operation evidence не заменяет отсутствовавшие stop/action proofs.
-11 вручную изменённых малых расписаний сравнены с полным rerun; повтор,
перестановки, разные hash seeds, соседние даты и static/typical/τ identities проверены.

Итог: **872 passed in11.86s**, прирост42:40 scenario cases + snapshot237 + reverse
scenario dependency guard. Фактически выполнены:

```sh
PYTHONPATH=packages/engine/src uv run --frozen pytest packages/engine/tests -q
uv run --frozen ruff check packages/engine
uv run --frozen ruff format --check packages/engine
PYTHONPATH=packages/engine/src uv run --frozen mypy --strict packages/engine/src packages/engine/tests packages/engine/examples
uv lock --check
uv pip check
uv run --frozen python scripts/check_product_config.py
git diff --check
```

Ruff clean,138 Python files formatted; strict mypy135 files clean; lock resolved157,
pip checked56 compatible; approved product v3 valid. Исполнены **все16 README
executable controls и12 standalone synthetic examples**, включая прежние грузовые.
Предсуществующее pytest-asyncio fixture-scope warning сохранено. Реальные транспортные
данные РФ, production resources/performance и общий backend E2E не объявляются PASS.

### Интеграционный handoff

Полные field/API mapping, inputs, semantics и запуск — последняя секция engine README.

**BACKEND — apps/api, packages/contracts:** собрать baseline из принятого snapshot,
сохранить immutable inputs+operation+constraint bundle; добавить engine supplements
service_date/event/expected execution hash/incoming pair/timing model/physical
bindings к текущему ScenarioOperation DTO. Согласовать whole_trip/subsequent events
с departure_tail. Feasibility DTO verified/unverified недостаточен для infeasible;
feasible отображать verified только в принятом scope. Mapper должен сохранить
semantic IDs, rich before/after, nullable connection effects, все ухудшения/unknown
и независимые completeness/impact/feasibility. Перед атомарной публикацией вызвать
validate_scenario_result; урезанный список frontend не является полным result.
Приёмка: attacks на пропуск/подмену known worsening блокируют публикацию.

**ML2 — packages/data_pipeline:** полные dated execution/event/calendar/alias
bindings, accepted transfer/walk permissions; CargoExecution actions/service dates,
readiness/loading/handling/wait/compatibility и unique physical sites. Resource/
vehicle blocks с полным набором соседей, accepted availability/turnaround/boundary
minimums, validity/coverage/evidence. Без них модельный результат unverified,
unknown данные не превращаются в нулевые нормы или invented permissions.

**DEVOPS — infra/workers:** budget выбранной области CPU/RAM/time, resource-limit
partial, воспроизводимый input bundle/retry и реальные performance измерения.
Учитывать стоимость полного before/after и повторного publication guard; SLA на
всю страну не заявлен. Общий E2E, реальные accepted данные, handler mapping и
итоговая приёмка остаются совместной интеграции. Чужие пакеты/manifests/lock/config
и a2-dev.bundle не изменялись; merge/rebase/force-push не выполнялись.

## Независимый аудит ML1 — 09.11.2026 (частичный, полный этап открыт)

Исходный9a2070085671bd3314e3ea8dca0e01bf406d57bc, основной PyCharm checkout,
work/ml1; independently872 passed. Подтверждены и исправлены P1 shared cargo
execution across jobs и преждевременный численный underflow трёх формул; исправлен
P2 quadratic population impact. Новые тесты сначала воспроизвели дефекты.
Дополнительно40 raw-graph DFS oracle checks и300 Decimal/metamorphic controls.
Итог881 passed/13.10s, Ruff clean, strict mypy137 files, lock/pip/config/diff clean.
16 README controls +12 synthetic examples PASS; API268 сохранён.

Измерения impact2000:0.479→0.057s, traced memory324→495KB. Walk chain2000:
2001 states,0.040s,16.54MB; API materializes full paths, memory O(V²) на цепи.
Повторяемый script: packages/engine/examples/benchmark_audit.py.

Полный отчёт, coverage/integration matrix и фактические пределы проверки:
packages/engine/AUDIT.md. Полный независимый разбор всех97 модулей и large
schedule/cargo/OD/scenario stress НЕ завершены: не объявлять весь финальный аудит
закрытым. Bare package import без PYTHONPATH падает: DEVOPS owns engine manifest
(package=false). BACKEND needs dated event/hash/timing supplements, infeasible DTO
and lossless full-result publication; ML2 needs shared physical execution IDs,
calendars/resources/compatibility. Актуальный ML2 remote216676a уже содержит
accepted городские bundles/справочники; их реальное ingestion/E2E здесь не проверено.
Чужие модули, manifests/lock/config и a2-dev.bundle не изменялись.

## Завершение оставшихся проверок аудита — 09.11.2026

Исходный HEAD8f2e3bbf31ac5232481d91158491b98f45e09b73, основной PyCharm checkout,
work/ml1, FF без обновления. Независимо881 passed/12.19s. Предыдущие исправления
сохранены. Новый P1: additional_departure создавал рейс только в target timetable,
не в других datasets того же физического исполнения. Новый тест сначала упал;
closure physical aliases теперь применяется к обоим шаблонам и новый рейс виден
всем связанным jobs. Проверены before/after, repeat, unchanged baseline, publication
attack и mismatch operation. Чужие даты не включаются через один trip_id.

Новые independent raw DFS oracles:30 seeds расписаний (urban min arrival + wait cap,
intercity whole-chain score/mode/frequency); cargo widths1/2/3/5 с terminal handling,
unknown unload и лимитами; Fraction OD/28 slices, conflicting/idempotent batches,
wrong mode/missing pair; last return after midnight/DST;7/31-day half-open boundaries.
41 новых case, общий результат **922 passed/13.69s,0 skipped**. Ruff check/format144
files, strict mypy140 files, all16 README controls/12 synthetic examples,
lock/pip/config/diff PASS. Public API268 и compatibility/architecture guards сохранены.

Нагрузки:60/240 timetable runs; intercity396/6858 states,0.43/4.15s;
cargo width3/8,20/90 states,0.052/0.739s; OD12/32points,3696/27776pair-slices,
0.305/2.307s; scenario4/16jobs,0.585/3.032s. Полные peak tracemalloc measurements,
повтор walk500/2000, сложность и воспроизводимые команды — packages/engine/AUDIT.md.
Walk predecessor rewrite не обоснован: materialized paths для всех nodes имеют
O(V²) output lower bound на цепи, незаметная смена публичного API недопустима.

Вердикт: **READY WITH EXTERNAL DEPENDENCIES** в нормализованной проверенной области.
Закрыты оставшиеся критические oracle/stress-пункты; подтверждённых неисправленных
P0/P1 в выполненных проверках нет. Full production E2E не заявлен.
DEVOPS: build-system + src package discovery + установка engine, isolated wheel
import без PYTHONPATH; config explicit deployment path. Минимальный TOML и
acceptance test приведены в AUDIT.md; манифест не изменялся.
BACKEND: infeasible DTO, dated event/hash/incoming/timing supplements; текущий
whole_trip validator несовместим с departure_tail — не публиковать ложный whole_trip;
lossless evidence/nullable/completeness/all worsening и atomic publication.
ML2: accepted resources/calendars/physical IDs и достаточные normalized data.
Data/bundles в checkout отсутствует,2 readiness CLI не запускались с реальными
данными; все synthetic examples выполнены. Ветки BACKEND6ce4179 и ML2216676a
проверены read-only. Merge/rebase/force-push и изменения чужих пакетов не выполнялись.
