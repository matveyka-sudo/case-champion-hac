# Контракты A2
Рабочие интерфейсы для реализации, пока не реализованный HTTP API. Владельцы: BACKEND, семантика ML1/ML2. Канонический scope: ../../../docs/PRODUCT_SPEC.md.
- analysis-profile.md: запрос и конфигурация
- dataset-snapshot.md: входные данные
- route-result.md: временная цепочка
- accessibility-result.md: исходные метрики и отложенный индекс
- scenario.md: предложение и пересчёт
- assistant-result.md: объяснение
- api.md: jobs и будущие endpoints
Datetime ISO8601 с offset; длительности seconds, отображение minutes; координаты lon/lat EPSG:4326; единицы у каждой метрики. null не ноль. Запрещены NaN/Infinity. Версия схемы независима от версии методики и snapshot.
