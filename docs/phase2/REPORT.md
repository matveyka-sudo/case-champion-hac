# Phase 2 — source-audited schools and the minimum-time proof boundary

Дата проверки: 10.11.2026. **Первый достоверный индекс пока не получен.**
Реальный частичный E2E воспроизведён на новой AcceptedData `.3` и заново
построенном Valhalla graph. Сохранены `partial`, `minimum_proven=false`,
`index=null`, `ranking_eligible=false`. Это инженерная поставка для следующего
этапа, а не объявление готовности ограниченного или городского индекса.

## Baseline и интеграция

PR #26 остаётся OPEN/DRAFT, base `dev`, merge state CLEAN:
`dd46659f2500844285b3c8849b756fe88664a39a`.
Проверенный удалённый `dev`: `91ed5262b62c16afa3ca619f8140c43917cf8325`.
Baseline snapshots, locks, graph/profile/runtime hashes и старый результат
закреплены в [baseline.json](baseline.json). `.2` не изменена.

Изменения объединены merge-коммитами в
`integration/verified-indices-20261110`. Проверенный implementation HEAD после
последнего исправления oracle: `47eddf8e7750ef28b1aa4453ea874e0ee611f6b3`.
Merge в `dev`, force push и удаление постоянных work-веток не выполнялись.

Работали три реальных дочерних агента **GPT-6.1 Sol / High**. Одновременно
доступны два дочерних слота; работы и повторные review выполнялись волнами.

| Владелец / ветка | Поставка | Проверенные коммиты | Итог |
| --- | --- | --- | --- |
| BACKEND / `work/phase2-backend` | Provider guards, название школы, batch design, bounded `.2/.3` runtime | `eb942ce`, `5a39425`, `fb9c4d6` | INTEGRATED |
| ML2 / `work/phase2-data` | 54+64 row audit, official-source reconciliation, immutable `.3` | `aa5fb0d` | INTEGRATED |
| ML1 / `work/phase2-ml1` | Exact finite reference oracle, proof-boundary research, regressions | `f463239`, `5c6df75` | INTEGRATED |
| Координатор / integration | UI, baseline, runtime selection, собственные replay/review | `2f713b2`, `30159b3`, `827ad2c` | INTEGRATED |

Каждая поставка прошла REVIEW по diff, ownership, SHA и проверкам координатора.
ML2 дополнительно независимо проверил oracle через Bellman–Ford на 2 000
синтетических графов (seed 17011). Найденная P3-асимметрия solver/verifier для
нулевого пути из точки в неё же получила CHANGES_REQUIRED; исправление
`5c6df75` повторно проверено reviewer и ACCEPTED. BACKEND независимо проверил
UI-исправление без изменения frontend-файлов.

## Исправленные дефекты PR #26

1. Некорректные вложенные контейнеры Valhalla (`trip`, `summary`, `locations`,
   `legs`, `maneuvers`) валидируются перед чтением полей. Ошибка провайдера
   сохраняет unknown, ненулевое H и null score; не становится unreachable.
   Добавлены 18 негативных provider cases. Формулы и DTO не менялись.
2. Карточка sample point показывает переданное население ячейки даже без
   отдельной cell geometry. Положительное значение, ноль и null проверены
   браузерными регрессиями. Неподтверждённая ссылка `cell_id` не добавлена.
3. Название школы берётся из checksummed accepted bundle. Реальный UI показал
   «МАОУ СОШ № 12». Entity ID, источники, координаты и время сохранены.

## Данные и фактическое покрытие

Новая версия: `kaliningrad-urban-2026-11-10.3`.
Manifest SHA256: `b174039c6c2d1feb33a46063418e5975afd5bc58095c20e8ff65f73434e25fa1`.
Canonical AcceptedData SHA256: `882030e7dc77f1216bd4ef2c289e65946af814d8b25c4332ddc7cb1f496b416f`.

