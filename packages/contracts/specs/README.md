# Контракты A2
Канонические DTO реализованы в `a2_contracts.v2`; текущий HTTP и схемы публикуются в `contracts/generated`. Владельцы: BACKEND, семантика ML1/ML2. Канонический scope: ../../../docs/PRODUCT_SPEC.md.
- analysis-profile.md: запрос и конфигурация
- dataset-snapshot.md: входные данные
- route-result.md: временная цепочка
- accessibility-result.md: метрики, утверждённый индекс и состояния данных
- scenario.md: предложение и пересчёт
- assistant-result.md: объяснение
- api.md: jobs и endpoints
Datetime ISO8601 с offset; длительности seconds, отображение minutes; координаты lon/lat EPSG:4326; единицы у каждой метрики. null не ноль. Запрещены NaN/Infinity. Версия схемы независима от версии методики и snapshot.

Версия контрактов 2: approved methodology и полнота данных разделены. OpenAPI строится из production router; наличие схемы не подтверждает готовность реальных данных и engine. Порядок интеграции — docs/IMPLEMENTATION.md.
