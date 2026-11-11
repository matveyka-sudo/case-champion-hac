# Alice AI LLM Flash: серверная интеграция
Принято пользователем 08.11.2026. Модель `gpt://<YANDEX_FOLDER_ID>/aliceai-llm-flash`.
OpenAI SDK из существующего Python workspace; `base_url=https://ai.api.cloud.yandex.net/v1`, `api_key=YANDEX_API_KEY`, `project=YANDEX_FOLDER_ID`; метод `client.chat.completions.create`.
Не использовать native foundationModels completion для Flash. Не переносить параметры reasoning другой модели. Tool choice: auto/none; не полагаться на required/strict без отдельного capability-теста.
API по потреблению; обычная подписка на чат не доказывает оплаченный API-доступ. Реальные ключи не получены и платный smoke-test не выполнен.

## Порядок
1. Backend валидирует запрос и выполняет детерминированный анализ независимо от LLM.
2. После сохранения результата передаёт Flash компактные факты с metric_id/evidence_id.
3. Разрешённые read-only tools: get_analysis(analysis_id), get_metric_details(analysis_id, metric_id), search_evidence(query, territory_ids), get_source_status(source_id, snapshot_id), get_scenario_templates(analysis_id).
4. Инструменты географии/расписаний: resolve_places(query), get_intercity_departures(origin_id, destination_id, departure_at), get_calendar(date, territory_id). Все вызывают серверные адаптеры с разрешёнными ID, кешем, квотами и валидацией; никакого произвольного URL/SQL/shell.
5. Модель может предложить template_id и ссылки на основание. Backend строит scenario_id по allowlist. Запустить сценарий может только отдельный HTTP-запрос кнопки пользователя.
6. LLM-ответ проверяется по DTO. Числа интерфейса берутся из metric_id; ссылки на несуществующие метрики отклоняются. Неподтверждённые численные утверждения заменяются шаблонным объяснением.

## Ограничители первой версии
В конфигурации: timeout 30 с на запрос; максимум 4 model calls / 8 tools на объяснение; max_tokens 2000 на ответ; максимум 2 повторных попытки только 429/5xx с Retry-After и общим бюджетом 90 с. Дедупликация tool call по имени+аргументам в одном цикле.
Бюджет расходов задаётся владельцем API при включении; без ключа/каталога или установленного deployment-лимита режим disabled. Не обнулять пользовательский расчёт из-за LLM.
Журналировать request_id, модель, токены, время, ошибки и tool names, без ключей/полных секретных ответов. Не передавать персональные данные и весь raw-каталог.
На старт: PostgreSQL full-text search по русским документам, фильтры территория/год/источник; embeddings не обязательны. RAG-чанк содержит source_id, URL, дату и раздел. Текст источника считается данными, не инструкцией.
Capability smoke: короткий ответ, один вызов read-only инструмента, обратный ответ с tool_call_id; проверяется именно Flash с проектным ключом. При отсутствии function calling остаётся серверная сборка фактов + одно объяснение Flash; расчёт и retrieval не зависят от способности модели вызывать функции.

## Сохранённые метрики и индексы

Объяснение допускает два разных вида ссылок: `metric_ids`/`claims` для исходных
метрик и `score_ids`/`score_claims` для индексов. Пустой список `metric_ids`
допустим только при непустом `score_ids`; модель не присылает численные значения.
Сервер заполняет claims из сохранённого AnalysisResult и проверяет их повторно
перед выводом. Индекс не преобразуется в фиктивную метрику. Частичный индекс
сопровождается явным ограничением полноты, неизвестный остаётся null.

Контекст выбранной сущности определяет компактный набор фактов и шаблонный
fallback, в том числе для метрик и индексов после первых двадцати записей.
Дополнительная метрика допускается после разрешённого `get_metric_details`,
но её значение всё равно берётся из сохранённого результата. Отсутствие фактов
выбранной сущности не подменяется объяснением другой точки.

## Проверенные официальные ссылки
- [Модели](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models)
- [Аутентификация](https://aistudio.yandex.ru/ru/docs/ai-studio/api-ref/authentication)
- [Chat Completions function calling](https://aistudio.yandex.ru/ru/docs/ai-studio/operations/generation/completions-function)
Проверено 08.11.2026. Общий пример function calling в документации использует другую модель; доступ Flash и фактические квоты подтверждаются smoke-test, а не этим примером.
