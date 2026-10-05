#!/usr/bin/env bash
# LogiTestHub: one-shot server setup for Ubuntu 22.04 / 24.04 (AWS EC2).
#
#   sudo bash setup-server.sh                      # asks for every detail (Enter = default shown in [brackets])
#   sudo bash setup-server.sh --config my.conf     # answers from a file (same variable names as below); asks only what is missing
#
# Safe to run again: it updates the code, packages and config in place and keeps the database, keys and runs.
# What it does, in order (each step prints "==> [n/14]"):
#   1 checks   2 timezone   3 apt packages   4 Node.js 20   5 code from GitHub (token, no password prompt)
#   6 npm + Chromium   7 Python venv   8 database (Amazon RDS or local MySQL)   9 data/mysql.json
#   10 tables + admin user   11 env file   12 systemd service   13 nginx   14 HTTPS (certbot / ALB / none)
# Log: /var/log/logitesthub-setup.log   Answers (no secrets): /etc/logitesthub/setup.conf

set -Eeuo pipefail

LOG=/var/log/logitesthub-setup.log
CONF_DIR=/etc/logitesthub
SAVED_CONF="$CONF_DIR/setup.conf"
TOKEN_FILE="$CONF_DIR/github-token"
ENV_FILE="$CONF_DIR/logitesthub.env"
RDS_CA="$CONF_DIR/rds-ca.pem"
SERVICE=logitesthub
STEP="start"
TOTAL=14

# ---------- output helpers ----------
c_red=$'\e[31m'; c_grn=$'\e[32m'; c_yel=$'\e[33m'; c_cyn=$'\e[36m'; c_off=$'\e[0m'
step()  { STEP="$2"; echo; echo "${c_cyn}==> [$1/$TOTAL] $2${c_off}"; }
ok()    { echo "  ${c_grn}OK${c_off} $*"; }
warn()  { echo "  ${c_yel}WARN${c_off} $*"; }
die()   { echo; echo "${c_red}ERROR in step \"$STEP\": $1${c_off}"; [ -n "${2:-}" ] && echo "  Fix: $2"; echo "  Full log: $LOG"; exit 1; }
on_err() { local code=$? line=$1
  echo; echo "${c_red}ERROR in step \"$STEP\" (line $line, exit code $code).${c_off}"
  echo "  The last lines of $LOG show the failing command. After fixing, run the script again: finished steps are skipped or redone safely."
  exit "$code"; }
trap 'on_err $LINENO' ERR

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo:  sudo bash $0"; exit 1; }
mkdir -p "$CONF_DIR"; chmod 755 "$CONF_DIR"
touch "$LOG"; chmod 600 "$LOG"
exec > >(tee -a "$LOG") 2>&1
echo "---- $(date '+%F %T') setup started ----"

# ---------- answers: saved answers of an earlier run (shown as defaults), then --config file (used as is) ----------
if [ -f "$SAVED_CONF" ]; then
  # shellcheck disable=SC1090
  source "$SAVED_CONF"
fi
CONFIG_MODE=0
if [ "${1:-}" = "--config" ]; then
  [ -f "${2:-}" ] || die "config file '${2:-}' not found" "sudo bash $0 --config /path/to/file.conf"
  # shellcheck disable=SC1090
  source "$2"; CONFIG_MODE=1
fi

