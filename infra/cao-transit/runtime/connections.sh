set -eu
d=/opt/waydex/defense-20261113
runtime=/opt/waydex-moscow-real/data/moscow-pilot-runtime
docker() { sudo -n docker "$@"; }
umask 077
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' waydex-moscow-real-api-1 |
  awk -F= '$1 == "DATABASE_URL" {print}' > "$d/connections.env"
python3 - "$runtime" >> "$d/connections.env" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
settings = json.loads((root / "runtime-settings.json").read_text())
digest = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
settings.update(
    A2_PRODUCT_RUNTIME_SHA256=digest,
    A2_CODE_VERSION="defense-20261113-" + digest,
    DATABASE_SCHEMA="waydex_defense",
    A2_SECURE_COOKIES="true",
)
for key, value in settings.items():
    print(f"{key}={value}")
PY

app() {
  docker run "$@" --network waydex-moscow-real_default \
    --env-file "$d/connections.env" --env-file "$d/consultant.env" \
    -e PYTHONPATH=/app/apps/api/src -e A2_ADDRESS_CATALOGUE=/address/catalog.sqlite \
    -e A2_ADDRESS_ROUTER_URL=http://waydex-moscow-real-valhalla-1:8002 \
    -v "$runtime:/runtime:ro" -v "$d/address:/address:ro" \
    -v "$d/source/apps/api/src/a2_api:/app/apps/api/src/a2_api:ro" \
    -v waydex-defense-observations:/observations waydex-defense-api:20261113
}
# Run the same argument set with a selected entry point.
docker run --rm --network waydex-moscow-real_default --env-file "$d/connections.env" \
  waydex-defense-api:20261113 /app/.venv/bin/alembic -c /app/apps/api/alembic.ini upgrade head
app -d --name waydex-defense-connections --restart unless-stopped
docker network connect waydex_default waydex-defense-connections
docker run -d --name waydex-defense-worker --restart unless-stopped --network waydex-moscow-real_default \
  --env-file "$d/connections.env" \
  -e PYTHONPATH=/app/apps/api/src \
  -v "$runtime:/runtime:ro" -v "$d/source/apps/api/src/a2_api:/app/apps/api/src/a2_api:ro" \
  -v waydex-defense-observations:/observations waydex-defense-api:20261113 \
  /app/.venv/bin/python -m a2_api.workers.cli worker
