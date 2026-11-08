# Контракты A2
Рабочие интерфейсы для реализации, пока не реализованный HTTP API. Владельцы: BACKEND, семантика ML1/ML2. Канонический scope: ../../../docs/PRODUCT_SPEC.md.
- analysis-profile.md: запрос и конфигурация
- dataset-snapshot.md: входные данные
- route-result.md: временная цепочка
- accessibility-result.md: метрики, утверждённый индекс и состояния данных
- scenario.md: предложение и пересчёт
- assistant-result.md: объяснение
- api.md: jobs и будущие endpoints
Datetime ISO8601 с offset; длительности seconds, отображение minutes; координаты lon/lat EPSG:4326; единицы у каждой метрики. null не ноль. Запрещены NaN/Infinity. Версия схемы независима от версии методики и snapshot.

Версия контрактов 2: approved methodology и полнота данных разделены. Это спецификация для реализации DTO/OpenAPI BACKEND, не автоматически опубликованная схема. Порядок интеграции — docs/IMPLEMENTATION.md.
