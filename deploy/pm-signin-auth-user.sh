#!/usr/bin/env bash
# Point an installed LogiTestHub's PM sign-in at the table PM itself signs people in with:
# Logimax.auth_user (a view over ticketing_system_live.auth_user) - current passwords, login by username or email.
# Unified_DB.members keeps only a copy of the password made when the member was created, so most PM logins failed.
#
#   bash pm-signin-auth-user.sh staging     # /var/www/logitesthub/staging, service logitesthub-staging
#   bash pm-signin-auth-user.sh prod        # /var/www/logitesthub/prod,    service logitesthub
#
# Needs: lth_readonly may SELECT (id, username, email, password, first_name, last_name, is_active) ON Logimax.auth_user.
# PM accounts that already signed in (linked by members.member_id) are re-linked to auth_user.id, so nobody gets a
# second LogiTestHub account. Safe to run twice: a site already on Logimax.auth_user is left as it is.
set -euo pipefail

case "${1:-}" in
  staging) APP=/var/www/logitesthub/staging/manager; SVC=logitesthub-staging; HOST=lthuat.logimaxindia.com ;;
  prod)    APP=/var/www/logitesthub/prod/manager;    SVC=logitesthub;         HOST=lth.logimaxindia.com ;;
  *) echo "usage: bash $0 staging|prod"; exit 1 ;;
esac
[ -f "$APP/data/mysql.json" ] || { echo "no $APP/data/mysql.json - is the $1 site installed?"; exit 1; }
PORT=$(sudo sed -n 's/^PORT=//p' "/etc/$SVC/$SVC.env")
cd "$APP"
echo "== $1: $APP"

sudo -u ubuntu .venv/bin/python - <<'PY'
import json, db, pm_auth
cfg = json.load(open(db.DB_CONFIG))
u = cfg.get('unified') or {}
if not u.get('host'):
    raise SystemExit('PM sign-in is not set up on this site (no "unified" in mysql.json) - add it first')
if u.get('database') == 'Logimax' and (u.get('members') or {}).get('table') == 'auth_user':
    print('already on Logimax.auth_user - nothing to change')
    raise SystemExit(0)
# 1. re-link PM accounts made with the old mapping: members.member_id -> auth_user.id (= members.user_id)
c = pm_auth._connect()
mp = {r['member_id']: r['user_id'] for r in
      c.execute('SELECT member_id, user_id FROM Unified_DB.members WHERE user_id IS NOT NULL').fetchall()}
c.close()
lc = db.connect()
with lc:
    rows = lc.execute("SELECT id, username, pm_member_id FROM users WHERE auth_source='pm'").fetchall()
    for r in rows:
        new = mp.get(r['pm_member_id'])
        if new is not None:
            lc.execute('UPDATE users SET pm_member_id=? WHERE id=?', (str(new), r['id']))
            print('  re-linked PM account:', r['username'])
        else:
            print('  left as is:', r['username'])
    print('  PM accounts on this site:', len(rows))
lc.close()
# 2. sign in where PM signs in
u['database'] = 'Logimax'
u['members'] = {'table': 'auth_user', 'id_column': 'id', 'login_columns': ['username', 'email'],
                'password_column': 'password', 'name_column': 'first_name', 'last_name_column': 'last_name',
                'active_column': 'is_active'}
cfg['unified'] = u
with open(db.DB_CONFIG, 'w') as f:
    json.dump(cfg, f, indent=2)
print('  mysql.json: PM sign-in -> Logimax.auth_user (username or email)')
PY

sudo systemctl restart "$SVC"
for i in $(seq 1 20); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/login")" = 200 ] && break
  sleep 1
done
sudo -u ubuntu .venv/bin/python - <<'PY'
import pm_auth
cfg = pm_auth.config()
c = pm_auth._connect()
n = c.execute(f"SELECT COUNT(*) AS n, SUM({cfg['active_column']}) AS a FROM {cfg['table']}").fetchone()
c.close()
print(f"== PM sign-in: {pm_auth.enabled()} | {cfg['table']} | login by {cfg['login_columns']} | {n['n']} users, {n['a']} active")
PY
curl -s -o /dev/null -w "== web https://$HOST -> app: %{http_code}\n" -H "Host: $HOST" http://127.0.0.1/login
