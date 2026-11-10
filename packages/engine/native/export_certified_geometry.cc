// Read only shapes for an already certified directed traversal sequence.
// This does not route, change the certificate, or contact a service.
#include <boost/property_tree/json_parser.hpp>
#include <valhalla/baldr/graphreader.h>
#include <valhalla/midgard/util.h>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <vector>

using namespace valhalla;
using namespace valhalla::baldr;

template <class T> uint64_t bits(T value) {
  uint64_t out = 0;
  std::memcpy(&out, &value, sizeof(value));
  return out;
}
double number(uint64_t value) {
  double out;
  std::memcpy(&out, &value, sizeof(value));
  if (!std::isfinite(out)) throw std::runtime_error("nonfinite coordinate");
  return out;
}
std::string read(const char* path) {
  std::ifstream stream(path);
  if (!stream) throw std::runtime_error("input unavailable");
  return {std::istreambuf_iterator<char>(stream), {}};
}
void point(const midgard::PointLL& ll) {
  std::cout << '[' << bits(ll.lng()) << ',' << bits(ll.lat()) << ']';
}

int main(int argc, char** argv) {
  try {
    if (argc != 3) throw std::runtime_error("usage: export-certified-geometry config plan");
    midgard::logging::Configure({{"type", "file"}, {"file_name", "/dev/stderr"}});
    boost::property_tree::ptree config, plan;
    std::istringstream config_stream(read(argv[1])), plan_stream(read(argv[2]));
    boost::property_tree::read_json(config_stream, config);
    boost::property_tree::read_json(plan_stream, plan);
    GraphReader reader(config.get_child("mjolnir"));
    std::map<uint64_t, GraphId> starts;
    // Use actual node ownership; no inference from an opposite edge's shape.
    for (auto tile_id : reader.GetTileSet()) {
      auto tile = reader.GetGraphTile(tile_id);
      if (!tile) throw std::runtime_error("missing tile");
      for (uint32_t i = 0; i < tile->header()->nodecount(); ++i) {
        auto node = tile->node(i);
        for (uint32_t j = 0; j < node->edge_count(); ++j) {
          GraphId edge(tile_id.tileid(), tile_id.level(), node->edge_index() + j);
          if (!starts.emplace(edge.value, GraphId(tile_id.tileid(), tile_id.level(), i)).second)
            throw std::runtime_error("duplicate edge ownership");
        }
      }
    }
    std::cout << "{\"kind\":\"geometry_header\",\"schema\":1,\"shape_decode_precision_bits\":"
      << bits(DECODE_PRECISION) << ",\"shape_encode_precision\":" << ENCODE_PRECISION
      << ",\"plan_sha256\":\""
      << plan.get<std::string>("plan_sha256") << "\",\"source_scope_id\":\""
      << plan.get<std::string>("bindings.source_scope_id") << "\"}\n";
    for (auto& item : plan.get_child("endpoints")) {
      auto& row = item.second;
      GraphId id(row.get<uint64_t>("edge_id"));
      auto tile = reader.GetGraphTile(id);
      auto edge = tile->directededge(id);
      auto shape = tile->edgeinfo(edge).shape();
      if (shape.size() < 2) throw std::runtime_error("empty endpoint shape");
      auto query = row.get_child("query_bits").begin();
      double lon = number(query++->second.get_value<uint64_t>());
      double lat = number(query->second.get_value<uint64_t>());
      midgard::projector_t projector({lon, lat});
      double best = std::numeric_limits<double>::infinity();
      size_t segment = 0;
      midgard::PointLL projected;
      // Same native projector, storage order and strict comparison as Loki 3.5.1.
      for (size_t i = 0; i + 1 < shape.size(); ++i) {
        auto p = projector(shape[i], shape[i + 1]);
        auto distance = projector.approx.DistanceSquared(p);
        if (distance < best) { best = distance; projected = p; segment = i; }
      }
      auto expected = row.get_child("projected_bits").begin();
      uint64_t expected_lon = expected++->second.get_value<uint64_t>();
      uint64_t expected_lat = expected->second.get_value<uint64_t>();
      if (bits(projected.lng()) != expected_lon || bits(projected.lat()) != expected_lat)
        throw std::runtime_error("projection differs from certified Loki bits");
      std::cout << "{\"kind\":\"geometry_projection\",\"endpoint\":" << row.get<int>("endpoint")
        << ",\"index\":" << row.get<int>("index") << ",\"edge_id\":" << id.value
        << ",\"storage_segment\":" << segment << ",\"lon_scale_bits\":" << bits(projector.lon_scale)
        << ",\"approx_m_per_lng_degree_bits\":"
        << bits(midgard::DistanceApproximator<midgard::PointLL>::MetersPerLngDegree(lat))
        << ",\"projected_bits\":"; point(projected); std::cout << "}\n";
    }
    uint64_t ordinal = 0;
    for (auto& item : plan.get_child("edges")) {
      auto& row = item.second;
      GraphId id(row.get<uint64_t>("id"));
      auto tile = reader.GetGraphTile(id);
      auto edge = tile->directededge(id);
      auto info = tile->edgeinfo(edge);
      auto start = starts.at(id.value);
      auto end_tile = reader.GetGraphTile(edge->endnode());
      if (!end_tile || edge->is_shortcut()) throw std::runtime_error("unsupported edge");
      std::cout << "{\"kind\":\"geometry_edge\",\"ordinal\":" << ordinal++
        << ",\"id\":" << id.value << ",\"fraction_bits\":" << row.get<uint32_t>("fraction_bits")
        << ",\"from\":" << start.value << ",\"to\":" << edge->endnode().value
        << ",\"forward\":" << (edge->forward() ? "true" : "false")
        << ",\"way_id\":" << info.wayid() << ",\"from_node_bits\":";
      point(tile->get_node_ll(start));
      std::cout << ",\"to_node_bits\":"; point(end_tile->get_node_ll(edge->endnode()));
      std::cout << ",\"shape_bits\":[";
      bool first = true;
      for (auto& p : info.shape()) { if (!first) std::cout << ','; first = false; point(p); }
      std::cout << "]}\n";
    }
    std::cout << "{\"kind\":\"geometry_footer\",\"edges\":" << ordinal << "}\n";
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
