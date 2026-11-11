// Read-only extraction of the actual actor route's graph IDs and clock fields.
#include <boost/property_tree/json_parser.hpp>
#include <valhalla/tyr/actor.h>
#include <valhalla/midgard/logging.h>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>

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
int main(int argc, char** argv) {
  try {
    if (argc != 3) throw std::runtime_error("usage: observed-route config request");
    valhalla::midgard::logging::Configure({{"type", "file"}, {"file_name", "/dev/stderr"}});
    boost::property_tree::ptree config;
    std::istringstream stream(read(argv[1]));
    boost::property_tree::read_json(stream, config);
    valhalla::tyr::actor_t actor(config);
    valhalla::Api api;
    auto json = actor.route(read(argv[2]), nullptr, &api);
    std::cout << "{\"kind\":\"route_response\",\"response\":" << json << "}\n";
    for (const auto& route : api.trip().routes())
      for (const auto& leg : route.legs())
        for (const auto& node : leg.node()) {
          std::cout << "{\"kind\":\"path_node\",\"has_edge\":" << (node.has_edge() ? "true" : "false")
            << ",\"edge\":" << node.edge().id()
            << ",\"source_percent_bits\":" << bits(node.edge().source_along_edge())
            << ",\"target_percent_bits\":" << bits(node.edge().target_along_edge())
            << ",\"elapsed_seconds_bits\":" << bits(node.cost().elapsed_cost().seconds())
            << ",\"turn_seconds_bits\":" << bits(node.cost().transition_cost().seconds()) << "}\n";
        }
    for (int i = 0; i < api.options().locations_size(); ++i)
      for (const auto& edge : api.options().locations(i).correlation().edges())
        std::cout << "{\"kind\":\"actor_endpoint\",\"endpoint\":" << i << ",\"edge\":" << edge.graph_id()
          << ",\"percent_bits\":" << bits(edge.percent_along()) << "}\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
