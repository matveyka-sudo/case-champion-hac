# JourneyResult
origin_node_id, destination_node_id, departure_at, arrival_at, total_seconds, legs, geometry, status, reason_code, snapshot_id.
Leg: mode, from_node_id, to_node_id, start_at, end_at, duration_seconds, service_id при наличии. Начальное ожидание отдельно от ожидания пересадки; total включает каждый интервал один раз. Не добавлять второй штраф за пересадку.
Карточка точки включает destination_object_id ближайшего подходящего объекта по времени. Межгород не включает путь из дома к первому узлу. Для грузов time_kind=movement|complete_chain; complete_chain допустим только при учёте всех этапов. Трубопровод не получает фиктивный дорожный JourneyResult.

Дополнительно: requested_departure_at, initial_wait_seconds, path_seconds, total_wait_seconds, time_kind, completeness, traffic_mode, reference_id, boundary_nodes, evidence_refs. Межгородской total=initial_wait+path; городской total=sum legs. Временные интервалы не пересекаются, transfer walk не повторяется как wait.
ReferenceResult: reference_id, seconds nullable, status, completeness, profile_version, graph_version, boundary_node_sets, edges, necessary_operations, calibration_evidence.
LastReturn: local service_date, leave_at, first_departure_at, arrival_at; first_departure в выбранный день, arrival может быть позже; walk_only → not_applicable.
