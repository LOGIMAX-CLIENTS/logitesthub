"""Sign in with the PM (Project Management) account: identity + active status come from PM Production.

Rules (do not relax them):
  * Read-only. Only SELECT on the Members table, through a dedicated read-only DB user, and the session is
    also put in READ ONLY mode so a mistake here cannot write to PM Production.
  * The PM password is only checked, never copied: LogiTestHub stores no PM password or PM hash.
  * The password check uses the PM application's own mechanism. PM is a Django app, so its hashes are
    Django hashes ('pbkdf2_sha256$<iter>$<salt>$<hash>', 'argon2$...', 'bcrypt_sha256$...') and we verify
    them with Django's own check_password(), never by decrypting anything.
  * LogiTestHub roles/permissions stay in LogiTestHub (users.role); PM only says who you are and whether
    you are still active.

Uses the second database connection "unified" (Unified_DB) from data/mysql.json, next to "default"
(LogiTestHub's own DB). PM sign-in is off until "unified" is filled in. The Members column mapping sits in
the same entry under "members" (defaults below); confirm every name against the PM Members model:

  "unified": {
    "host": "pm-logimax-prod-db.....rds.amazonaws.com", "port": 3306,
    "user": "lth_readonly", "password": "...", "database": "Unified_DB",
    "members": {"table": "Members", "id_column": "id", "login_columns": ["username", "email"],
                "password_column": "password", "name_column": "first_name", "last_name_column": "last_name",
                "active_column": "is_active", "deleted_column": null}
  }
"""
import json
import os
import re
import threading

import pymysql

import db

DB_NAME = 'unified'   # the named connection in data/mysql.json
IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')   # column/table names come from config: whitelist them
_django_ready = threading.Lock()
_django_done = False


class PMUnavailable(Exception):
    """PM DB not reachable / misconfigured: the caller falls back to local accounts only."""


def config():
    """Members mapping of the 'unified' connection, or None while that connection is not set up."""
    if not db.configured(DB_NAME):
        return None
    with open(db.DB_CONFIG, encoding='utf-8') as f:
        raw = json.load(f)
    cfg = dict(((raw.get(DB_NAME) or {}) if isinstance(raw.get('default'), dict) else {}).get('members') or {})
    cfg.setdefault('table', 'Members')
    cfg.setdefault('id_column', 'id')
    cfg.setdefault('login_columns', ['username', 'email'])
    cfg.setdefault('password_column', 'password')
    cfg.setdefault('active_column', 'is_active')
    names = [cfg['table'], cfg['id_column'], cfg['password_column'], *cfg['login_columns']]
    names += [cfg[k] for k in ('name_column', 'last_name_column', 'active_column', 'deleted_column') if cfg.get(k)]
    bad = [n for n in names if not IDENT.match(str(n))]
    if bad:
        raise PMUnavailable(f'mysql.json "unified.members" has invalid table/column names: {bad}')
    return cfg


def enabled():
    try:
        return config() is not None
    except Exception:
        return False


def _connect():
    try:
        return db.connect(DB_NAME, read_only=True)
    except pymysql.MySQLError as e:
        raise PMUnavailable(f'Unified_DB not reachable ({e.args[0] if e.args else e})')


def _select(cfg, where_sql, params):
    cols = [cfg['id_column'], cfg['password_column'], *cfg['login_columns']]
    cols += [cfg[k] for k in ('name_column', 'last_name_column', 'active_column', 'deleted_column') if cfg.get(k)]
    sql = f"SELECT {', '.join('`' + c + '`' for c in dict.fromkeys(cols))} FROM `{cfg['table']}` WHERE {where_sql} LIMIT 2"
    c = _connect()
    try:
        return c.execute(sql, params).fetchall()
    except pymysql.MySQLError as e:
        raise PMUnavailable(f'Unified_DB Members lookup failed ({e.args[0] if e.args else e})')
    finally:
        c.close()


def _is_active(cfg, m):
    if cfg.get('active_column'):
        v = m.get(cfg['active_column'])
        if isinstance(v, (bytes, bytearray)):   # BIT(1)
            v = int.from_bytes(v, 'big')
        if isinstance(v, str):
            v = v.strip().lower() in ('1', 'true', 'yes', 'active', 'y')
        if not v:
            return False
    if cfg.get('deleted_column'):
        d = m.get(cfg['deleted_column'])
        if isinstance(d, (bytes, bytearray)):
            d = int.from_bytes(d, 'big')
        if d not in (None, 0, '0', False, ''):   # deleted flag set or a deleted_at date
            return False
    return True


def _check_django_password(raw_password, encoded):
    """PM's own verification: Django's check_password (no setter, so it never re-hashes / writes back)."""
    global _django_done
    if not encoded or encoded.startswith('!'):   # Django 'unusable password'
        return False
    with _django_ready:
        if not _django_done:
            from django.conf import settings
            if not settings.configured:
                settings.configure(USE_TZ=True)   # Django's default PASSWORD_HASHERS, same as a stock PM install
            _django_done = True
    from django.contrib.auth.hashers import check_password
    try:
        return check_password(raw_password, encoded)
    except Exception:   # unknown algorithm / hasher library not installed
        return False


def _display_name(cfg, m, login):
    parts = [m.get(cfg['name_column']) if cfg.get('name_column') else None,
             m.get(cfg['last_name_column']) if cfg.get('last_name_column') else None]
    name = ' '.join(str(p).strip() for p in parts if p and str(p).strip())
    return (name or login)[:120]


def authenticate(login, password):
    """-> {'pm_id', 'name', 'login', 'username'} when the PM credentials are right and the member is active,
    'inactive' when right but the member is inactive/deleted, None when wrong.
    Raises PMUnavailable when PM cannot be asked."""
    cfg = config()
    if cfg is None:
        raise PMUnavailable('PM sign-in is not configured')
    login = (login or '').strip()
    if not login or not password or len(login) > 254:
        return None
    where = ' OR '.join(f'`{c}` = ?' for c in cfg['login_columns'])
    rows = _select(cfg, f'({where})', [login] * len(cfg['login_columns']))
    if len(rows) != 1:   # unknown, or ambiguous (same text is one member's username and another's email)
        return None
    m = rows[0]
    if not _check_django_password(password, m.get(cfg['password_column']) or ''):
        return None
    if not _is_active(cfg, m):
        return 'inactive'
    first_login_col = cfg['login_columns'][0]
    return {'pm_id': str(m[cfg['id_column']]), 'name': _display_name(cfg, m, login),
            'username': str(m.get(first_login_col) or login)}


def still_active(pm_id):
    """True / False from PM; None when PM cannot be asked (the caller then keeps the session)."""
    try:
        cfg = config()
        if cfg is None:
            return None
        rows = _select(cfg, f"`{cfg['id_column']}` = ?", [pm_id])
    except PMUnavailable:
        return None
    return bool(rows) and _is_active(cfg, rows[0])


_active_cache = {}   # pm_id -> (checked_at, ok); for CLI access keys, which have no browser session


def still_active_cached(pm_id, max_age=600):
    """still_active() asked at most every max_age seconds per member. PM unreachable -> allowed (None)."""
    import time
    now = time.time()
    hit = _active_cache.get(pm_id)
    if hit and now - hit[0] < max_age:
        return hit[1]
    ok = still_active(pm_id)
    if ok is not None:
        _active_cache[pm_id] = (now, ok)
    return ok