ask() {   # ask VAR "Question" "default". With --config, a VAR that already has a value is not asked again.
  local var=$1 q=$2 def=${3:-} cur=${!1:-}
  if [ -n "$cur" ] && [ "$CONFIG_MODE" = 1 ]; then return 0; fi
  if [ -n "$cur" ]; then def=$cur; fi
  local a; read -r -p "  $q${def:+ [$def]}: " a </dev/tty || true
  printf -v "$var" '%s' "${a:-$def}"
}
ask_secret() {   # ask_secret VAR "Question" "existing value (Enter keeps it)" required(1/0)
  local var=$1 q=$2 keep=${3:-} req=${4:-1} cur=${!1:-}
  if [ -n "$cur" ]; then return 0; fi
  local a hint=""; [ -n "$keep" ] && hint=" (Enter = keep the current one)"
  while :; do
    read -r -s -p "  $q$hint: " a </dev/tty || true; echo
    a=${a:-$keep}
    if [ -n "$a" ] || [ "$req" = 0 ]; then break; fi
    echo "  ${c_yel}required${c_off}"
  done
  printf -v "$var" '%s' "$a"
}
no_quote() { case "$2" in *"'"*|*'\'*|*'"'*) die "$1 must not contain quotes or backslashes" "choose another value";; esac; }

# values that already exist on this server (re-run): Enter keeps them
OLD_TOKEN=$( [ -f "$TOKEN_FILE" ] && cat "$TOKEN_FILE" || true )
OLD_AI_KEY=$( [ -f "$ENV_FILE" ] && sed -n 's/^ANTHROPIC_API_KEY=//p' "$ENV_FILE" || true )

echo
echo "${c_cyn}LogiTestHub server setup${c_off}: answer the questions below (Enter = value in [brackets])."

echo; echo "  -- Server --"
ask APP_USER     "Linux user that runs the app" "${SUDO_USER:-ubuntu}"
id "$APP_USER" >/dev/null 2>&1 || die "Linux user '$APP_USER' does not exist" "use an existing user (ubuntu) or create it: sudo adduser $APP_USER"
APP_HOME=$(getent passwd "$APP_USER" | cut -d: -f6)
ask INSTALL_DIR  "Install folder" "/opt/logitesthub"
ask TIMEZONE     "Server time zone (times shown in the app)" "Asia/Kolkata"
ask APP_PORT     "App port (internal; nginx forwards 80/443 to it)" "5050"

echo; echo "  -- GitHub (read-only token: Contents = Read) --"
ask GITHUB_REPO   "Repository (owner/name)" "LOGIMAX-CLIENTS/logitesthub"
ask GITHUB_BRANCH "Branch" "main"
ask_secret GITHUB_TOKEN "GitHub token (github_pat_... or ghp_...)" "$OLD_TOKEN" 1

echo; echo "  -- Database --"
ask DB_MODE "Database: 'rds' (Amazon RDS MySQL) or 'local' (MySQL on this server)" "rds"
case "$DB_MODE" in rds|local) ;; *) die "Database must be 'rds' or 'local' (got '$DB_MODE')" "run again and type rds or local";; esac
if [ "$DB_MODE" = rds ]; then
  ask RDS_HOST "RDS endpoint (xxx.yyy.ap-south-1.rds.amazonaws.com)" ""
  [ -n "$RDS_HOST" ] || die "RDS endpoint is required" "AWS Console -> RDS -> Databases -> your DB -> Connectivity & security -> Endpoint"
  ask RDS_PORT "RDS port" "3306"
  ask RDS_MASTER_USER "RDS master user (creates the database + app user; leave empty if they already exist)" "admin"
  if [ -n "$RDS_MASTER_USER" ]; then ask_secret RDS_MASTER_PASSWORD "RDS master password" "" 1; fi
  DB_HOST=$RDS_HOST; DB_PORT=$RDS_PORT
else
  DB_HOST=127.0.0.1; DB_PORT=3306
fi
ask DB_NAME "Database name" "logitesthub"
ask DB_USER "App database user" "logitesthub"
OLD_DB_PW=""
if [ -f "$INSTALL_DIR/manager/data/mysql.json" ]; then
  OLD_DB_PW=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['default'].get('password',''))" "$INSTALL_DIR/manager/data/mysql.json" 2>/dev/null || true)
