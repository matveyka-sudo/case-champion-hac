| Территория / направление | Вкладка | Режим | Статус | Реальное | Модельное | Примечание |
| --- | --- | --- | --- | --- | --- | --- |
| moscow | in_city | walk | model | 6 origins (GHSL cells), 18 destinations | 0 destinations, 108 edge times | speed 5 km/h from the accepted walking profile |
| moscow | in_city | private_car | model | same origins/destinations | 108 edge times, 24.0 km/h | no traffic data; static |
| moscow | in_city | public_transport | model | stop positions: mos_ground_transport GTFS stops (transit_stops.json) | 2 routes, 40 trips, 174 footpaths, 10 transfer rules | model OD reached 108/108; direct walk included |
| moscow | cargo_in_city | road | model | 3 sites | logistics_terminal node, rail_terminal node, handling, resource | truck-model-20t-container-nonfood-ordinary |
| moscow | cargo_between | pipeline | not_applicable |  |  | NO_PIPELINE_PROFILE |
| kaliningrad | in_city | walk | model | 6 origins (GHSL cells), 17 destinations | 1 destinations, 108 edge times | speed 5 km/h from the accepted walking profile |
| kaliningrad | in_city | private_car | model | same origins/destinations | 108 edge times, 30.0 km/h | no traffic data; static |
| kaliningrad | in_city | public_transport | model | stop positions: gortrans39 stop lists (stop_frequencies.json) | 2 routes, 40 trips, 178 footpaths, 10 transfer rules | model OD reached 108/108; direct walk included |
| kaliningrad | cargo_in_city | road | real+model | 3 sites, port location, truck observations | port operations, rail_terminal node, handling, resource | truck-model-20t-container-nonfood-ordinary |
| kaliningrad | cargo_between | pipeline | not_applicable |  |  | NO_PIPELINE_PROFILE |
| novorossiysk | in_city | walk | model | 6 origins (GHSL cells), 15 destinations | 3 destinations, 108 edge times | speed 5 km/h from the accepted walking profile |
| novorossiysk | in_city | private_car | model | same origins/destinations | 108 edge times, 30.0 km/h | no traffic data; static |
| novorossiysk | in_city | public_transport | model | stop positions: real walk-network projections (no accepted stop table) | 2 routes, 41 trips, 174 footpaths, 10 transfer rules | model OD reached 108/108; direct walk included |
| novorossiysk | cargo_in_city | road | real+model | 3 sites, port location, truck observations | port operations, rail_terminal node, handling, resource | truck-model-20t-container-nonfood-ordinary |
| novorossiysk | cargo_between | pipeline | model | pipeline terminal locations (OSM) | connections, direction, capacity/volume per day | separate profile, not in the delivery index |
| moscow→kaliningrad | passenger_links | air | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→kaliningrad | passenger_links | rail | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→kaliningrad | passenger_links | road_car | real_observation | Valhalla freeflow observation |  |  |
| moscow→kaliningrad | passenger_links | road_bus | not_applicable |  |  | INTERNATIONAL_TRANSIT_BUS_NOT_MODELLED |
| moscow→kaliningrad | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| moscow→kaliningrad | cargo_between | road | not_applicable |  |  | LONG_HAUL_ROAD_NOT_IN_DEMO_CHAIN |
| moscow→kaliningrad | cargo_between | rail | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| moscow→kaliningrad | cargo_between | water | not_applicable |  |  | NO_WATER_LINK_MODELLED |
| moscow→kaliningrad | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| moscow→kaliningrad | cargo_between | pipeline | not_applicable |  |  | NO_PIPELINE_LINK |
| kaliningrad→moscow | passenger_links | air | model_schedule | nodes (intercity directory) | schedule/chain |  |
| kaliningrad→moscow | passenger_links | rail | model_schedule | nodes (intercity directory) | schedule/chain |  |
| kaliningrad→moscow | passenger_links | road_car | real_observation | Valhalla freeflow observation |  |  |
| kaliningrad→moscow | passenger_links | road_bus | not_applicable |  |  | INTERNATIONAL_TRANSIT_BUS_NOT_MODELLED |
| kaliningrad→moscow | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| kaliningrad→moscow | cargo_between | road | not_applicable |  |  | LONG_HAUL_ROAD_NOT_IN_DEMO_CHAIN |
| kaliningrad→moscow | cargo_between | rail | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| kaliningrad→moscow | cargo_between | water | not_applicable |  |  | NO_WATER_LINK_MODELLED |
| kaliningrad→moscow | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| kaliningrad→moscow | cargo_between | pipeline | not_applicable |  |  | NO_PIPELINE_LINK |
| moscow→novorossiysk | passenger_links | air | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→novorossiysk | passenger_links | rail | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→novorossiysk | passenger_links | road_car | real_observation | Valhalla freeflow observation |  |  |
| moscow→novorossiysk | passenger_links | road_bus | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→novorossiysk | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| moscow→novorossiysk | cargo_between | road | not_applicable |  |  | LONG_HAUL_ROAD_NOT_IN_DEMO_CHAIN |
| moscow→novorossiysk | cargo_between | rail | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| moscow→novorossiysk | cargo_between | water | not_applicable |  |  | NO_WATER_LINK_MODELLED |
| moscow→novorossiysk | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| moscow→novorossiysk | cargo_between | pipeline | separate_profile |  |  | demo-novorossiysk-pipeline |
| novorossiysk→moscow | passenger_links | air | model_schedule | nodes (intercity directory) | schedule/chain |  |
| novorossiysk→moscow | passenger_links | rail | model_schedule | nodes (intercity directory) | schedule/chain |  |
| novorossiysk→moscow | passenger_links | road_car | real_observation | Valhalla freeflow observation |  |  |
| novorossiysk→moscow | passenger_links | road_bus | model_schedule | nodes (intercity directory) | schedule/chain |  |
| novorossiysk→moscow | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| novorossiysk→moscow | cargo_between | road | not_applicable |  |  | LONG_HAUL_ROAD_NOT_IN_DEMO_CHAIN |
| novorossiysk→moscow | cargo_between | rail | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| novorossiysk→moscow | cargo_between | water | not_applicable |  |  | NO_WATER_LINK_MODELLED |
| novorossiysk→moscow | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| novorossiysk→moscow | cargo_between | pipeline | separate_profile |  |  | demo-novorossiysk-pipeline |
| kaliningrad→novorossiysk | passenger_links | air | model_schedule_via_moscow | nodes (intercity directory) | schedule/chain |  |
| kaliningrad→novorossiysk | passenger_links | rail | not_applicable |  |  | NO_DIRECT_RAIL_LINK_AND_CROSS_TERMINAL_TRANSFER_IN_MOSCOW_NOT_MODELLED |
| kaliningrad→novorossiysk | passenger_links | road_car | ROAD_OBSERVATION_UNAVAILABLE |  |  | 154:Path distance exceeds the max distance limit: 1500000 meters |
| kaliningrad→novorossiysk | passenger_links | road_bus | not_applicable |  |  | INTERNATIONAL_TRANSIT_BUS_NOT_MODELLED |
| kaliningrad→novorossiysk | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| kaliningrad→novorossiysk | cargo_between | road | unknown |  |  | ROAD_OBSERVATION_UNAVAILABLE |
| kaliningrad→novorossiysk | cargo_between | rail | not_applicable |  |  | NO_DIRECT_RAIL_LINK |
| kaliningrad→novorossiysk | cargo_between | water | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| kaliningrad→novorossiysk | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| kaliningrad→novorossiysk | cargo_between | pipeline | separate_profile |  |  | demo-novorossiysk-pipeline |
| novorossiysk→kaliningrad | passenger_links | air | model_schedule_via_moscow | nodes (intercity directory) | schedule/chain |  |
| novorossiysk→kaliningrad | passenger_links | rail | not_applicable |  |  | NO_DIRECT_RAIL_LINK_AND_CROSS_TERMINAL_TRANSFER_IN_MOSCOW_NOT_MODELLED |
| novorossiysk→kaliningrad | passenger_links | road_car | ROAD_OBSERVATION_UNAVAILABLE |  |  | 154:Path distance exceeds the max distance limit: 1500000 meters |
| novorossiysk→kaliningrad | passenger_links | road_bus | not_applicable |  |  | INTERNATIONAL_TRANSIT_BUS_NOT_MODELLED |
| novorossiysk→kaliningrad | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| novorossiysk→kaliningrad | cargo_between | road | unknown |  |  | ROAD_OBSERVATION_UNAVAILABLE |
| novorossiysk→kaliningrad | cargo_between | rail | not_applicable |  |  | NO_DIRECT_RAIL_LINK |
| novorossiysk→kaliningrad | cargo_between | water | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| novorossiysk→kaliningrad | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| novorossiysk→kaliningrad | cargo_between | pipeline | separate_profile |  |  | demo-novorossiysk-pipeline |
