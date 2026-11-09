# Подготовка интеграции Waydex · 9 ноября 2026

## Источники и границы

Контракт: `contracts/generated/openapi.json`, DTO `packages/contracts/src/a2_contracts/v2/`, router `apps/api/src/a2_api/api_v2.py`, полный backend handoff `apps/api/docs/frontend-integration-handoff.md`. URL `/api/v1`, schema_version `2`; не использовать исторический app. Формулы PRODUCT_SPEC 2026-11-08.1 / methodology v3 исполняются только engine.

Изменены только `frontend/**` и `docs/tasks/frontend.md`. Ветка work/frontend; merge, commit и push не выполнялись. Проверка выполнена после fetch: HEAD f57d359, origin/dev 3446f7e, origin/work/backend 6ce4179, без переключения/слияния. OpenAPI и router совпадают. В origin/work/backend results.py усилена серверная проверка применимости directed references и обязательных journey operands; wire-поля не изменены. Клиент не дублирует эту доменную проверку и не пытается исправлять отклонённые зависимости.

## Запуск

Использовать Node **24.22.2** и npm из принятого окружения.

```sh
cd frontend
npm ci
cp .env.example .env.local
```

В `.env.local` установить `VITE_DATA_MODE=api`, а `BACKEND_PROXY_TARGET` — в фактический адрес процесса API. Адрес и порт не угадываются. Запустить `npm run dev`, открыть `#atlas`. Для разовой проверки `/?data=api#atlas`; явный `?data=demo` открывает прежнюю маркированную демонстрацию. Без настройки режима сохранён demo по умолчанию. В production режим задаётся при сборке; query-переключатель не меняет готовность backend.

Vite proxy передаёт `/api/v1` и `/health`, **changeOrigin=false**: Host/Origin должны соответствовать браузерному origin для защиты POST. Браузер обращается только к своему origin с credentials:same-origin. Cookie a2_session выдаёт capabilities; JS её не читает и не сохраняет. Отсутствующий/ошибочный proxy показывает ошибку API, а не подставляет fixtures.

## Устройство

- `scripts/generate-api.mjs` создаёт типы Schemas и JSON Schema для AJV из реального OpenAPI, с SHA256 источника. `npm run api:generate`, затем `npm run api:check`. Генератор покрывает используемый в опубликованной схеме JSON Schema dialect; generated-файлы руками не менять. Не установлен openapi-typescript 7: его peer TypeScript 5 несовместим с закреплённым TypeScript 6. Обхода peer-зависимостей нет.
- `api/client.ts`: session bootstrap, безопасный ErrorEnvelope, field_errors/request_id, 15-секундный таймаут попытки; максимум два повтора для GET/идемпотентных POST, только network/429/retryable503. Retry-After учитывается, при ожидании свыше 15 секунд автоматический повтор прекращается. Один UUID и неизменное тело на логическую операцию. Explanation и cancel автоматически не повторяются.
- `api/adapters/request.ts`: реальные IDs/level/timezone, категории/приоритеты, traffic, areas и зарегистрированные грузовые профили. Temporal отвергает несуществующее/неоднозначное местное время. Pipeline — отдельная операция и продукт, без смешивания с delivery.
- `features/analysis/useAnalysisSession.ts`: один контроллер API-сессии с draft revision, последовательным polling, предыдущим snapshot и отдельным scenario result. Нет восстановления задания через localStorage. Смена параметров отменяет локальное ожидание; серверная отмена только кнопкой.
- `ApiAtlasPage` и `ApiMap`: API-композиция в существующих токенах, controls, панелях и анимациях Waydex. Прежняя demo-композиция остаётся изолированной: её виджеты напрямую вычисляют presentation selectors над fixtures, поэтому подстановка DTO в них смешала бы данные. API-путь не импортирует mock model/gateway и не вводит второе глобальное хранилище. Это осознанная граница миграции, а не перенос всего приложения на другой state stack.

