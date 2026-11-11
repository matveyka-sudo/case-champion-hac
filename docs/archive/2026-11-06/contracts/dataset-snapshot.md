# Source, Evidence и SnapshotBundle

ML2 получает и проверяет входы, публикует неизменяемый bundle и реализует `load_bundle(bundle_id) → AcceptedData`. ML1 считает на AcceptedData, BACKEND проверяет ссылки, обслуживает coverage/evidence и сохраняет версии в runs.

| Сущность | Обязательные поля и семантика |
| --- | --- |
| Source | `source_id`, `publisher`, `url`, `license_status`. Статус лицензии явно указывает известность права использования. Публичный адрес сам по себе права не подтверждает. |
| Evidence | `evidence_id`, `source_id`, `record_id`, `fetched_at` в UTC, `valid_period`, `content_hash`, статус проверки. Период действия отличен от момента загрузки. Неизвестная граница периода остаётся `null`, а запись не доказывает покрытие за этой границей. |
| SnapshotBundle | `bundle_id`, `timetable_snapshot`, `destinations_snapshot`, `fares_snapshot` (nullable), `transfer_snapshot`, `hashes` нормализованных таблиц, `parser_version`, `acceptance_state`, `coverage`, `completeness_scope`, `completeness_evidence`, `missing_fields`, `accepted_by` и `accepted_at` (обязательны при `accepted`). Набор целей включает origins, destinations и услуги. |

Нормализованные входы с. 8 и 16

| Таблица | Обязательные данные |
| --- | --- |
| `origins` / `destinations` | Собственный ID, `city_id`, `anchor_id`, `lon` (−180…180), `lat` (−90…90), `timezone`, `type`, `evidence_ids`. Цель также содержит `entrance_id` и `verification_state`. Центр города не заменяет вход. |
| `service_offerings` | `destination_id`, `service_type_id`, `eligibility`, `valid_from`, `valid_to`, `service_verified`, `hours_verified`, `evidence_ids`. Состояния услуги и часов раздельно `verified\|absent\|unknown`. Окна и ограничения применимости хранятся с доказательством. |
| stops / trips | Строковые namespaced `stop_id`, `trip_id`, связь с parent/station и `service_id`, оператор/режим и evidence. Сохранять feed namespace и связь версии с внешним ID. Город, станция и платформа имеют разные ID. |
| `stop_times` | `trip_id`, `stop_id`, `sequence`, `arrival_seconds`, `departure_seconds`, `service_id`, pickup/dropoff. Допустимы значения >86400. |
| calendars | `service_id`, интервал локальных сервисных дат, базовое правило и исключения add/remove с evidence, IANA timezone источника. Исключения имеют приоритет. |
| transfers | `from_id`, `to_id`, `duration_seconds`, `access_state`, `basis`, ограничения и `evidence_ids`. Различать подход, межплатформенную пересадку и выход. |
| fares при наличии | Размер, валюта, категория пассажира, период, область применения и evidence. Неизвестная цена остаётся неизвестной. |
| coverage | Территория, режим и даты, конкретная `completeness_scope`, доказательства полноты, известные пропуски и ограничения. Полнота утверждается по области запроса, не по наличию отдельных строк. |

Идентичность версии привязана к содержимому, правилам преобразования и принятым доказательствам. Проверяем ключи и ссылки, географию, порядок событий, календарь и объяснения отличий. Жизненный цикл `downloaded → parsed → validated → accepted`, ошибочный вход → `quarantined`. Публикация `accepted` атомарна, старые runs сохраняют свой bundle. Принятая ограниченная область допустима при явных пробелах. Расчётные входы из непринятой версии отклоняются. Неизвестный ID → `404 unknown_id`, неверный вход → `422 invalid_request`, несовместимые версии → `409 incompatible_versions`.

Конкретные UTC-события строятся из сервисной даты, целых секунд и зоны источника по семантике GTFS. Проверяются 25:10, переход DST, даты исключений и ночные события предыдущей даты. `weekday` сам по себе не доказывает наличие рейса. В пилоте профиль использует `Europe/Kaliningrad`.
