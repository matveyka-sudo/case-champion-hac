# AnalysisResult
analysis_id, analysis_kind, request, data_versions, schema_version, status, metrics, scores, limitations.
Metric: key, value nullable, unit, status available|unknown|not_applicable, reason_code, provenance_ref.
Score: value nullable, status methodology_pending|available|unknown|not_applicable, methodology_version nullable, reason_code.
Сейчас все scores: value=null, status=methodology_pending, methodology_version=null. Raw metrics могут быть available. Нулевой score даже для отсутствия маршрута не назначать до утверждения обработки отсутствия в методике.
Пример: {"value":null,"status":"methodology_pending","methodology_version":null,"reason_code":"FORMULA_NOT_APPROVED"}.
Метрики города по девяти категориям и режимам; грузовые агрегаты по объектам; будущие пассажирские агрегаты по населению. Неполные результаты не включаются в полный рейтинг.
