#!/bin/sh
set -eu
case " ${RENEWED_DOMAINS:-} " in
  *" waydex.mhimf.net "*)
    /usr/bin/docker exec waydex-web-1 nginx -t
    /usr/bin/docker exec waydex-web-1 nginx -s reload
    ;;
esac
