set -eu
d=/opt/waydex/defense-20261113
app=/opt/waydex/releases/91ed5262b62c16afa3ca619f8140c43917cf8325/infra/waydex/nginx-app.conf
web=waydex-web-1
backup=$d/nginx-app.before-defense.conf
test -s "$app"
test -s "$d/source/infra/cao-transit/runtime/edge.conf"
if ! test -e "$backup"; then cp -p "$app" "$backup"; fi
rollback() {
  cat "$backup" > "$app"
  sudo -n docker exec "$web" nginx -s reload
}
trap rollback EXIT
cat "$d/source/infra/cao-transit/runtime/edge.conf" > "$app"
sudo -n docker exec "$web" nginx -t
sudo -n docker exec "$web" nginx -s reload
curl --retry 3 --retry-all-errors --silent --show-error --fail --resolve waydex.mhimf.net:443:127.0.0.1 https://waydex.mhimf.net/map | grep -o 'assets/index-[^"]*\.js'
curl --retry 3 --retry-all-errors --silent --show-error --fail --resolve waydex.mhimf.net:443:127.0.0.1 https://waydex.mhimf.net/cao-api/v1/capabilities >/dev/null
trap - EXIT
