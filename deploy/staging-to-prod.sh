#!/usr/bin/env bash
# Go-live, once: make prod a copy of staging - the LogiTestHub database, the data folder (encryption keys, code copies,
# CLI package) and the run recordings. For when prod is still empty: everything made on staging (projects, modules,
# login helpers, test cases with their ids, repo links, lessons) keeps its ids, so the Tester_CLI test repo's
# "case_id:" lines and tester.json stay right.
#
#   sudo bash staging-to-prod.sh            # shows what it will do, asks you to type COPY
#
# Before: merge staging into prod on GitHub and update the prod code (setup-server.sh --config prod.conf, or git pull).
# It backs up the prod database first (/var/backups/logitesthub), stops both services while copying, and turns
# "Pick up clicks from PM" off on staging (only one server may read PM's queues). Prod keeps its own data/mysql.json.
set -euo pipefail

S=/var/www/logitesthub/staging
P=/var/www/logitesthub/prod
SVC_S=logitesthub-staging
SVC_P=logitesthub
APP_USER=ubuntu
HOST_S=lthuat.logimaxindia.com
HOST_P=lth.logimaxindia.com
BK=/var/backups/logitesthub
STAMP=$(date +%Y%m%d-%H%M%S)

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[ "$(id -u)" = 0 ] || die "run with sudo"
for d in "$S" "$P"; do [ -f "$d/manager/data/mysql.json" ] || die "$d/manager/data/mysql.json not found - is that site installed?"; done
command -v mysqldump >/dev/null && command -v mysql >/dev/null || die "mysql client not installed (apt install mysql-client)"
# MySQL 8 mysqldump on a GTID server (RDS) needs --set-gtid-purged=OFF; MariaDB's mysqldump does not know the option
GTID=""; mysqldump --help 2>/dev/null | grep -q -- "--set-gtid-purged" && GTID="--set-gtid-purged=OFF"

# host / port / user / password / database of one site, as S_* or P_* shell variables (password never printed)
dbvars() {
  python3 - "$1" "$2" <<'PY'
import json, shlex, sys
c = json.load(open(sys.argv[1]))
c = c.get('default', c)
for k in ('host', 'port', 'user', 'password', 'database'):
    print(f"{sys.argv[2]}_{k.upper()}={shlex.quote(str(c.get(k) or ''))}")
PY
}
eval "$(dbvars "$S/manager/data/mysql.json" S)"
eval "$(dbvars "$P/manager/data/mysql.json" P)"
S_PORT=${S_PORT:-3306}; P_PORT=${P_PORT:-3306}
[ -n "$S_DATABASE" ] && [ -n "$P_DATABASE" ] || die "a database name is missing in mysql.json"
[ "$S_DATABASE@$S_HOST" != "$P_DATABASE@$P_HOST" ] || die "staging and prod use the same database"

q_s() { MYSQL_PWD="$S_PASSWORD" mysql -h "$S_HOST" -P "$S_PORT" -u "$S_USER" -N -B "$S_DATABASE" -e "$1"; }
q_p() { MYSQL_PWD="$P_PASSWORD" mysql -h "$P_HOST" -P "$P_PORT" -u "$P_USER" -N -B "$P_DATABASE" -e "$1"; }

step "What will be copied"
echo "  staging : $S  ($S_DATABASE on $S_HOST)  code $(sudo -u $APP_USER git -C "$S" log --oneline -1)"
echo "  prod    : $P  ($P_DATABASE on $P_HOST)  code $(sudo -u $APP_USER git -C "$P" log --oneline -1)"
echo "  staging has: $(q_s 'SELECT COUNT(*) FROM projects') projects, $(q_s 'SELECT COUNT(*) FROM cases') test cases, $(q_s 'SELECT COUNT(*) FROM runs') runs, $(q_s 'SELECT COUNT(*) FROM users') users"
PC=$(q_p 'SELECT COUNT(*) FROM cases' 2>/dev/null || echo 0)
PR=$(q_p 'SELECT COUNT(*) FROM runs' 2>/dev/null || echo 0)
echo "  prod has   : $PC test cases, $PR runs  (they will be REPLACED; a backup is made first)"
if [ "$(sudo -u $APP_USER git -C "$S" rev-parse HEAD^{tree})" != "$(sudo -u $APP_USER git -C "$P" rev-parse HEAD^{tree})" ]; then
  echo "  WARNING: prod does not run the same code as staging. Merge staging into prod and update prod first,"
  echo "           or the copied database may not match the code."