| Показатель | `.2` | `.3` |
| --- | ---: | ---: |
| Принятые школьные объекты | 76 | 77 |
| Школьные объекты в карантине | 54 | 53 |
| Sample points | 216 | 216 |
| Подтверждённые подходы точек | 190 | 190 |
| Подтверждённые подходы принятых школ | 38 | 39 |
| Unknown подходы точек / школ | 26 / 38 | 26 / 38 |
| Исключённые population cells | 70 | 70 |
| Население исключённых ячеек GHSL | 277,772 | 277,772 |

Добавлен только корпус 2 гимназии №40, пер. Рассветный, 5:
`kaliningrad-school-way-1055458688`. Точный адрес OSM совпал с перечнем
муниципального учредителя, запись 4418524. Сохранён прежний подход 59,1 м;
новая независимая проверка входа этим исправлением не заявляется.
Прежние 76 принятых объектов, все точки, population/access/connector коллекции
побайтно/по моделям сохранены. Отдельный source ID `klgd_school_sites`
не перезаписывает прежнюю версию `klgd_education` в контракте v2.

Все 54 исходных quarantine-записи и все 64 неизвестных подхода разобраны в
[построчном аудите](../../data/manifests/qa/kaliningrad-school-audit-2026-11-10.json).
Координатор повторил его по заново скачанному PBF: JSON совпал побайтно,
SHA256 `547d4eecab260348085fa83f6c4b4dd6824d06da43f31b5d4723613d897a0af6`.
Сохранены конкретные OSM препятствия, теги ворот, исходные пороги и причины.
Ни один unknown-подход не переведён в traversable ради роста покрытия.

[Сверка официальных источников](../../data/manifests/qa/kaliningrad-school-source-review-2026-11-10.json)
фиксирует шесть расхождений перечня и карты: дополнительные корпуса
гимназии №40, школ №3/12, лицея №49; противоречивые номера домов гимназии №1
и лицея №23. Муниципальный перечень также не закрывает региональные/федеральные
публичные школы. Поэтому ни один район или иной независимо обоснованный
scope с полной школьной категорией пока не подтверждён. 77 объектов нельзя
трактовать как 77 разных организаций или исчерпывающий перечень корпусов.

AcceptedData и manifests доступны в Git. Raw HTML, PBF, tiles, HTTP observations
и screenshots остаются вне Git. Публичные источники сейчас скачиваются и
проверяются по SHA. Старый HTML после изменения сайта требует сохранённых
immutable bytes; отдельное удалённое архивное хранилище этих raw HTML в этой
поставке не настроено. Локальные raw snapshots лежат в
`/Users/kirito/waydex-phase2-runtime/ml2/official/`.
Инструкции повторения ML2 audit/revision — в README data_pipeline.

## Маршруты, минимумы и индексы

Формула ML1 не менялась. Reference oracle использует exact Fraction seconds,
directed state graph, edge + transition elapsed time, все initial/final
connectors и отдельно сохранённый inventory. Это проверка переданного
конечного графа, не доказательство полноты реальной сети. Нет автоматической
связи oracle → production `minimum_proven`.

Проверенный Valhalla 3.5.1 оптимизирует generalized cost; `shortest=true`
означает distance objective. `/expansion` тоже не предоставляет требуемый
exact elapsed proof: веса поиска cost, duration сериализуется целым числом.
Шестизначная сериализация привязок не доказывает точную физическую привязку.
Источники, SHA, source-level оговорки и два варианта строгого certificate —
в [FINITE_WALK_MINIMUM.md](../../packages/engine/FINITE_WALK_MINIMUM.md).

Достоверно вычисленных accessibility indices: **0**. Размер подтверждённой
индексной области: **не установлен**. Пилот охватывает одну принятую точку
`kaliningrad-utm34n-1km-v1-e468-n6061-p` и один реальный маршрут к школе №12,
а не полную категорию для этой ячейки. Её население 13 245,52 — оценка GHSL
2025 для ячейки; не число людей в отдельном здании. Районные и городские
индексы недоступны. Положительное население с unknown временем не исключается
из знаменателя и не заменяется нулём.

