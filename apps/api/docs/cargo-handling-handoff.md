# Грузовые сценарии обработки — 2026-11-11

В `work/backend` добавлен `reduce_handling_wait`: сервер выбирает неизменяемую
`CargoScenarioDocument` версии `waydex-product-cargo-scenarios/1`, сверяет точный
нормализованный запрос, snapshot/input hashes, сохранённый baseline и версии.
Предложение фиксирует действия и ресурс; worker меняет только `CargoAction.seconds`
для обязательного совместимого `kind=handling`, вызывает публичный
`analyze_intercity_cargo`, затем сравнивает нормализованные engine metrics. Числа
из `control/*.json` не используются как эффект API.

BACKEND сохраняет и повторно проверяет `handling_changes` вместе с before/after.
Очередь, проверка захвата задания при публикации, идемпотентность и session scope остаются общими.
Марии: новые поля и ограничения приведены в
`packages/contracts/specs/scenario.md`; body отдельного запуска не изменён.

## Предложенный ресурсный интерфейс — ожидает согласования Матвея и Максима

Ресурс не выводится из произвольного `resource_id`/количества в плане.
`CargoScenarioDocument.resources` содержит обязательные hash-pinned факты:
`resource_id`, точный `cargo_profile_id`, `compatible_action_ids`,
`available_units`, `valid_from`, `valid_to`, `evidence_refs`.
План принимается только при совпадении ресурса/профиля/действия, допустимом
количестве и окне, покрывающем реальные before/after интервалы engine.
Доказательства факта должны принадлежать проверенным каноническим evidence
snapshot и resource evidence плана; одного private reference недостаточно.
Отсутствие факта закрывает допуск.

BACKEND предлагает этот контракт; предметное одобрение ML1/ML2 **не получено**.
Реальная поставка данных и полнота матрицы этим изменением не подтверждаются.
Длительность `after_seconds` — заданное условие эксперимента, а не рассчитанная
backend зависимость производительности от количества оборудования.
`feasibility=verified` означает проверку согласованности заданного модельного
плана с его ресурсным фактом и движком; ограничения
`MODELLED_CARGO_PROCESSING_RESOURCE` и
`CARGO_RESOURCE_CONTRACT_PENDING_APPROVAL` сохраняются в предложении/результате.

Операция запрещена для pipeline: repository проверяет тип сохранённого запроса
`IntercityCargoRequest` и `cargo_transport=multimodal` до сохранения предложения
и публикации результата. Подмена одного `analysis_kind` не предоставляет допуск.

## Точная зависимость от ML1: три городских грузовых сравнения

Пути владельца ML1:

- `packages/engine/src/a2_engine/freight/urban_analysis.py`: `CargoNode` и
  `analyze_urban_cargo` содержат только дорожный доступ, тип узла и совместимость.
- `packages/engine/src/a2_engine/freight/truck.py`: `TruckRoute.seconds` складывает
  network + origin_access + destination_access, без обработки.
- `packages/engine/src/a2_engine/freight/delivery/models.py`: `CargoDeliveryScope`
  запрещает одинаковые территории, поэтому использовать его с фиктивной парой
  для городской обработки нельзя.

Нужен отдельный публичный engine интерфейс обработки в пределах одного города:
фиксированные `site_id → node_id`, профиль, направление, дата, road/access
времена отдельно от ожидания и обработки; совместимость груза/узла/ресурса,
ресурсное окно, demand/resource evidence и nullable статусы; immutable overlay
меняет заданную операцию, возвращает прежние road/access показатели и новые
waiting/handling/complete-chain метрики. Индекс доступа не должен молча стать
индексом полного времени обработки.

Приёмка: по реальному POST отдельного сценария для Москвы, Калининграда,
Новороссийска; engine-derived before/after waiting/handling/total, неизменный
baseline и дорожное время; отказ при несовместимом ресурсе/грузе, чужом узле,
неподходящем окне и отсутствии входов. До поставки интерфейса API явно сообщает
`CARGO_HANDLING_MODEL_UNAVAILABLE`, а три сравнения не считаются пройденными.

## Семантика поставки ML2

Если manifest поставки отмечает грузовые сравнения `requires_contract_change`,
его `add_compatible_departure` нельзя считать реализованным: текущая
добавленная backend операция сокращает обработку существующего действия. Если
для пары собирается альтернативное предложение обработки, его template/document
версии и ограничения должны прямо указывать `ALTERNATIVE_HANDLING_PLAN`.

При сокращении обработки engine сам учитывает ожидание следующего рейса.
Нулевая экономия допустима: её нельзя заменять вычитанием заданной длительности
из сохранённого результата. Модельный ресурс обозначен
`MODELLED_CARGO_PROCESSING_RESOURCE` и не подтверждает внедряемость на реальной
инфраструктуре.
