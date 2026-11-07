# AcceptedData
snapshot_id, schema_version, source_versions, geographic_scope, valid_period, timezone, provenance, QA report.
Коллекции: territories, population_cells, sample_points, destinations, access_nodes, scheduled_services, transfers, freight_sites, freight_nodes, freight_compatibility, pipeline_connections.
Геометрия и сеть версионируются. FreightSite — физическая площадка; PipelineConnection содержит продукт, вход/выход, направление и подтверждение, capacity/volume с единицами и периодом при наличии.
ML2 публикует только прошедший QA snapshot; unknown отдельно от confirmed_absence. Источники/лицензии/хэши остаются доступны технически, без отдельного пользовательского индекса покрытия.