## Собственные реальные испытания

Первый новый UI-run на исправленной `.2`: job
`5a694044-e3e3-4d03-a415-ab12cee5827d`, analysis
`48a782b8-0bc3-480d-b17f-ca2ea3af9600`, worker **0,609856 с**.

Затем источники заново скачаны в пустую runtime-папку и собран отдельный graph:
Valhalla 3.5.1, 121 tile, **8,024218 с** сборки; новый manifest
`1e44413dcb0a19befd40774b40584e3b13d1c879fc09130e6a9633557b4964e3`.
Digest контейнера и исходный PBF те же, что в baseline; новая идентичность
сборки не выдана за старую. Это fresh runtime/reinstalled workspace на том же
компьютере и с cached Docker image, не испытание второй ОС/другого компьютера.

На новом graph создан отдельный runtime artifact `.3`:
`a7a09b0576041ab70a9f40a59e4df5e5602b30c3f7c5ce35b4ede779b7e1a72d`.
Проверены CLI rejection неизвестного bundle и несовпадения artifact/snapshot.
Новый настоящий запрос создан через React, без route mocks:

- PostgreSQL schema: `a2_phase2_revision3`, migration `0004_alice_budget`;
- job: `a9e79720-ebe4-4d89-b3ae-a918188d8070`, attempts 1;
- analysis: `4ed50919-834d-469e-90ea-05b21294d551`;
- result hash: `77005b0dac8952f39f9eead088ace48bf93c6132d6cc69b14ea34be0c3db93f9`;
- worker время между persisted started/finished: **0,522832 с**;
- физический подход 9,7 + сеть 519,204 + конечный подход 18,5 = **547,404 с**;
- 76 остальных school pairs остались blocked; minimum не доказан;
- canonical DTO/hash и persisted snapshot hash проверены независимо из SQL;
- два GET владельца: 200 с одинаковым hash; другая сессия: 404;
- React: 9,1 мин, принятое название школы, население/год/метод, partial,
  «Нет данных» для индекса и отсутствие полного рейтинга.

Новый результат использует implementation `dd96fd72...`; последующее
исправление reference-only oracle не меняет calculation runtime. Локальные
артефакты: `phase2-e2e-revision3-evidence.json`,
`ui-saved-result-revision3.json`, `ui-point-card-revision3-evidence.json`,
`ui-point-card-revision3.png` в `/Users/kirito/waydex-phase2-runtime/`.

Измеренный snapshot памяти после replay: Valhalla **60,96 MiB**;
PostgreSQL **222,4 MiB** (общий контейнер нескольких схем). Это не peak RSS.
OD-batch throughput не измерялся: реализована архитектура, не batch executor.
Платных запросов, Alice calls и Yandex Rasp calls в Phase 2: **0**.

## Проверки и границы результатов

| Проверка координатора | Результат |
| --- | --- |
| API + contracts + assistant, isolated PG fixtures | 909 passed, 0 skipped |
| Engine после независимого review fix | 982 passed |
| ML2 QA | 225 passed |
| React unit | 39 passed |
| Playwright, synthetic API/demo/UI regressions | 24 passed, 48,5 с |
| TypeScript / ESLint / build / generated API | pass |
| Ruff / formatting / strict mypy | pass: backend 78, engine 142, ML2 34 source files |
| Canonical exports / product config / layout / Compose config | pass |
| Frozen uv locks / installed dependencies / PG head | pass, 74 compatible packages, 0004 |
| Real route replay + fresh graph + UI/PG `.2` и `.3` | pass, partial result only |

Один общий pytest invocation для всех пакетов остановился на существующем
совпадении имён `test_freight.py`; запуски разделены по пакетам как в CI.
Первый standalone mypy без CI `MYPYPATH` дал duplicate module error; с
зафиксированным CI environment все проверки прошли. Chromium внутри macOS
sandbox не запустился (MachPort permission); после разрешённого запуска вне
sandbox браузерные проверки прошли. Эти неудачные invocation не выданы за pass.