fi
[ "$PC" = 0 ] || echo "  WARNING: prod is not empty. Only go on if those test cases may be thrown away."
read -rp $'\nType COPY to replace prod with staging: ' ans
[ "$ans" = COPY ] || die "stopped, nothing changed"

step "1/7 Stop both services"
systemctl stop "$SVC_S" "$SVC_P"
trap 'echo; echo "Something failed. Prod backup: $BK/prod-$STAMP.sql.gz. Starting staging again."; systemctl start "$SVC_S" || true' ERR

step "2/7 Back up the prod database"
mkdir -p "$BK"; chmod 700 "$BK"
MYSQL_PWD="$P_PASSWORD" mysqldump -h "$P_HOST" -P "$P_PORT" -u "$P_USER" --single-transaction --no-tablespaces \
  $GTID "$P_DATABASE" | gzip > "$BK/prod-$STAMP.sql.gz"
echo "  $BK/prod-$STAMP.sql.gz"

step "3/7 Copy the staging database into prod"
MYSQL_PWD="$S_PASSWORD" mysqldump -h "$S_HOST" -P "$S_PORT" -u "$S_USER" --single-transaction --no-tablespaces \
  $GTID "$S_DATABASE" \
  | MYSQL_PWD="$P_PASSWORD" mysql -h "$P_HOST" -P "$P_PORT" -u "$P_USER" "$P_DATABASE"
echo "  prod now has $(q_p 'SELECT COUNT(*) FROM cases') test cases"

step "4/7 Copy the data folder (keys, code copies, CLI package) and the run recordings"
# env.key decrypts saved passwords / tokens / API key; secret.key keeps users signed in. mysql.json stays prod's own.
rsync -a --exclude mysql.json "$S/manager/data/" "$P/manager/data/"
mkdir -p "$S/runs" "$P/runs"
rsync -a "$S/runs/" "$P/runs/"
chown -R "$APP_USER": "$P/manager/data" "$P/runs"

step "5/7 Point prod's records at prod"
q_p "UPDATE runs SET run_dir=REPLACE(run_dir, '$S/', '$P/') WHERE run_dir LIKE '$S/%'"
q_p "UPDATE settings SET value=REPLACE(value, '$HOST_S', '$HOST_P') WHERE name='pm.public_url'"
q_s "INSERT INTO settings(name, value) VALUES ('pm.cli_poll', '') ON DUPLICATE KEY UPDATE value=''"
echo "  run folders -> $P/runs ; PM public URL -> $HOST_P ; staging no longer picks up PM clicks"

step "6/7 Start both services"
trap - ERR
systemctl start "$SVC_P" "$SVC_S"
sleep 4
systemctl is-active "$SVC_P" "$SVC_S"

step "7/7 Check"
echo "  prod   https://$HOST_P/login -> $(curl -s -o /dev/null -w '%{http_code}' "https://$HOST_P/login")"
echo "  PM settings on prod (check them in Settings > Integrations):"
q_p "SELECT name, IF(name LIKE '%key%', '(saved)', value) FROM settings WHERE name LIKE 'pm.%' AND name NOT LIKE 'pm.%since%'" | sed 's/^/    /'
cat <<EOF

Done. Next:
  - Settings > Environments on prod: each environment points at the right site (Dev / QA / Live).
  - Settings > Integrations on prod: "Pick up clicks from PM" is ON (it was copied from staging).
  - Users: npm install -g https://$HOST_P/cli/tester.tgz, then tester login --server https://$HOST_P
  - Undo: gunzip -c $BK/prod-$STAMP.sql.gz | mysql ... $P_DATABASE
EOF
