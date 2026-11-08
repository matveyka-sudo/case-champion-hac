# AnalysisResult / Metric / Score — schema_version 2
analysis_id, analysis_kind, request, snapshot_id, methodology_version, config_hash, traffic_mode, status, metrics[], scores[], evidence_refs[], limitations[].
Metric: metric_id, name, value nullable, unit, status, reason_code, source_refs, population_basis/site_basis where applicable.
Score: score_id, value nullable (0..100), status=available|unknown|not_applicable|methodology_pending, completeness=full|partial, methodology_version, reason_code, components[], evidence_refs[]. Для текущих четырёх методов version=a2-methodology-v3; methodology_pending не глобальное состояние.
available допускается только для рассчитанного результата. unknown/not_applicable/methodology_pending требуют value=null. Partial score — результат только явно заявленного поднабора, отдельная карточка, не общий рейтинг.
Подтверждённая недостижимость в полном графе/окне даёт value=0, available, reason_code=NO_FEASIBLE_ROUTE. Ошибка API/пустой неполный каталог даёт unknown, null, DATA_UNAVAILABLE. Нет выбранных категорий → validation error до запуска.
Территориальный полный индекс отсутствует при unknown у любой точки с положительным весом или unknown населении. Нулевой знаменатель → not_applicable, ZERO_WEIGHT. Публиковать известные категории отдельно; не перенормировать пропуски как полноценный результат.
Популяционная оценка хранит population_method, year, coverage и единицу persons. Отсутствие данных отличается от 0 жителей.
JourneyResult, ReferenceResult и CountResult сохраняют собственные статусы: неизвестный τ не блокирует индекс H; неизвестный эталон блокирует только зависящий от него score.
На экране 1 знак, сортировка по исходному float; NaN/Infinity запрещены. Demo вынесен из production dataset, не состояние доступности реального score.