API-карта получает только server map_data/journeys. GeoJSON передаётся Leaflet без ручной перестановки [lon,lat]; renderer сам преобразует его. Геометрия отсутствует — числа сохраняются, сетка не выдумывается. Схемы помечены, агрегат выбирается по calculation_kind; неоднозначные категории не усредняются локально. Карта сохраняет масштаб при работе с чатами/фильтрами; новый анализ может приблизить геометрию. Сценарий не содержит собственной геометрии в текущем DTO: показывается явно подписанная базовая геометрия без подмены маршрутов.

Карточки сохраняют unknown/null/partial/pending/not_applicable/methodology_pending. Дополнительные counts/jobs не скрывают готовые scores. last_returns и counts доступны в дополнительных показателях; reference_ids/calculation_kind и исходные references сохранены в DTO для связки, но не выводятся как отдельные пользовательские показатели; автообновление immutable snapshot по dependency_job не заявляется. Рейтинг не достраивается. Источники открываются только по HTTP(S). Прибытие форматируется в IANA-зоне назначения из сохранённого запроса и каталога, без догадок по названию.

Объяснение привязано к saved analysis_id/hash и selection, история локальная. Claims связываются с сохранёнными metric_ids, текст не исполняется как HTML. Template — допустимый fallback. Кнопки сценариев только из списка серверных proposals, с baseline hash и editable_local_graph; идеи из текста агента сами запуск не разрешают. Delta, знак, исход new/lost connection и охват берутся из сервера. База не перезаписывается.

## Проверки и данные

`tests/fixtures/api` — **synthetic-only**: четыре result payload из канонических backend fixtures, capabilities/каталог и сценарная обёртка по опубликованным DTO и backend storage-тестам. Они используются только unit/E2E, не импортируются в API runtime. Синтетическая геометрия в E2E проверяет [lon,lat] и связи по ID, не является географическими данными проекта.

```sh
npm run api:check
npm run typecheck
npm run lint
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Если Chromium уже установлен, задать `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`. В текущем окружении использован headless Chromium 1247; отсутствующая ожидаемая установка 1254 сама по себе не является падением интерфейса. E2E повторены с установленным браузером. Точные итоговые результаты — в docs/tasks/frontend.md.

**Live-интеграция не выполнена.** Перехваченные HTTP-ответы доказывают клиентский workflow, а не реальный engine, качество исходных данных, durable worker, cookie в настоящем reverse proxy или платный вызов модели. До live-приёмки не снимать ограничения готовности.

## Передача владельцам

**BACKEND:** `apps/api/docs/frontend-integration-handoff.md`, опубликованный `/api/v1` без новых обязательных полей. Обязательных расхождений router/OpenAPI/DTO при подготовке не обнаружено. Нужен доступный согласованный runtime с каталогом, профилями, ready capabilities, принятыми snapshots, worker/storage и объяснениями. Приёмка: capabilities устанавливает cookie; реальный POST→job→result, 422/503, cancel и scenario run проходят из браузера одного origin; hashes и baseline совпадают. Приложить воспроизводимую реальную пару территорий/условий, не только synthetic fixtures.

**ML1/ML2 через BACKEND:** реальные map_data/features и journey geometry с source_refs/geometry_kind; population с годом/методом; принятые операнды и ссылки. Приёмка: связанные IDs существуют в сохранённом результате, координаты/геометрии подтверждены, неизвестное сохраняется как unknown. Данные не добавлять на клиент в обход API.

**DEVOPS:** production reverse proxy (при необходимости `infra/**`, клиент его не меняет) должен обслуживать frontend и `/api/v1` на одном публичном origin, сохранять cookie и Origin/Host, не кэшировать персональные session endpoints как общие. `npm ci && npm run build`, раздача dist, VITE_DATA_MODE=api при сборке; `/health` проверяет процесс, не расчётную готовность. Приёмка: HTTPS cookie/session и реальный POST без CORS-обходов, API-ошибка не возвращает HTML SPA.
