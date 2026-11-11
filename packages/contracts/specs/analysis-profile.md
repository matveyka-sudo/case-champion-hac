# AnalysisRequest
analysis_kind: urban_passenger | intercity_passenger | urban_cargo | intercity_cargo.
Городские запросы: city_id, optional area_id. Межгород: territory_level city|region, origin_id, destination_id; два разных объекта одного уровня.
departure_at с offset; passenger_mode walk|public_transport|private_car только для urban_passenger; categories из config/product.json и priorities low|medium|high только для пассажирского города. cargo_profile_id для грузов. Не принимать пользовательские коэффициенты, H, цены или сценарные операции.
MethodologyRef: version=a2-methodology-v3, config_hash, reference_profile=edge-calibrated-v1. Сервер разрешает только опубликованные версии config/product.json.
traffic_mode=static|typical|live|historical, snapshot_id (server-resolved immutable), schema_version=2. Unsupported requested traffic mode → явная причина/доступные режимы, без незаметного fallback.
CargoProfile: id, food_class=food|nonfood, urgency=urgent|ordinary|nonurgent, temperature_requirements, handling_requirements, vehicle_constraints. Pipeline product только отдельной веткой, не food_class.
Для intercity: два разных ID одного уровня; optional fixed node sets, иначе явные server-resolved sets. Для urban: city_id из трёх, категории nonempty unique, priorities для каждой выбранной категории. Date/time aware, UI шаг 15 мин.
