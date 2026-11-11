# Original atlas actions and canonical API coverage

Authority: `/Users/kirito/Desktop/waydex-prototype/index.html`, its README and
DESIGN_NOTES, and the existing `AtlasPage`/widgets. The incumbent presentation
and planned controls stay present. Missing data must produce explicit original
empty/error/unknown states, never a reduced set of product actions or invented
values. This matrix records source facts, not live data readiness.

| Original control/workflow | Input/action | Existing canonical interface | Remaining requirement |
| --- | --- | --- | --- |
| Header: «В городе», «Пассажиры», «Грузы» | Three tabs; cargo has urban/inter scale inside filters | Four `analysis_kind` values | One shared three-tab presentation; map cargo scale to its two kinds. |
| «Город» | Москва, Калининград, Новороссийск | Territory catalog IDs, `city_id` | Publish canonical catalog identities for all three. Keep all planned city actions; no name-to-fake-ID substitution. |
| «Передвижение» | Общ. транспорт / Пешком / Авто | `passenger_mode`: public_transport / walk / private_car | Accept corresponding real source profiles. An active walk-only profile must not remove the other controls. |
| Nine categories | Adult/child clinics, kindergarten, school, college/university, rail/bus terminals, airport, shopping center | `categories`, `priorities` | Real source coverage/tasks for every selected category, or explicit unknown; no averaging only known categories. |
| «Н / С / В» | Category priorities 1/2/3 | low / medium / high | Preserve the original buttons. Priority cannot add an unselected category through a hidden control. |
| Date and 15-minute clock | Local date, 00:00–23:45; arrows and PageUp/PageDown | `departure_at`, territory timezone | Validate supported time transparently. Preserve the chosen time and display truthful unavailable state; no silent time replacement. |
| «Рассчитать / Проанализировать» | Explicit calculation, loading, stale result, retry | POST analyses, jobs, saved AnalysisResult | No automatic analysis. Preserve baseline until explicit city-context reset; unknown differs from zero/no-route/source error. |
| «Цвет карты» | Overall or selected category | Score subject category and point/territory | Backend returns the actual totals/category tasks, including unknowns. Frontend only selects a saved value for display. |
| Layers «Индекс / Объекты / Население» | Toggle saved layers | MapFeature geometry, kind, categories, population/year/method | Supply pinned real cell geometry and population facts. Missing cell geometry is not permission to synthesize a prototype grid. |
| Map zoom, home, city focus | Overview, cells/points, object selection | Saved map inventory and journey geometry | Preserve all accepted points. Actual cell/aggregate shapes must come from accepted sources; no browser-generated administrative or population aggregation. |
| Selected result card | Time, score, actual route segments, waits/transfers, category table, city comparison, sources | Metric, Score, JourneyResult, PopulationBasis, evidence | Missing optional facts stay unknown. Found-route estimate stays distinct from a proved minimum/category/point index. |
| «Проанализировать / Спросить агента» | Explicit saved selection explanation | POST analysis explanation with selection and result/hash binding | Live Alice uses saved canonical values and citations. No fixture narrative in API mode. |
| Suggestions, new conversation | Populate question; clear pending dialogue | Existing explanation endpoint and local chat cancellation | Suggestions alone do not call the provider; new conversation preserves saved analysis/scenario. |
| Transfer proposal → «Проверить сценарий» | Saved proposal; coordinate transfer/departure shift | ScenarioProposal coordinate_transfer; fixed_parameters.shift_seconds; POST runs | Real editable timetable/local graph plus accepted turnaround/resource evidence. Walking input is not an editable timetable. |
| Additional departure proposal | Server-approved departure with demand/resources | ScenarioProposal additional_departure | Accepted demand/resource evidence and whole-service/stop/calendar updates; no inference from a large gap alone. |
| «База / Сценарий» | Switch actual server before/after | ScenarioResult before/after, deltas, after_journeys | Preserve baseline; expose negative/new/lost/unknown effects, distinct affected population, and source geometry gaps. |
| Passenger scale | City–city or subject–subject via capitals | `territory_level`, origin/destination IDs, capital city metadata | Serve real paired source profiles; regional result represents capital-node connection, not every locality. |
| Passenger A/B and «Поменять направление» | Directed pair, swap | origin_id, destination_id | Directional calculation; departure and arrival use their own timezone labels. |
| Passenger modes | Air, rail, bus, private car, ship/ferry | air, rail, road, private_car, water | Actual scheduled/private-car branches. Missing international/transit proof stays unknown. |
| Passenger result mode tabs | Time, first wait, frequency/day/week, max gap, direct/transfers | Generic named Metrics plus Journey/Count/Reference results | Populate these independent facts; private car must not gain invented frequency/transfers. |
| Cargo scale and direction | Urban/inter; outbound/inbound; A/B+swap | UrbanCargoRequest / IntercityCargoRequest | Separate real delivery directions and compatible freight nodes. |
| Cargo layers | Enterprises, warehouses, ports, terminals, rail freight stations | freight_site / freight_node MapFeature kinds | Need authoritative freight site/node subtype metadata for separate layer switches; current MapFeature lacks those subtype fields. |
| Cargo commodity/handling | Food, nonfood; Desktop also has raw material; container/general/bulk/liquid | DeliveryCargoProfile food_class, handling_requirements; cargo_profile_id | Complete deterministic original-control tuple binding, or an approved input seam. Do not silently map raw material to nonfood. |
| Cargo constraints | Temperature, urgent; Desktop also has independent perishable | TemperatureRequirements, urgency, profile catalog | Perishable and food are distinct, and perishable is not a temperature range. Do not invent numeric temperature bounds or choose an arbitrary matching profile. |
| Pipeline product | Oil / oil products / gas; only intercity liquid profile | Product-specific PipelineCargoProfile | Actual endpoint connection proof; line transit is not connection. No invented departure frequency or general cargo score penalty. |
| Theme and panel controls | Auto/light/dark, collapse, mobile filters/chat, close/reopen card | Presentation state | Shared original DOM, keyboard handling, mobile focus isolation; no data/provider call. |
| «Состояния» menu | Prototype state-preview tool | Actual job/result/error states | State preview must never masquerade as an actual successful calculation or replace saved API facts. No backend calculation is implied by this prototype-only testing tool. |

