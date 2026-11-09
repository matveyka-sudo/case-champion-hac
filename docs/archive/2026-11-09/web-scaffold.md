# История подготовительного web scaffold

Это README прежнего apps/web до структурного переноса в fix-fullstack.
Его35 tracked files проверены в исходном коммите bbef1b6637931293a476c1e35c720e575dd30f2f:
React-заглушки, временный profile API и отдельные зависимости не использовались
активным Waydex. Manifest/lock и код доступны по этому коммиту в Git history.
.npmrc с engine-strict сохранён в текущем приложении. .prettierrc относился к scaffold.
Ниже историческое описание, не инструкция текущего приложения.

---

# Waydex frontend

Техническая основа React 19 + TypeScript + Vite. Bootstrap CSS без JS; финальный дизайн подключается отдельно. Node 24.x, npm 11.x согласно командному manifest.

## Запуск

`npm ci`, затем `npm run dev`. Скопируйте `.env.example` в `.env.local` при необходимости. По умолчанию `/api` проксируется на `http://127.0.0.1:8000` в dev. В production нужен same-origin reverse proxy либо `VITE_API_BASE_URL` и CORS на backend. Переменные VITE публичны: секреты сюда не класть.

## Проверки

`npm run typecheck`, `npm run lint`, `npm test`, `npm run build`.

## Архитектура

- `app`: провайдеры, error boundary, hash-маршруты `/` и `/atlas` (не требует server fallback).
- `pages`: сборка экранов; без HTTP и расчётных формул.
- `widgets`: крупные части экрана; карта позже монтируется здесь с cleanup MapLibre.
- `features`: пользовательские действия, например выбор города.
- `entities`: предметные view models и TanStack Query запросы.
- `shared`: конфигурация, HTTP, простые UI-компоненты, стилевые токены.

Зависимости направлены сверху вниз: app → pages → widgets/features/entities → shared. Server state в TanStack Query; локальные значения формы в React. Не вводим глобальное хранилище до реальной необходимости. Данные маршрутов, индексов и сценариев считаются сервером. Не добавляем пустые слои для будущих функций.

## Контракт API

Опубликованного OpenAPI пока нет (`contracts/generated/README.md` содержит только инструкции). `entities/profile/model.ts` — временный валидируемый view model на основе `packages/contracts/specs/analysis-profile.md`, НЕ сгенерированный контракт. ID городов выводятся как ID до публикации каталога; фиктивных координат и баллов нет.

После публикации схемы BACKEND: `npm run api:generate` (ожидается `../../contracts/generated/openapi.json`), затем заменить временный HTTP-адаптер на `openapi-fetch` с generated `paths`. Не редактировать generated types вручную. Версию/хэш схемы зафиксировать при интеграции. Текущий endpoint — GET `/api/v1/profiles/{id}/versions/{version}`. Результат проверяется перед отображением, запрос отменяется при размонтировании; ошибка явно отличена от отсутствия маршрута.

## UI и развитие

Bootstrap выбран по решению пользователя вместо Tailwind из общей архитектуры. Не активировать обе CSS-системы одновременно. Старые зависимости сохранены до согласования командой. Дизайн меняет tokens/base и UI-компоненты, сохраняя queries и API границы. Следующие модули добавляются по реальному контракту: accessibility, routes, scenarios, assistant, provenance. Для результатов показывать snapshot/profile/algorithm версии и покрытие; `no_path`, недостаток данных и ошибка — разные состояния. Markdown помощника не рендерить как произвольный HTML.

Пока реализованы landing-заглушка, профиль из API, выбор ID города и область будущей карты; нет реальной карты, индексов, чата или мокового режима. Нужны backend OpenAPI и каталог городов с подтверждёнными координатами. Никакие новые схемы от имени BACKEND не утверждены.
