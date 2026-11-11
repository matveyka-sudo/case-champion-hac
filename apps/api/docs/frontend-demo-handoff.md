# Марии: анализы и сохранённые сценарии

Ветка `work/backend`, база `dev`. Канонические DTO находятся в
`packages/contracts/src/a2_contracts/v2`, экспорт — в `contracts/generated`.
Примеры в OpenAPI синтетические: они показывают контракт ответа и не являются
приёмкой данных Максима или контрольными числами Матвея.

1. Получить `GET /api/v1/capabilities`. `analysis_presets` содержит точные запросы
   с `snapshot_id` и methodology/config версией. Отправлять `preset.request` без
   произвольной замены времени, категории, города или режима. Для нескольких
   категорий нужен один опубликованный совместимый пресет.
2. `POST /api/v1/analyses`, `Idempotency-Key`, body = request пресета → 202,
   `job_id`. Повтор одного клика использует тот же ключ. Сохранить session cookie.
3. Опросить `GET /api/v1/jobs/{job_id}`. При `succeeded` прочитать
   `GET /api/v1/analyses/{analysis_id}`. Повторный GET возвращает сохранённую базу.
4. `GET /api/v1/analyses/{analysis_id}/scenarios` возвращает только сохранённые
   сервером предложения. `POST /api/v1/scenarios/{scenario_id}/runs` с новым
   `Idempotency-Key` и `{"schema_version":"2"}` запускает отдельный job.
   После успеха получить `GET /api/v1/scenario-runs/{scenario_run_id}`.
   Тело запуска не содержит изменяемых операций или клиентских before/after.

| Состояние | Контракт и действие интерфейса |
| --- | --- |
| success | 202 — запрос принят, вычисление ещё идёт. `job.status=succeeded` даёт ID сохранённого результата. |
| partial | HTTP 200, `AnalysisResult.status=partial`. Показать имеющиеся метрики, `null` и `reason_code` для неизвестных. Результат не теряется и не превращается в failed job. |
| unsupported | 422, `REQUEST_OUTSIDE_SUPPORTED_PRESETS`, `retryable=false`; `field_errors` указывает `SUPPORTED_PRESETS`. Предложить выбор из обновлённых capabilities. |
| retry | 503, `RUNTIME_INPUTS_UNAVAILABLE_FOR_REQUEST`, `retryable=true`. Запрос зарегистрирован, но входы временно недоступны. Для непринятого HTTP запроса повторять тот же ключ; для terminal failed job новый запуск требует нового ключа. |
| incompatible | 409, `incompatible_versions`. Предложить явное действие «Пересчитать базу» с текущим пресетом, затем получить новые предложения. Старые результаты доступны для чтения. |
| failed job | `error.code` и `error.message` сохраняют причину; `field_errors.location=["worker", stage]` задаёт этап `preflight`, `calculation` или `publication`. Существующие ошибки полей сохраняются. |

В `contracts/generated/openapi.json` есть `accepted` для POST, `success` и
`partial` для GET анализа, `unsupported`, `retry` и `recalculate` для ошибок.
В примере partial неизвестное значение явно `null`, не 0.

Группы остаются отдельным вторичным путём: `POST /api/v1/analysis-groups`, затем
`GET /api/v1/analysis-groups/{group_id}`. Категории идут последовательно,
успешные результаты остаются в `items` при ошибке другой категории. Их индексы
нельзя усреднять для получения общего индекса. Сначала интегрировать одиночную
цепочку выше.

Новая грузовая операция `reduce_handling_wait` имеет `handling_changes` вместо
`service_changes`. Поля и ограничения: `packages/contracts/specs/scenario.md`.
Она допустима только для `intercity_cargo` + `cargo_transport=multimodal`.
Трубопровод и городской грузовой доступ эту операцию не принимают. Ресурсные
факты и длительности поставляются в versioned fixture; контракт этого документа
нужно согласовать с Максимом и Матвеем до регистрации поставки. Не показывать
кнопку только по имени типа анализа: использовать `scenario_operations` пресета
и фактически полученные предложения.

## Обязательное обновление клиента для PR #35

Проверено на `dev` `232642e` и backend PR #35: существующие клиентские схемы
отвергают HTTP capabilities с `analysis_presets`, грузовое предложение и результат
с `handling_changes`. FRONTEND должен перегенерировать
`apps/web/src/api/generated/schema.ts` и `validation.json` командой
`node apps/web/scripts/generate-api.mjs` из актуального backend OpenAPI.
Ослаблять AJV или вручную удалять новые поля из ответа нельзя. Отдельная проверка
схем после генерации принимает все четыре сохранённых DTO: capabilities, baseline,
предложение и результат грузового сценария; это не проверка готовности интерфейса.

- `apps/web/src/pages/ApiAtlasPage.tsx` и `src/api/adapters/request.ts`:
  выбирать опубликованный `analysis_presets[].request` и отправлять его без
  изменений через `/analyses`. Свободный запрос для обычных данных сохранить.
  Полный пресет не разбивать через `/analysis-groups`, если дочерние запросы не
  опубликованы отдельно: backend теперь возвращает синхронный 422
  `ANALYSIS_GROUP_CATEGORY_REQUEST_UNSUPPORTED`, без создания группы/jobs.
- `apps/web/src/pages/ApiAtlasPage.tsx` и `src/widgets/ScenarioComparison.tsx`:
  `reduce_handling_wait` — изменение длительности обработки, а не дополнительное
  отправление. Показывать поля `handling_changes`, ресурс и before/after из DTO;
  пустые `service_ids`/`service_changes` и нулевой `shift_seconds` здесь допустимы.
- `apps/web/src/api/adapters/demoModel.ts` и `src/api/adapters/result.ts`:
  выводить понятные метки `DEMO_SYNTHETIC_TRANSPORT_MODEL` и
  `NOT_A_REAL_CITY_RANKING` для всех модельных снимков, включая межгородские и
  трубопроводные. Не определять синтетику только по префиксу `demo-moscow-`.
  `ranking_eligible=false`, partial, unknown и null должны оставаться видимыми.

Regression gate FRONTEND: фактические DTO проходят строгий decoder; запрос
выбранного пресета совпадает с серверным целиком; грузовой сценарий отображает
обработку и правильные единицы; все пять опубликованных модельных snapshots
имеют маркировку, обычный профиль её не получает. Проверить также пустой список
пресетов. Выполнить API-generation check, typecheck, lint, unit tests, build и
браузерную цепочку API mode → preset → baseline → cargo scenario. Обновление
клиента выполняет FRONTEND; backend-исправления сами по себе не закрывают этот P1.

24 baseline и 21 before/after, браузерные цепочки и холодное время проверяются
после поставки данных и нужного engine интерфейса. Подготовка DTO и контрактные
примеры не подтверждают прохождение этой матрицы.
