# Реальный canonical subset ML2

kaliningrad_walk_canonical_subset.json получен существующим
load_accepted_data("kaliningrad-urban-2026-11-08.2") из work/ml2@02221bf с
канонической моделью work/backend@d2cfe9a. Полный payload hash и состав записаны
в ../../examples/kaliningrad_walk_readiness.json. Это реальные исходные геоданные,
не ответы Valhalla и не доказательства проходимости подходов.

Выборка сохраняет без изменения полей первые две sample_points и их ячейки,
первую принятую destination (airport), первую excluded ячейку с положительным
населением, используемые access_nodes, исходные coverage scopes и все используемые
evidence. Остальные записи удалены только для компактной тестовой фикстуры.
Подмножество отдельно успешно проверено AcceptedData.model_validate. Оно не
является новым принятым полным городским набором; городской рейтинг по нему unknown.

SHA256 файла: 35560203df431b1593af74f475b7b438ce7486dcdacdf5d4700838be6b0b0354.
Исходные evidence содержат ссылки, hashes и лицензии: © OpenStreetMap contributors
(ODbL-1.0), European Commission GHSL (CC-BY-4.0), OurAirports (Public Domain).
Маршрутные ответы, build и attestations в test_walk_integration.py синтетические.