fi
ask_secret DB_PASSWORD "App database user password (Enter on a first run = generate one)" "$OLD_DB_PW" 0
[ -n "$DB_PASSWORD" ] || DB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
for v in DB_NAME DB_USER; do [[ ${!v} =~ ^[A-Za-z0-9_]+$ ]] || die "$v may only contain letters, digits and _" "run again with another name"; done
no_quote "DB password" "$DB_PASSWORD"; no_quote "RDS master password" "${RDS_MASTER_PASSWORD:-}"

echo; echo "  -- PM sign-in (optional: Unified_DB, read-only user) --"
ask PM_DB_HOST "PM Unified_DB host (Enter = skip, local accounts only)" ""
if [ -n "$PM_DB_HOST" ]; then
  ask PM_DB_PORT "PM DB port" "3306"
  ask PM_DB_NAME "PM DB name" "Unified_DB"
  ask PM_DB_USER "PM DB read-only user (SELECT on Members only)" ""
  ask_secret PM_DB_PASSWORD "PM DB read-only password (Enter on a re-run = keep)" "" 0
fi

echo; echo "  -- Domain / HTTPS --"
ask DOMAIN "Domain name (e.g. tester.logimaxindia.com; Enter = none, use IP)" ""
if [ -n "$DOMAIN" ]; then
  ask SSL_MODE "HTTPS: 'alb' (AWS load balancer + ACM cert), 'certbot' (Let's Encrypt on this server), 'none'" "alb"
else
  SSL_MODE=none
fi
case "$SSL_MODE" in alb|certbot|none) ;; *) die "HTTPS must be alb, certbot or none (got '$SSL_MODE')" "run again";; esac
if [ "$SSL_MODE" = certbot ]; then
  ask CERTBOT_EMAIL "Email for Let's Encrypt expiry notices" ""
  [ -n "$CERTBOT_EMAIL" ] || die "certbot needs an email" "run again and give an email"
fi

echo; echo "  -- AI + first admin --"
ask_secret ANTHROPIC_API_KEY "Anthropic API key (sk-ant-...; Enter = skip, AI steps off)" "$OLD_AI_KEY" 0
ask ADMIN_USERNAME "Admin username" "admin"
ask ADMIN_NAME     "Admin full name" "Administrator"
ask_secret ADMIN_PASSWORD "Admin password, min 8 chars (Enter = skip if this admin already exists)" "" 0

# save the non-secret answers for the next run
umask 077
{
  echo "# LogiTestHub setup answers (no secrets). Written by setup-server.sh; shown as defaults on the next run."
  for v in APP_USER INSTALL_DIR TIMEZONE APP_PORT GITHUB_REPO GITHUB_BRANCH DB_MODE RDS_HOST RDS_PORT RDS_MASTER_USER \
           DB_NAME DB_USER PM_DB_HOST PM_DB_PORT PM_DB_NAME PM_DB_USER DOMAIN SSL_MODE CERTBOT_EMAIL ADMIN_USERNAME ADMIN_NAME; do
    printf '%s=%q\n' "$v" "${!v:-}"
  done
} > "$SAVED_CONF"
umask 022
as_app() { sudo -u "$APP_USER" -H env HOME="$APP_HOME" GIT_TERMINAL_PROMPT=0 "$@"; }

# ================================================================================================
step 1 "Checks: OS, internet, GitHub token"
. /etc/os-release
[ "${ID:-}" = ubuntu ] || warn "tested on Ubuntu only (this is ${PRETTY_NAME:-unknown})"
ok "${PRETTY_NAME:-unknown}, $(nproc) CPU, $(free -m | awk '/Mem:/{print $2}') MB RAM, $(df -h / | awk 'NR==2{print $4}') free on /"
[ "$(free -m | awk '/Mem:/{print $2}')" -ge 1800 ] || warn "less than 2 GB RAM: Chromium runs may be slow or killed (t3.small or bigger recommended)"
curl -fsS --max-time 10 -o /dev/null https://github.com \
  || die "no internet from this server (github.com not reachable)" "private subnet needs a NAT gateway route; ask AWS admin. Test: curl -I https://github.com"
