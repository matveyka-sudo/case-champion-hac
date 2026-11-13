# Подготовка входов ОТ для ЦАО

Скрипты извлекают рельсовые направления, станции и входы из снимка OSM,
проверяют геометрию перегонов и сопоставляют времена pMetro по линии и паре
станций. Файлы результатов создаются в текущем рабочем каталоге.

## Запуск

Нужны Python 3.12, `osmium==4.2.0`, московский PBF и полный снимок ЦФО
Geofabrik от 7 ноября 2026 года. Скачайте [московскую схему pMetro](https://pmetro.github.io/download/Moscow.zip)
от 13 октября 2026 года в рабочий каталог под именем `pmetro-moscow-202610.zip`.
SHA-256 проверенной выгрузки
`26f2bcf46dedde17ffecb4843879493bef57f92d4b46965eb148baedb7fcf805`.
Использование схемы регулирует [лицензия pMetro](https://pmetro.su/License.html).

```bash
scripts=/absolute/path/to/case-champion-hac/infra/cao-transit
moscow=/absolute/path/to/moscow-bbox-261107.osm.pbf
region=/absolute/path/to/central-fed-district-261107.osm.pbf

python -m venv .venv
.venv/bin/python -m pip install 'osmium==4.2.0'
.venv/bin/python "$scripts/extract-rail.py" "$moscow"
.venv/bin/python "$scripts/audit-rail.py" "$moscow"
.venv/bin/python "$scripts/extract-entrances.py" "$moscow"
.venv/bin/python "$scripts/recover-d3.py" "$region"
.venv/bin/python "$scripts/audit-segments.py"
.venv/bin/python "$scripts/match-pmetro.py"
```

`osm-rail-audit.json` и `pmetro-match-audit.json` содержат сводки.
В `pmetro-segment-match.json` статус `reverse` означает, что найдено время
обратного направления. Для `unmatched` и `ambiguous` время остаётся
неизвестным. `osm-rail-segments.json` описывает геометрию московского выреза.
`osm-d3-extra.json` содержит отдельно восстановленные узлы и пути D3.

Пассажирский расчёт ещё требует интервалы движения, пешие подходы и переходы,
а также подключение расписаний автобусов и трамваев. Архивы источников
и полученные выгрузки хранятся отдельно от кода.

## Расчёт и показ на отдельном стенде

`runtime/network.py`, `links.py`, `route.py` и `geometry.py` — скрипты
разового расчёта на a2. Они используют соседние каталоги
`cao-transit-inputs`, `cao-walk-500m` и контейнер Valhalla. Выполняются
в этом порядке из каталога расчёта. Если промежуточные данные уже собраны,
для изменения правила выбора достаточно перезапустить API со старой
выгрузкой `transit-500m.json`.

Для отдельного API соберите `infra/full-stack/api.Dockerfile` и подключите
к контейнеру `runtime/preview.py`, `runtime/transit_preview.py`,
пешие `data-500m.json` и `metadata.json`, а также `transit-500m.json`
под одноимёнными путями в `/preview`. Задайте `PYTHONPATH=/preview` и
используйте запуск
`/app/.venv/bin/python -m uvicorn preview:build_app --factory --host 0.0.0.0 --port 8000`.
База и переменные окружения берутся из конфигурации стенда. Соберите веб
через `infra/full-stack/web.Dockerfile` и подключите
`runtime/nginx.conf` как `/etc/nginx/conf.d/default.conf`. Имя API в
Docker-сети — `waydex-cao-transit-api`. Сохранённые расчёты открываются по
`/#atlas?analysis=cao-walk-500m` и `/#atlas?analysis=cao-transit-500m`.

Пеший вариант участвует в выборе для каждой ячейки и категории. Для ОТ
прибавляется штраф 4 минуты за пересадку. Карта, фактическое время и
индекс берутся из выбранного маршрута. Расчёт ОТ доступен на 13 ноября
2026 года, 08:45 по Москве. Выгрузки маршрутов и настройки ВМ хранятся
отдельно.
