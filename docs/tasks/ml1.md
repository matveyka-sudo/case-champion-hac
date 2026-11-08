# ML1

Ветка work/ml1. Пути: packages/engine/.

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
