# Канонические контракты A2

Документальные контракты ТЗ 1.1, с. 8–11 и приложение A, с. 16–17. BACKEND ведёт схемы и общие валидаторы, ML1 согласует расчётную семантику, ML2 — принятые данные и evidence. DTO независимы от HTTP, хранилища, engine и модели. Файлы переиспользуют прежние пути спецификаций.

| Канонические сущности | Прежнее соответствие по смыслу | Поток |
| --- | --- | --- |
| [Source, Evidence, SnapshotBundle, AcceptedData](dataset-snapshot.md) | `DatasetSnapshot` | ML2 → ML1/BACKEND |
| [ProfileConfig, TaskCase, TaskSet](analysis-profile.md) | `AnalysisProfile` и ранее неявные задачи | DEVOPS/ML1 задают правила, BACKEND описывает схемы → ML1, ML2, FRONTEND |
| [JourneyRequest, JourneyResult](route-result.md) | `RouteResult`, добавлены деятельность, ограничения и возврат | REST/tools BACKEND → ML1 → BACKEND/FRONTEND |
| [ProfileResult](accessibility-result.md) | `AccessibilityResult`, разделён по трём метрикам | ML1 → BACKEND → FRONTEND/tools |
| [ScenarioRequest, Comparison](scenario.md) | `ScenarioRequest/Result` | BACKEND валидирует → ML1 → BACKEND/FRONTEND |
| [REST, Error, Run, Job](api.md) | Предыдущие разрозненные соглашения HTTP и расчётов | BACKEND → FRONTEND/tools |
| [ToolCall и ответ агента](assistant-result.md) | `AssistantResult` | BACKEND → FRONTEND и проверки F07 |

ML2 реализует `load_bundle(bundle_id) → AcceptedData`. ML1 предоставляет `find_activity_journeys(data, request) → JourneyResult`, `evaluate_cases(data, profile, task_set) → ProfileResult` и `compare_scenario(data, baseline_runs, operations) → Comparison`. ML1 получает данные аргументом и возвращает типизированную ошибку при сбое. REST и tools используют один engine и общие валидаторы.

Публичные datetime имеют смещение ISO 8601, события расчёта в UTC, расписания сохраняют IANA timezone и сервисную дату. Длительности legs — секунды, агрегаты `*_minutes` — минуты, оценки и их изменения — пункты шкалы 0–100. Координаты в EPSG:4326 с именованными `lon`/ `lat`. `null` означает отсутствие значения, NaN/Infinity в JSON запрещены. Версии bundle, профиля, задач и кода независимы от `/api/v1`.

Учебные `demo-*` и числа на с. 6, 9–11, 16 иллюстрируют форму. F04 требует не менее трёх территорий. Сокращённые примеры не снимают письменных инвариантов. Следующий шаг BACKEND — схемы, fixtures success/partial/invalid и настоящий экспорт OpenAPI.
