# ScenarioRequest и Comparison

BACKEND валидирует запрос REST или tool, ML1 реализует `compare_scenario(data, baseline_runs, operations)`. BACKEND хранит результаты, UI и агент показывают эффекты и ограничения по профилям.

**ScenarioRequest.** Обязательны `base_run_ids` по профилям, `evaluate_profile_ids` и упорядоченные `operations`. Полный P0 запрашивает все три профиля. Все базы используют один принятый bundle. Для сравнения внутри каждого профиля неизменны bundle, profile version, task_set_hash, веса, параметры, горизонт, период, код и правила. Операции применяются поверх неизменяемой базы. Версионная несовместимость → 409. Неверные ID, календарь, диапазон сдвига, цепочка или лимит операций → 404 или 422 по [общим правилам](api.md). R допускает до 10 операций, сдвиг ±30 минут.

| Тип | Обязательные поля |
| --- | --- |
| `shift_trip` | `trip_id`, сервисные даты и знаковый сдвиг секунд. Сдвигается вся цепочка одинаково, длительности сохраняются. |
| `add_trip` | Новый ID, маршрут и полная упорядоченная цепочка с календарём, либо принятый `template_trip_id`, `service_dates` и `stop_time_shift_seconds` для копии всех времён с новым ID. Шаблон, даты, остановки, посадка/высадка и последовательность проверяются. |

Даты и секунды сохраняют семантику сервисного дня и зоны источника. У гипотетических операций есть допущения и маркировка сценария.

**Comparison.** Обязательны `run_id`, `scenario_id`, `bundle_id`, `base_run_ids`, `status`, операции, `comparisons`, `resource_delta`, `operational_validation`, `provenance`, `evidence_ids`, `assumption_ids`, версии и хэши параметров, кода и результата. Для каждого профиля и origin при расчёте — `profile_id`, `profile_version`, `task_set_hash`, `base_run_id`, `scenario_run_id`, `origin_id`, интервалы `before`/`after`, `lower_bound_change`, `upper_bound_change`, `delta_interval_conservative`, `confirmed_improved_cases`, `confirmed_worse_cases`, `unresolved_cases` и худшие ухудшения. Если база `insufficient_data`, результат профиля сохраняет профильную версию, hash задач и `base_run_id`, возвращает причину `baseline_insufficient_data`, `scenario_run_id=null` и отсутствующие оценки. Укороченный учебный пример ТЗ не снимает эти поля.

Изменения границ L1−L0 и U1−U0 отделены от консервативного эффекта [L1−U0, U1−L0], всё до округления. Для regular/medical только fail→pass и pass→fail считаются подтверждёнными переходами; переходы с unknown учитываются отдельно. Для intercity сравниваются времена и границы полезности. `add_trip` не уменьшает нижнюю оценку при неизменных правилах. `shift_trip` может ухудшать поездки. Число отправлений и известные машино-минуты допустимы как ресурсные прокси. Без спроса/ставок нет пассажиро-часов/рублей. `operational_validation=not_verified` до проверки эксплуатации.