ok "internet"
gh_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$GITHUB_REPO" || true)
case "$gh_code" in
  200) ok "token can read $GITHUB_REPO" ;;
  401) die "GitHub token rejected (401: wrong, expired or revoked)" "make a new token, then run again" ;;
  403) die "token blocked for this org (403)" "classic token: Configure SSO -> Authorize LOGIMAX-CLIENTS. Fine-grained: org admin must approve it" ;;
  404) die "repo $GITHUB_REPO not visible to this token (404)" "fine-grained token: Resource owner = LOGIMAX-CLIENTS, select repo logitesthub, Contents = Read-only" ;;
  *)   die "GitHub API answered HTTP $gh_code" "check internet / token and run again" ;;
esac

step 2 "Time zone"
timedatectl set-timezone "$TIMEZONE" || die "unknown time zone '$TIMEZONE'" "list: timedatectl list-timezones | grep Asia"
ok "$(date)"

step 3 "System packages (apt)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
pkgs=(git curl ca-certificates gnupg python3 python3-venv python3-pip nginx mysql-client)
[ "$DB_MODE" = local ] && pkgs+=(mysql-server)
[ "$SSL_MODE" = certbot ] && pkgs+=(certbot python3-certbot-nginx)
apt-get install -y "${pkgs[@]}"
ok "installed: ${pkgs[*]}"

