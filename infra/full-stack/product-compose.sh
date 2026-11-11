#!/bin/sh
# One Compose application; no pre-existing router, network or database required.
set -eu
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec docker compose \
  -f "$source_root/infra/full-stack/compose.product.yaml" \
  -f "$source_root/infra/full-stack/compose.product-urban-walk.yaml" \
  -f "$source_root/infra/full-stack/compose.product-novorossiysk-walk.yaml" \
  -f "$source_root/infra/full-stack/compose.product-kaliningrad-router.yaml" \
  -f "$source_root/infra/full-stack/compose.product-fourprofile.yaml" \
  -f "$source_root/infra/full-stack/compose.product-city-categories.yaml" \
  -f "$source_root/infra/full-stack/compose.product-cargo.yaml" \
  -f "$source_root/infra/full-stack/compose.product-truck-routing.yaml" \
  "$@"
