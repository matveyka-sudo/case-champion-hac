# BACKEND — канонические DTO и контракт API

**Входы и зависимости.** ТЗ 1.1, с. 8–11, 13, 16–17 и [спецификации](../../packages/contracts/specs/README.md). ML2 согласует SnapshotBundle/evidence, ML1 — Journey/ProfileResult/Comparison, DEVOPS — правила D1–D3. Уточните условно обязательные поля, reason codes и единицы до публикации.

**Пути и выход.** `apps/api/`, `packages/contracts/`, `contracts/generated/`, `packages/assistant/` и этот файл. Сначала создайте канонические DTO, контракт `/api/v1`, три маркированных fixtures success/partial/invalid и настоящий воспроизводимый экспорт OpenAPI/JSON Schema. Следующие задачи — адаптеры API, jobs и один worker, общие REST/tools валидаторы, агент и журнал. Сбор источников и расчёт остаются у ML2 и ML1.

**Приёмка и передача.** Валидатор принимает два допустимых примера, отклоняет invalid с согласованным envelope, версии и nullable поля одинаково трактуются DTO, схемой и потребителями. Два экспорта из одинакового состояния совпадают. Запустите контрактные проверки после появления моделей. Передайте FRONTEND схемы, fixtures, поля polling, а DEVOPS — entrypoints и настройку worker. Агент и его evals у BACKEND, отдельно подтверждаются API-доступ и денежный лимит D4.
