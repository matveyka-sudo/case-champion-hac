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
