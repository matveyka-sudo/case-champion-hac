# ScenarioProposal и ScenarioResult
Proposal: scenario_id, baseline_analysis_id, operation_type, fixed_parameters, evidence_refs, assumptions, status=proposed. Операции формирует сервер из allowlist. UI передаёт scenario_id, а не произвольную правку расписания.
Проверка только по «Проверить сценарий». Вход run: scenario_id и immutable baseline. Результат: before/after raw metrics, worsened effects, affected_population либо affected_sites, versions. Индексные delta рассчитываются engine по FORMULAS.md только при сопоставимых численных before/after; иначе null с причиной.
Согласование пересадки — базовый сценарий. Дополнительное отправление требует подтверждения потребности и исполнимого расчёта. Нельзя просто вычитать минуты из внешнего маршрута. Эффект до запуска не заявляется.

Сохранять feasibility=verified|unverified, изменённые service_ids, shift_seconds, ресурсные ограничения. При отсутствии оборота — только модельный эксперимент; не подтверждённое предложение внедрения. baseline версии заморожены; scenario получает новый graph overlay. Повторный запуск идемпотентен.
