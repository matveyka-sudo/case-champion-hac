# Демо-стенд Waydex

Docker Compose запускает PostgreSQL 17, FastAPI и Nginx с собранным фронтендом. Снаружи доступны HTTP и HTTPS через Nginx. API и база работают внутри сети Compose. Карта использует демо-данные. Для сквозного расчёта отдельно подключаются принятые данные, обработчик и воркер.

Нужны Docker Engine с Buildx, Docker Compose v2 и Certbot на хосте. Для исходников API нужна ревизия с установкой workspace-пакетов из [PR #22](https://github.com/matveyka-sudo/case-champion-hac/pull/22). До его слияния проверенная ревизия приложения — `8a65fb770ea4c9a07db7b43d6cf66faa3e7c41b1`. Конфигурация стенда может лежать в отдельном checkout. `SOURCE_DIR` и `WAYDEX_CONFIG_DIR` указывают на эти каталоги независимо.

## Подготовить ревизию

Для новой ВМ создайте каталоги `/opt/waydex/releases` и `/opt/waydex/deploy`, доступные оператору, и один checkout `/opt/waydex/repo` из GitHub. После слияния обоих PR экспортируйте выбранный коммит `dev` в чистый каталог. `git archive` включает только файлы из Git, поэтому локальные настройки и зависимости не попадают в контекст сборки.

```bash
set -o pipefail
repo=/opt/waydex/repo
git -C "$repo" fetch origin dev
revision=$(git -C "$repo" rev-parse origin/dev)
source_dir="/opt/waydex/releases/$revision"
mkdir "$source_dir"
git -C "$repo" archive "$revision" | tar -x -C "$source_dir"
config_dir="$source_dir/infra/waydex"
release_env="/opt/waydex/deploy/$revision.env"
```

На существующей ВМ скопируйте предыдущий приватный файл окружения с сохранением прав, например `cp -p /opt/waydex/deploy/.env "$release_env"` для первого перехода на эту конфигурацию. В копии обновите `SOURCE_DIR`, `WAYDEX_CONFIG_DIR` и `WAYDEX_REVISION` по значениям выше. Сохраните действующий `POSTGRES_PASSWORD`: том уже инициализирован с этим паролем.

Для новой базы создайте приватный файл по [.env.example](.env.example) с правами `600`, теми же путями и новым паролем из `openssl rand -hex 32`. Файл окружения храните отдельно от экспортированных исходников.

Во всех командах используйте один проект Compose `waydex`. На текущей ВМ это сохраняет существующий том `waydex_postgres_data`.

```bash
waydex() {
  sudo docker compose --project-name waydex --env-file "$release_env" \
    -f "$config_dir/compose.yaml" "$@"
}
waydex_tls() {
  waydex -f "$config_dir/compose.https.yaml" "$@"
}
```

## Первый запуск

Для новой ВМ подготовьте каталог ACME и запустите HTTP. Проверьте, что A-запись `waydex.mhimf.net` ведёт на ВМ и входящие TCP-порты 80 и 443 открыты.

```bash
sudo install -d -m 0755 /var/www/waydex-acme
waydex config --quiet
waydex build api web
waydex up -d --wait --wait-timeout 90 postgres
waydex run --rm --no-deps -T --interactive=false api /app/.venv/bin/alembic -c /app/apps/api/alembic.ini upgrade head
waydex up -d --no-build --wait --wait-timeout 90
```

Certbot пишет файлы проверки в общий каталог ACME. Nginx читает сертификат и ключ из каталогов `live` и `archive`, смонтированных только для чтения.

```bash
sudo certbot certonly --webroot -w /var/www/waydex-acme -d waydex.mhimf.net
waydex_tls config --quiet
waydex_tls run --rm --no-deps -T --interactive=false web nginx -t
waydex_tls up -d --no-build --wait --wait-timeout 90
sudo install -D -m 0755 "$config_dir/renew-nginx.sh" /etc/letsencrypt/renewal-hooks/deploy/waydex-nginx
sudo systemctl enable --now certbot.timer
sudo certbot renew --cert-name waydex.mhimf.net --dry-run
```

## Обновление и откат

На текущем стенде сертификат уже выпущен. Сохраните предыдущие образы и приватный файл окружения релиза. Перед миграциями проверьте совместимость с предыдущей версией API и снимите резервную копию PostgreSQL.

```bash
backup="/opt/waydex/deploy/waydex-before-$revision.dump"
(umask 077; waydex_tls exec -T postgres pg_dump -U waydex -d waydex -Fc > "$backup")
test -s "$backup"
waydex_tls config --quiet
waydex_tls build api web
waydex_tls run --rm --no-deps -T --interactive=false web nginx -t
waydex_tls run --rm --no-deps -T --interactive=false api /app/.venv/bin/alembic -c /app/apps/api/alembic.ini upgrade head
waydex_tls run --rm --no-deps -T --interactive=false api /app/.venv/bin/alembic -c /app/apps/api/alembic.ini current --check-heads
waydex_tls up -d --no-build --wait --wait-timeout 90
curl -fsS https://waydex.mhimf.net/health
curl -fsS https://waydex.mhimf.net/api/v1/capabilities
```

Для отката выставьте `config_dir` и `release_env` на предыдущий релиз и выполните `waydex_tls up -d --no-build --pull never --wait --wait-timeout 90`. Compose возьмёт сохранённые образы по тегу коммита. Если это первый переход с ручной конфигурации, предыдущий запуск находится в `/opt/waydex/deploy/compose.yaml` и использует её прежний `.env`. Для отката после несовместимой миграции требуется отдельное восстановление базы по проверенной резервной копии. Обычный откат сохраняет том PostgreSQL.