step 4 "Node.js 20"
node_major=$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || echo 0)
if [ "${node_major:-0}" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
ok "node $(node -v), npm $(npm -v)"

step 5 "Code from GitHub ($GITHUB_REPO, branch $GITHUB_BRANCH)"
# token file: read by git's credential helper, so clone / pull never ask for a username or password
install -m 640 -o root -g "$APP_USER" /dev/null "$TOKEN_FILE"
printf '%s' "$GITHUB_TOKEN" > "$TOKEN_FILE"
HELPER="!f() { test \"\$1\" = get && echo username=x-access-token && echo password=\$(cat $TOKEN_FILE); }; f"
REPO_URL="https://github.com/$GITHUB_REPO.git"
export GIT_TERMINAL_PROMPT=0
mkdir -p "$(dirname "$INSTALL_DIR")"
if [ -d "$INSTALL_DIR/.git" ]; then
  chown -R "$APP_USER": "$INSTALL_DIR"
  as_app git -C "$INSTALL_DIR" config credential.helper "$HELPER"
  as_app git -C "$INSTALL_DIR" fetch origin "$GITHUB_BRANCH"
  as_app git -C "$INSTALL_DIR" checkout "$GITHUB_BRANCH"
  as_app git -C "$INSTALL_DIR" merge --ff-only "origin/$GITHUB_BRANCH" \
    || die "local changes on the server block the update" "see them: git -C $INSTALL_DIR status ; drop them: git -C $INSTALL_DIR reset --hard origin/$GITHUB_BRANCH"
  ok "updated to $(as_app git -C "$INSTALL_DIR" log --oneline -1)"
else
  [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ] \
    && die "$INSTALL_DIR exists and is not a git copy" "move it away: sudo mv $INSTALL_DIR $INSTALL_DIR.old"
  install -d -o "$APP_USER" -g "$APP_USER" "$INSTALL_DIR"
  as_app git -c credential.helper= -c "credential.helper=$HELPER" clone --branch "$GITHUB_BRANCH" "$REPO_URL" "$INSTALL_DIR"
  as_app git -C "$INSTALL_DIR" config credential.helper "$HELPER"
  ok "cloned $(as_app git -C "$INSTALL_DIR" log --oneline -1)"
fi
MGR="$INSTALL_DIR/manager"

step 6 "Node packages + Chromium for test runs"
cd "$INSTALL_DIR"
if [ -f package-lock.json ]; then as_app npm ci --no-audit --no-fund; else as_app npm install --no-audit --no-fund; fi
npx --yes playwright install-deps chromium            # system libraries (root)
as_app npx playwright install chromium                # browser into ~$APP_USER/.cache/ms-playwright
ok "playwright $(as_app npx playwright --version)"

step 7 "Python virtual env"
[ -x "$MGR/.venv/bin/python" ] || as_app python3 -m venv "$MGR/.venv"
as_app "$MGR/.venv/bin/pip" install --upgrade pip -q
as_app "$MGR/.venv/bin/pip" install -r "$MGR/requirements.txt"
# "Verify code" (generated Playwright-Python test.py) runs in this same Python
PW_PY=$(sed -n "s/^const PW_VERSION = '\([0-9.]*\)'.*/\1/p" "$INSTALL_DIR/lib/codegen.js")
as_app "$MGR/.venv/bin/pip" install "playwright==${PW_PY:-1.55.0}" "python-dotenv>=1.0"
as_app "$MGR/.venv/bin/python" -m playwright install chromium
ok "$("$MGR/.venv/bin/python" --version), playwright-python ${PW_PY:-1.55.0}"

step 8 "Database ($DB_MODE)"
SSL_ARGS=()
if [ "$DB_MODE" = rds ]; then
  curl -fsSL -o "$RDS_CA" https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
  chmod 644 "$RDS_CA"
  SSL_ARGS=(--ssl-ca="$RDS_CA" --ssl-mode=VERIFY_IDENTITY)
  timeout 8 bash -c "</dev/tcp/$DB_HOST/$DB_PORT" 2>/dev/null \
    || die "cannot reach $DB_HOST:$DB_PORT" "RDS security group: inbound MySQL/Aurora 3306 from this EC2's security group (or 172.16.11.35/32); same VPC; endpoint spelled right"
  ok "RDS reachable"
  if [ -n "${RDS_MASTER_USER:-}" ]; then
    MCNF=$(mktemp); chmod 600 "$MCNF"
    printf '[client]\nuser=%s\npassword="%s"\nhost=%s\nport=%s\n' "$RDS_MASTER_USER" "$RDS_MASTER_PASSWORD" "$DB_HOST" "$DB_PORT" > "$MCNF"
    mysql --defaults-extra-file="$MCNF" "${SSL_ARGS[@]}" -e "SELECT 1" >/dev/null \
      || { rm -f "$MCNF"; die "RDS master login failed" "check master user/password (RDS -> Configuration -> Master username; Modify to reset the password)"; }
    mysql --defaults-extra-file="$MCNF" "${SSL_ARGS[@]}" <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS '$DB_USER'@'%' IDENTIFIED BY '$DB_PASSWORD';
ALTER USER '$DB_USER'@'%' IDENTIFIED BY '$DB_PASSWORD';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'%';
FLUSH PRIVILEGES;
SQL
    rm -f "$MCNF"
    ok "database $DB_NAME + user $DB_USER ready on RDS"
  else
    warn "no master user given: database $DB_NAME and user $DB_USER must already exist"
  fi
else
  systemctl enable --now mysql
  mysql <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASSWORD';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASSWORD';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
  ok "local MySQL: database $DB_NAME + user $DB_USER ready"
fi
ACNF=$(mktemp); chmod 600 "$ACNF"
printf '[client]\nuser=%s\npassword="%s"\nhost=%s\nport=%s\n' "$DB_USER" "$DB_PASSWORD" "$DB_HOST" "$DB_PORT" > "$ACNF"
mysql --defaults-extra-file="$ACNF" "${SSL_ARGS[@]}" -e "USE \`$DB_NAME\`; SELECT 1" >/dev/null \
  || { rm -f "$ACNF"; die "app user $DB_USER cannot log in to $DB_NAME" "wrong DB password, or the user/database was never created (give the RDS master user on the next run)"; }
rm -f "$ACNF"
ok "app user can log in"

step 9 "App config: manager/data/mysql.json"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$MGR/data"
DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" DB_USER="$DB_USER" DB_PASSWORD="$DB_PASSWORD" DB_NAME="$DB_NAME" \
SSL_CA=$( [ "$DB_MODE" = rds ] && echo "$RDS_CA" || true ) \
PM_DB_HOST="${PM_DB_HOST:-}" PM_DB_PORT="${PM_DB_PORT:-3306}" PM_DB_USER="${PM_DB_USER:-}" \
PM_DB_PASSWORD="${PM_DB_PASSWORD:-}" PM_DB_NAME="${PM_DB_NAME:-Unified_DB}" OUT="$MGR/data/mysql.json" \
python3 - <<'PY'
import json, os
e = os.environ
out = e['OUT']
cfg = json.load(open(out)) if os.path.exists(out) else {}
if not isinstance(cfg.get('default'), dict):
    cfg = {}
d = {'host': e['DB_HOST'], 'port': int(e['DB_PORT']), 'user': e['DB_USER'], 'password': e['DB_PASSWORD'], 'database': e['DB_NAME']}
if e.get('SSL_CA'):
    d['ssl_ca'] = e['SSL_CA']
cfg['default'] = d
if e.get('PM_DB_HOST'):
    u = cfg.get('unified') or {}
    pw = e['PM_DB_PASSWORD'] or u.get('password', '')
    u.update({'host': e['PM_DB_HOST'], 'port': int(e['PM_DB_PORT']), 'user': e['PM_DB_USER'], 'password': pw, 'database': e['PM_DB_NAME']})
    if e.get('SSL_CA'):
        u['ssl_ca'] = e['SSL_CA']
    cfg['unified'] = u
with open(out, 'w') as f:
    json.dump(cfg, f, indent=2)
PY
chown "$APP_USER": "$MGR/data/mysql.json"; chmod 600 "$MGR/data/mysql.json"
ok "written (mode 600)"

step 10 "Tables + admin user"
cd "$MGR"
as_app .venv/bin/python -c "import db; db.init(); print('  tables ok')" \
  || die "could not create tables" "see the Python error above (wrong DB details / no CREATE privilege)"
if [ -n "$ADMIN_PASSWORD" ]; then
  [ ${#ADMIN_PASSWORD} -ge 8 ] || die "admin password must be at least 8 characters" "run again"
  exists=$(as_app .venv/bin/python -c "import db,sys; c=db.connect(); print(1 if c.execute('SELECT 1 FROM users WHERE username=?', (sys.argv[1],)).fetchone() else 0)" "$ADMIN_USERNAME")
  if [ "$exists" = 1 ]; then
    warn "user $ADMIN_USERNAME already exists: password not changed (change it: cd $MGR && sudo -u $APP_USER .venv/bin/python manage.py set-password $ADMIN_USERNAME)"
  else
    printf '%s\n' "$ADMIN_PASSWORD" | as_app .venv/bin/python manage.py create-user "$ADMIN_USERNAME" "$ADMIN_NAME" --admin --password-stdin
    ok "admin $ADMIN_USERNAME created"
  fi
else
  warn "no admin password given: no user created"
fi

step 11 "Environment file $ENV_FILE"
BEHIND=1; SECURE=0
if [ "$SSL_MODE" != none ]; then SECURE=1; fi
install -m 640 -o root -g "$APP_USER" /dev/null "$ENV_FILE"
{
  echo "# LogiTestHub service settings (read by systemd). Change, then: sudo systemctl restart $SERVICE"
  echo "PORT=$APP_PORT"
  echo "HOST=127.0.0.1"
  echo "LTH_BEHIND_PROXY=$BEHIND"
  echo "LTH_SECURE_COOKIE=$SECURE"
  echo "PYTHONUNBUFFERED=1"
  if [ -n "$ANTHROPIC_API_KEY" ]; then echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"; fi
} > "$ENV_FILE"
ok "written (mode 640)"

step 12 "systemd service '$SERVICE'"
cat > /etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=LogiTestHub (test manager + runner)
After=network-online.target $( [ "$DB_MODE" = local ] && echo mysql.service )
Wants=network-online.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$MGR
EnvironmentFile=$ENV_FILE
ExecStart=$MGR/.venv/bin/python app.py
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable $SERVICE >/dev/null
systemctl restart $SERVICE
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$APP_PORT/login" || true)
  [ "$code" = 200 ] && break
  sleep 1
done
if [ "$code" != 200 ]; then
  journalctl -u $SERVICE -n 40 --no-pager
  die "app did not start (http://127.0.0.1:$APP_PORT/login -> $code)" "read the Python error just above; logs: journalctl -u $SERVICE -n 100"
fi
ok "app answers on 127.0.0.1:$APP_PORT"

step 13 "nginx (port 80 -> app)"
cat > /etc/nginx/sites-available/$SERVICE <<EOF
# LogiTestHub. HTTPS comes from certbot (edits this file) or the AWS load balancer in front.
map \$http_x_forwarded_proto \$lth_proto { default \$http_x_forwarded_proto; "" \$scheme; }
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN:-_};
    client_max_body_size 25m;            # app accepts 10 MB (screenshots from the CLI)
    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$lth_proto;
        proxy_read_timeout 600s;         # Test Assistant / Generate with AI can take minutes
        proxy_send_timeout 600s;
        proxy_buffering off;
    }
}
EOF
ln -sf /etc/nginx/sites-available/$SERVICE /etc/nginx/sites-enabled/$SERVICE
rm -f /etc/nginx/sites-enabled/default
nginx -t || die "nginx config test failed" "see the message above"
systemctl enable --now nginx >/dev/null; systemctl reload nginx
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: ${DOMAIN:-localhost}" http://127.0.0.1/login || true)
[ "$code" = 200 ] || die "nginx -> app gives HTTP $code" "check: sudo tail -n 30 /var/log/nginx/error.log"
ok "nginx serves the app on port 80"

