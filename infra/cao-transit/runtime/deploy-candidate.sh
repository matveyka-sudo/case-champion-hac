set -eu
d=/opt/waydex/defense-20261113
p=/opt/waydex-moscow-real
stage=$d/source/infra/cao-transit/runtime
image=waydex-defense-api:20261113
docker() { sudo -n docker "$@"; }

for artifact in data-500m.json metadata.json transit-500m.json; do
  touch "$stage/$artifact"
done

docker run -d --name waydex-defense-cao --restart unless-stopped --network waydex-moscow-real_default \
  --env-file "$d/consultant.env" \
  -e PYTHONPATH=/preview:/app/apps/api/src -e A2_SECURE_COOKIES=true \
  -e A2_ADDRESS_CATALOGUE=/address/catalog.sqlite \
  -e A2_ADDRESS_ROUTER_URL=http://waydex-moscow-real-valhalla-1:8002 \
  -e WAYDEX_CAO_ROUTING_DIR=/routing -e WAYDEX_CAO_GTFS=/address/ground.zip \
  -v "$d/source/apps/api/src/a2_api:/app/apps/api/src/a2_api:ro" \
  -v "$stage:/preview:ro" -v "$d/address:/address:ro" \
  -v "$p/cao-react-preview/data-500m.json:/preview/data-500m.json:ro" \
  -v "$p/cao-react-preview/metadata.json:/preview/metadata.json:ro" \
  -v "$p/cao-transit-500m/transit-500m.json:/preview/transit-500m.json:ro" \
  -v "$p/cao-transit-500m/network.json:/routing/network.json:ro" \
  -v "$p/cao-transit-500m/links.json:/routing/links.json:ro" \
  "$image" /app/.venv/bin/python -m uvicorn preview:build_app --factory --host 0.0.0.0 --port 8000
docker network connect waydex_default waydex-defense-cao

docker run -d --name waydex-defense-web --restart unless-stopped --network waydex_default \
  -v "$d/dist:/usr/share/nginx/html:ro" -v "$stage/static.conf:/etc/nginx/conf.d/default.conf:ro" \
  nginx:1.28.0-alpine@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284
