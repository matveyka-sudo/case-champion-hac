// Read-only Valhalla 3.5.1 pedestrian graph relaxation and candidate replay.
// Build against the pinned runtime's headers and libvalhalla; see README.md.
#include <boost/property_tree/json_parser.hpp>
#include <valhalla/baldr/graphreader.h>
#include <valhalla/loki/search.h>
#include <valhalla/sif/costfactory.h>
#include <valhalla/sif/edgelabel.h>
#include <valhalla/worker.h>
#include <algorithm>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <limits>
#include <sstream>
#include <vector>

using namespace valhalla;
using namespace valhalla::baldr;
using namespace valhalla::sif;

template <class T> uint64_t bits(T value) {
  uint64_t result = 0;
  std::memcpy(&result, &value, sizeof(value));
  return result;
}
std::string read(const char* path) {
  std::ifstream stream(path);
  if (!stream) throw std::runtime_error("input unavailable");
  return {std::istreambuf_iterator<char>(stream), {}};
}
// These are necessary conditions in PedestrianCost::Allowed (3.5.1).
// Deliberately retain surface, SAC, turn, distance and access-restricted edges.
bool retained(const DirectedEdge* edge, const cost_ptr_t& cost) {
  return !edge->is_shortcut() && cost->IsAccessible(edge) &&
         edge->use() != Use::kPlatformConnection && edge->use() != Use::kEgressConnection &&
         edge->use() != Use::kTransitConnection;
}