step 14 "HTTPS ($SSL_MODE)"
case "$SSL_MODE" in
  certbot)
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --redirect --keep-until-expiring \
      || die "certbot could not get a certificate for $DOMAIN" "DNS A record of $DOMAIN must point to THIS server's public IP and port 80 must be open to the internet (security group). Private-subnet servers: use SSL_MODE=alb instead"
    ok "certificate installed, auto-renew: systemctl list-timers | grep certbot" ;;
  alb)
    ok "nginx listens on 80; the AWS load balancer does HTTPS (steps below)" ;;
  none)
    ok "plain http (no certificate)" ;;
esac

# ------------------------------------------------------------------------------------------------
echo
echo "${c_grn}==== Setup finished ====${c_off}"
echo "  Code:      $INSTALL_DIR  ($(as_app git -C "$INSTALL_DIR" log --oneline -1))"
echo "  Service:   sudo systemctl status $SERVICE   |  logs: journalctl -u $SERVICE -f"
echo "  Config:    $ENV_FILE, $MGR/data/mysql.json"
echo "  KEEP SAFE: $MGR/data/env.key (decrypts environment passwords) and secret.key - back them up"
case "$SSL_MODE" in
  certbot) echo "  Open:      https://$DOMAIN" ;;
  alb)     echo "  Next:      AWS load balancer -> target group HTTP:80 -> this instance; health check path /login (200)"
           echo "             HTTPS:443 listener with an ACM certificate for $DOMAIN; HTTP:80 listener redirects to 443"
           echo "             DNS: CNAME $DOMAIN -> the load balancer's DNS name"
           echo "  Open:      https://$DOMAIN" ;;
  none)    echo "  Open:      http://<server-ip>/  (from your PC through an SSH tunnel: localhost:8080 -> $(hostname -I | awk '{print $1}'):80)" ;;
esac
echo "  Update later: run this script again (Enter keeps every answer)."
