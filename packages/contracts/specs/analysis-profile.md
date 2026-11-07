# AnalysisRequest
analysis_kind: urban_passenger | intercity_passenger | urban_cargo | intercity_cargo.
Городские запросы: city_id, optional area_id. Межгород: territory_level city|region, origin_id, destination_id; два разных объекта одного уровня.
departure_at с offset; passenger_mode walk|public_transport|private_car только для urban_passenger; categories из config/product.json и priorities low|medium|high только для пассажирского города. cargo_profile_id для грузов. Не принимать пользовательские коэффициенты, H, цены или сценарные операции.
MethodologyRef: status=pending, version=null пока методика не утверждена. В будущем явная version и parameters. Не подменять недостающие настройки константами.