CI не запускает real Valhalla replay, raw-source audit downloads или Playwright;
для них приведены отдельные локальные доказательства. Synthetic tests не
считаются real E2E. Успех partial E2E не доказывает готовность индекса.

## Следующий этап и критерии допуска

1. **ML2 — замкнуть источник категории.** Сверить организации, филиалы и
   физические корпуса с муниципальным, региональным и федеральным реестрами;
   разрешить шесть зафиксированных конфликтов; проверить дубли уже accepted
   записей. Зафиксировать источник исчерпывающего списка, дату, ownership,
   геометрию и разрешённые внешние цели. Выбрать минимальный административно
   или содержательно обоснованный scope, не произвольный набор удобных школ.
2. **ML2 — покрыть население и доступы этого scope.** Учесть все положительные
   GHSL cells, исключённые residents и несколько origins при необходимости;
   исходные district fractions сохранить. Для неизвестных подходов нужны
   подтверждённые входы, gates/access и физические path geometry, не повышение
   допуска и не новая прямая через препятствие. Выпустить новый immutable bundle.
3. **ML1 + DEVOPS — source-bound proof.** Реализовать read-only exporter
   pinned graph/profile: exact endpoint states, split edges, все допустимые
   physical access alternatives, elapsed weights и state history. Начать с
   одного bounded OD. Независимый полный state graph либо полный relaxation
   supergraph с доказанным включением всех допустимых путей: только равенство
   lower bound `L` и feasible upper bound `U` допускает доказанный минимум.
   Если `L < U` или источник неполон — minimum остаётся unknown. Округлённые
   HTTP coordinates/durations не подменяют source-level данные.
4. **BACKEND — resumable OD после утверждения входов.** Выполнить
   [batch design](../../apps/api/docs/phase2-batches.md): approved pair inventory,
   cache identity snapshot/graph/profile/connectors/precision/scope/code,
   private committed checkpoints через fenced parent transaction, явное
   возобновление новым submission, cancellation/lease и прежняя atomic
   publication. Нужны crash/restart, lease race, scope leak, changed-graph,
   timeout и cancellation tests. Массовая матрица сейчас не создавалась.
5. **Первый индекс и районы.** После catalog/population/minimum gates вызвать
   существующий ML1 без изменения формулы. Все selected categories и все
   positive-population origins должны иметь корректные значения. Районы
   агрегировать из исходных population contributions, не усреднять их индексы.
   Затем canonical validation → PG → повторный owner GET → React, проверить
   unknown/partial на негативном запуске и воспроизвести в отдельной машине.

## Readiness

| Flag | State | Основание |
| --- | --- | --- |
| ML2_ACCEPTED_DATA_READY | partial | `.3` validated; school/population/access coverage не полное |
| ROUTING_EXECUTOR_READY | partial | Реальный pinned Valhalla replay; elapsed minimum не доказан |
| ML1_ENGINE_READY | yes | Детерминированные формулы и unknown gates проверены; real proof inputs ещё отсутствуют |
| BACKEND_WORKER_READY | partial | Реальный bounded pilot работает; resumable batches пока design |
| POSTGRESQL_READY | yes | Migration head, реальные jobs/results, hashes и session isolation |
| FRONTEND_READY | yes | Корректно показывает bounded partial result и исправленные поля |
| REAL_E2E_READY | partial | Реальная цепочка одной точки/школы выполнена; не полный анализ |
| VERIFIED_ACCESSIBILITY_INDEX_READY | no | Нет одновременно доказанной полноты категории и временных минимумов |

Рекомендация: рассматривать эту поставку как проверенное развитие partial E2E
после PR #26. Не объединять в `dev` без разрешения пользователя и не
обозначать её как готовый accessibility index или рейтинг Калининграда.