int main(int argc, char** argv) {
  try {
    midgard::logging::Configure({{"type", "file"}, {"file_name", "/dev/stderr"}});
    if (argc < 3 || argc > 4) throw std::runtime_error("usage: source-bound-walk config request [candidate]");
    boost::property_tree::ptree config;
    std::istringstream config_stream(read(argv[1]));
    boost::property_tree::read_json(config_stream, config);
    GraphReader reader(config.get_child("mjolnir"));
    Api api;
    ParseApi(read(argv[2]), Options::route, api);
    if (api.options().costing_type() != Costing::pedestrian || api.options().has_date_time())
      throw std::runtime_error("static pedestrian request required");
    auto cost = CostFactory().Create(api.options());
    auto options = api.options().costings().at(Costing::pedestrian).options();
    if (options.transport_type() != "foot" || options.ignore_access() || options.ignore_oneways() ||
        options.exclude_edges_size()) throw std::runtime_error("unsupported pedestrian scope");
    std::vector<baldr::Location> locations;
    for (const auto& location : api.options().locations()) {
      locations.emplace_back(midgard::PointLL(location.ll().lng(), location.ll().lat()),
                             baldr::Location::StopType::BREAK,
                             location.minimum_reachability(), location.minimum_reachability(),
                             location.radius());
    }
    if (locations.size() != 2) throw std::runtime_error("two locations required");
    auto correlations = loki::Search(locations, reader, cost);
    std::map<uint64_t, uint64_t> starts;
    std::map<uint64_t, std::vector<uint64_t>> incoming, inverse_hierarchy;
    std::vector<GraphId> tiles;
    for (auto id : reader.GetTileSet()) tiles.push_back(id);
    std::sort(tiles.begin(), tiles.end());
    uint64_t all_nodes = 0, all_edges = 0, retained_edges = 0, transitions = 0;
    if (argc == 3) std::cout << "{\"kind\":\"header\",\"schema\":2,\"model\":\"valhalla-3.5.1-binary32-edge-seconds-minimum-turn-relaxation\"}\n";
    for (auto tile_id : tiles) {
      auto tile = reader.GetGraphTile(tile_id);
      if (!tile) throw std::runtime_error("missing inventoried tile");
      all_nodes += tile->header()->nodecount();
      all_edges += tile->header()->directededgecount();
      uint64_t seen_edges = 0;
      for (uint32_t i = 0; i < tile->header()->nodecount(); ++i) {
        GraphId node_id(tile_id.tileid(), tile_id.level(), i);
        auto node = tile->node(i);
        if (argc == 3) std::cout << "{\"kind\":\"node\",\"id\":" << node_id.value << "}\n";
        for (uint32_t j = 0; j < node->edge_count(); ++j) {
          GraphId edge_id(tile_id.tileid(), tile_id.level(), node->edge_index() + j);
          if (!starts.emplace(edge_id.value, node_id.value).second)
            throw std::runtime_error("duplicate edge ownership");
          ++seen_edges;
          auto edge = tile->directededge(edge_id);
          incoming[edge->endnode().value].push_back(edge_id.value);
          bool keep = retained(edge, cost);
          if (!reader.GetGraphTile(edge->endnode())) throw std::runtime_error("missing edge end tile");
          float seconds = keep ? cost->EdgeCost(edge, tile).secs : 0;
          retained_edges += keep;
          if (argc == 3) std::cout << "{\"kind\":\"edge\",\"id\":" << edge_id.value
            << ",\"from\":" << node_id.value << ",\"to\":" << edge->endnode().value
            << ",\"retained\":" << (keep ? "true" : "false") << ",\"seconds_bits\":"
            << bits(seconds) << ",\"length\":" << edge->length() << "}\n";
        }
        for (uint32_t j = 0; j < node->transition_count(); ++j) {
          auto target = tile->transition(node->transition_index() + j)->endnode();
          if (!reader.GetGraphTile(target)) throw std::runtime_error("missing hierarchy tile");
          ++transitions;
          inverse_hierarchy[target.value].push_back(node_id.value);
          if (argc == 3) std::cout << "{\"kind\":\"transition\",\"from\":" << node_id.value
            << ",\"to\":" << target.value << "}\n";
        }
      }
      if (seen_edges != tile->header()->directededgecount())
        throw std::runtime_error("edge ownership is not exhaustive");
      if (argc == 3) std::cout << "{\"kind\":\"tile\",\"id\":" << tile_id.value
        << ",\"nodes\":" << tile->header()->nodecount() << ",\"edges\":" << seen_edges << "}\n";
    }
    // A complete immediate-predecessor relaxation: all incoming edges are kept,
    // even if their access/history would make them inadmissible. Hierarchy arcs
    // are directed, so gather their actual inverse roster rather than assuming
    // that transitions happen to be symmetric.
    if (argc == 3) {
      for (const auto& entry : starts) {
        GraphId edge_id(entry.first), node_id(entry.second);
        auto tile = reader.GetGraphTile(edge_id);
        auto edge = tile->directededge(edge_id);
        if (!retained(edge, cost)) continue;
        auto node = tile->node(node_id);
        std::vector<uint64_t> predecessors = incoming[node_id.value];
        for (auto alias : inverse_hierarchy[node_id.value])
          predecessors.insert(predecessors.end(), incoming[alias].begin(), incoming[alias].end());
        float minimum = std::numeric_limits<float>::infinity();
        for (auto predecessor : predecessors) {
          GraphId predecessor_id(predecessor);
          auto predecessor_tile = reader.GetGraphTile(predecessor_id);
          auto predecessor_edge = predecessor_tile->directededge(predecessor_id);
          EdgeLabel label(kInvalidLabel, predecessor_id, predecessor_edge, Cost{}, 0,
                          sif::TravelMode::kPedestrian, 0, kInvalidRestriction,
                          false, false, InternalTurn::kNoTurn);
          minimum = std::min(minimum, cost->TransitionCost(edge, node, label).secs);
        }
        if (predecessors.empty()) minimum = 0;
        std::cout << "{\"kind\":\"turn_lower\",\"edge\":" << edge_id.value
          << ",\"seconds_bits\":" << bits(minimum) << ",\"predecessors\":"
          << predecessors.size() << "}\n";
      }
    }
    for (size_t endpoint = 0; endpoint < locations.size(); ++endpoint) {
      auto found = correlations.find(locations[endpoint]);
      if (found == correlations.end()) throw std::runtime_error("uncorrelated endpoint");
      // Include filtered candidates too: they are available to Valhalla retries.
      std::vector<PathLocation::PathEdge> edges = found->second.edges;
      edges.insert(edges.end(), found->second.filtered_edges.begin(), found->second.filtered_edges.end());
      for (size_t i = 0; i < edges.size(); ++i) {
        auto& match = edges[i];
        auto tile = reader.GetGraphTile(match.id);
        auto edge = tile->directededge(match.id);
        float fraction = endpoint ? match.percent_along : 1.0 - match.percent_along;
        float partial = (cost->EdgeCost(edge, tile) * fraction).secs;
        if (argc == 3) std::cout << "{\"kind\":\"endpoint\",\"endpoint\":" << endpoint
          << ",\"index\":" << i << ",\"edge\":" << match.id.value
          << ",\"percent_bits\":" << bits(match.percent_along)
          << ",\"lon_bits\":" << bits(match.projected.lng()) << ",\"lat_bits\":" << bits(match.projected.lat())
          << ",\"partial_seconds_bits\":" << bits(partial) << "}\n";
      }
    }
    if (argc == 3) {
      std::cout << "{\"kind\":\"footer\",\"tiles\":" << tiles.size() << ",\"nodes\":"
        << all_nodes << ",\"edges\":" << all_edges << ",\"retained_edges\":" << retained_edges
        << ",\"transitions\":" << transitions << "}\n";
      return 0;
    }
    // Candidate contains directed traversals with a binary32 fraction per edge.
    // Replay never interprets HTTP success as feasibility. It executes the same
    // Allowed, Restricted, node access and elapsed transition costs as the model.
    boost::property_tree::ptree candidate;
    std::istringstream candidate_stream(read(argv[3]));
    boost::property_tree::read_json(candidate_stream, candidate);
    std::vector<EdgeLabel> labels;
    uint32_t distance = 0;
    uint32_t replayed_rows = 0;
    GraphId previous_node;
    for (auto& row : candidate.get_child("edges")) {
      GraphId edge_id(row.second.get<uint64_t>("id"));
      float fraction;
      uint32_t fraction_bits = row.second.get<uint32_t>("fraction_bits");
      std::memcpy(&fraction, &fraction_bits, sizeof(fraction));
      if (!(fraction >= 0 && fraction <= 1)) throw std::runtime_error("invalid candidate fraction");
      auto tile = reader.GetGraphTile(edge_id);
      auto edge = tile->directededge(edge_id);
      auto start = GraphId(starts.at(edge_id.value));
      auto node = tile->node(start);
      if (!retained(edge, cost) || !cost->Allowed(node) || edge->use() == Use::kFerry ||
          edge->use() == Use::kRailFerry) throw std::runtime_error("candidate access rejected");
      if (previous_node.Is_Valid() && previous_node != start) {
        auto previous_tile = reader.GetGraphTile(previous_node);
        auto previous_info = previous_tile->node(previous_node);
        bool connected = false;
        for (uint32_t j = 0; j < previous_info->transition_count(); ++j)
          connected |= previous_tile->transition(previous_info->transition_index() + j)->endnode() == start;
        if (!connected || !cost->Allowed(previous_info))
          throw std::runtime_error("candidate hierarchy discontinuity");
      }
      // A snapped endpoint at a node traverses none of its correlated edge.
      // Do not fabricate predecessor history or a terminal turn on that edge.
      if (fraction == 0) {
        if (replayed_rows && !row.second.get<bool>("final", false))
          throw std::runtime_error("interior zero traversal unsupported");
        if (!replayed_rows) previous_node = edge->endnode();
        ++replayed_rows;
        std::cout << "{\"kind\":\"replay\",\"id\":" << edge_id.value
          << ",\"fraction_bits\":" << fraction_bits << ",\"edge_seconds_bits\":0,\"turn_seconds_bits\":0}\n";
        continue;
      }
      uint8_t restriction = kInvalidRestriction;
      Cost turn{};
      if (!labels.empty()) {
        if (!cost->Allowed(edge, row.second.get<bool>("final", false), labels.back(), tile,
                           edge_id, 0, 0, restriction) ||
            cost->Restricted(edge, labels.back(), labels, tile, edge_id, true))
          throw std::runtime_error("candidate restriction rejected");
        turn = cost->TransitionCost(edge, node, labels.back());
      } else if (!cost->Allowed(edge, tile)) {
        throw std::runtime_error("candidate initial edge rejected");
      }
      Cost running = cost->EdgeCost(edge, tile) * fraction;
      distance += static_cast<uint32_t>(edge->length() * fraction + .5f);
      auto pred = labels.empty() ? kInvalidLabel : labels.size() - 1;
      labels.emplace_back(pred, edge_id, edge, running, 0, sif::TravelMode::kPedestrian,
                          distance, restriction, false, false, InternalTurn::kNoTurn);
      previous_node = edge->endnode();
      ++replayed_rows;
      std::cout << "{\"kind\":\"replay\",\"id\":" << edge_id.value << ",\"fraction_bits\":" << fraction_bits << ",\"edge_seconds_bits\":"
        << bits(running.secs) << ",\"turn_seconds_bits\":" << bits(turn.secs) << "}\n";
    }
    std::cout << "{\"kind\":\"replay_footer\",\"feasible\":true,\"edges\":" << replayed_rows << "}\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
