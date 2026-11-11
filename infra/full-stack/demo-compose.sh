#!/bin/sh
set -eu
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec docker compose \
  -f "$source_root/infra/full-stack/compose.product.yaml" \
  -f "$source_root/infra/full-stack/compose.product-generated-demo.yaml" \
  "$@"