## Exact source locations and deliberate distinctions

- Desktop controls: `index.html` 1777–1810; calculation/status 1620–1700;
  clock 2330–2355; result cards 2380–2545; agent and scenarios 2550–2705.
- React counterparts: `FiltersPanel.tsx`, `TimeDock.tsx`, `ResultCard.tsx`,
  `AgentChat.tsx`, `AtlasPage.tsx`.
- Desktop has **raw material** and **perishable** controls that the earlier
  React port omitted. These are recorded as gaps, not silently equated to other
  canonical fields.
- Both actual prototype versions explicitly describe a **fixed** proposal and
  say its parameters are not edited. There is no route-drawing or arbitrary
  scenario constructor. Root confirmed PRODUCT_SPEC section 63 excludes that
  constructor; no new editor is part of this parity work.
- Prototype H=30 and 44→36 are illustrative. Real server H, time, scores and
  scenario effects are displayed unchanged, including null/partial states.
- `observed_route_accessibility` and its `best_observed_time` operand are
  explicitly preliminary found-route facts. They must not rename or overwrite
  unknown canonical category/point/city indices. Multi-category and city totals
  cannot be reconstructed by skipping unknown values.
- Applicable snapshot/profile binding is internal. Provenance belongs in the
  existing sources disclosure, not a required technical snapshot/hash selector.

## Frontend implementation boundary

Shared controlled presenters retain the incumbent DOM/CSS and actions. Demo
controllers keep their illustrative fixtures; the API controller keeps the
canonical gateway/session, status/hash/revision checks and server values. No API
result is converted into a legacy `Cell`, `canonCell`, `cellScore` or `aggregate`.
Unavailable backend functionality remains a planned visible action with a
meaningful original empty/error state while its owning backend is enhanced.
