#!/bin/sh
set -eu

usage() {
  printf 'Usage: %s --env-file PATH COMMAND [ARGS...]\n' "${0##*/}" >&2
  exit 2
}

case ${1-} in
  --env-file)
    [ "$#" -ge 2 ] || usage
    demo_env=$2
    shift 2
    ;;
  --env-file=*)
    demo_env=${1#*=}
    shift
    ;;
  *) usage ;;
esac
[ -n "$demo_env" ] || usage
case ${1-} in
  ""|-*) usage ;;
esac

# Give the selected demo file precedence for deployment settings.
unset COMPOSE_PROJECT_NAME WAYDEX_HTTP_PORT WAYDEX_REVISION WAYDEX_SOURCE_DIR \
  WAYDEX_DEPLOYMENT_DIR WAYDEX_PRODUCT_MANIFEST WAYDEX_PRODUCT_SHA256 \
  POSTGRES_PASSWORD WAYDEX_ASSISTANT_ENV_FILE

# Keep Docker CLI discovery settings while resolving dotenv aliases from the file.
# Render only the project and port so the probe does not collect credentials.
identity=$(env -i PATH="$PATH" HOME="${HOME-}" DOCKER_CONFIG="${DOCKER_CONFIG-}" \
  docker compose --env-file "$demo_env" -f - config --format json <<'YAML'
name: "${COMPOSE_PROJECT_NAME:?set a unique demo project in --env-file}"
services:
  identity:
    image: scratch
    environment:
      WAYDEX_HTTP_PORT: "${WAYDEX_HTTP_PORT:?set an unused loopback port in --env-file}"
YAML
)
project_and_port=$(printf '%s\n' "$identity" | python3 -c '
import json
import re
import sys

project = json.load(sys.stdin)
port = project["services"]["identity"]["environment"]["WAYDEX_HTTP_PORT"]
if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
    sys.exit("WAYDEX_HTTP_PORT must be a single TCP port from 1 to 65535 in --env-file")
print(project["name"], port)
')
IFS=' ' read -r project_name demo_port <<EOF
$project_and_port
EOF

source_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
WAYDEX_HTTP_PORT="$demo_port" exec docker compose \
  --project-name "$project_name" --env-file "$demo_env" \
  -f "$source_root/infra/full-stack/compose.product.yaml" \
  -f "$source_root/infra/full-stack/compose.product-generated-demo.yaml" \
  "$@"
