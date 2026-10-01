"""AI budget: credits ledger, monthly limits per user and for the whole organisation.

Every AI call goes through this server (the Anthropic key never leaves it), so the
server can refuse a call before it is made. Limits are in rupees (what admins think
in); spend is tracked in USD from token counts (pricing.py) and converted at
pricing.usd_inr() (the rate admins entered with their credit purchases). All figures are estimates; Anthropic's Console is the billed truth.

Checks happen BEFORE a call. A single run can overshoot a little (its AI steps are
already underway when the limit is crossed); the next call is then refused.
"""
from datetime import date

import pricing

S_USER_DEFAULT = 'ai_user_monthly_inr'   # default monthly limit per user (blank = no limit)
S_ORG = 'ai_org_monthly_inr'             # whole-organisation monthly limit (blank = no limit)
WARN_AT = 0.8


def inr(v):
    """1234567.5 -> '₹12,34,567.50' (Indian grouping, like the web pages)."""
    whole, frac = f'{abs(v):.2f}'.split('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        whole = ','.join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1]) + ',' + tail
    return ('-' if v < 0 else '') + f'₹{whole}.{frac}'


def get_setting(con, name):
    r = con.execute('SELECT value FROM settings WHERE name=?', (name,)).fetchone()
    return r['value'] if r else None


def set_setting(con, name, value):
    con.execute('INSERT INTO settings(name, value) VALUES (?,?) ON DUPLICATE KEY UPDATE value=VALUES(value)', (name, value))


def _num(v):
    try:
        f = float(v)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


def month_bounds(today=None):
    d = today or date.today()
    start = d.replace(day=1)
    nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1))
    return start.isoformat(), nxt.isoformat()


def _spent_inr(con, where='', params=()):
    start, end = month_bounds()
    r = con.execute(f'SELECT COALESCE(SUM(cost_usd), 0) AS c FROM ai_usage WHERE at >= ? AND at < ? {where}',
                    (start, end, *params)).fetchone()
    return float(r['c'] or 0) * pricing.usd_inr(con)


def user_limit_inr(con, user_id):
    """(limit, source) -> per-user override, else the default; None limit = unlimited."""
    r = con.execute('SELECT monthly_inr FROM ai_limits WHERE user_id=?', (user_id,)).fetchone()
    if r and r['monthly_inr'] is not None:
        return float(r['monthly_inr']), 'user'
    return _num(get_setting(con, S_USER_DEFAULT)), 'default'


def this_month():
    return date.today().strftime('%Y-%m')


def topups(con, user_id, month=None):
    """Extra budget the admin gave this user for one month (gone next month)."""
    return con.execute('SELECT * FROM ai_topups WHERE user_id=? AND month=? ORDER BY id',
                       (user_id, month or this_month())).fetchall()


def status(con, user):
    """Budget state for one user this month. user = row with id, username.
    limit_inr = base limit (own or default) + this month's extra; None = no limit."""
    start, end = month_bounds()
    used = _spent_inr(con, 'AND username=?', (user['username'],))
    base, source = user_limit_inr(con, user['id'])
    extras = topups(con, user['id'])
    extra = sum(float(t['amount_inr']) for t in extras)
    limit = None if base is None else base + extra
    org_used = _spent_inr(con)
    org_limit = _num(get_setting(con, S_ORG))
    if org_limit is not None:
        org_limit += extras_this_month(con)   # extras come out of the reserve, so they raise the team's ceiling too
    s = {'used_inr': used, 'limit_inr': limit, 'base_limit_inr': base, 'extra_inr': extra, 'extras': extras,
         'limit_source': source, 'org_used_inr': org_used,
         'org_limit_inr': org_limit, 'resets_on': end, 'month_start': start, 'ok': True, 'warn': False, 'message': ''}
    if limit is not None and used >= limit:
        s.update(ok=False, message=(f'AI limit reached: {inr(used)} of {inr(limit)} used this month. '
                                    f'Ask your LogiTestHub admin to raise the limit. Resets on {end}.'))
    elif org_limit is not None and org_used >= org_limit:
        s.update(ok=False, message=(f'Organisation AI limit reached: {inr(org_used)} of {inr(org_limit)} used this month. '
                                    f'Ask your LogiTestHub admin. Resets on {end}.'))
    elif limit is not None and used >= WARN_AT * limit:
        s.update(warn=True, message=f'You have used {inr(used)} of your {inr(limit)} AI limit this month.')
    s['pct'] = min(100, round(used / limit * 100)) if limit else None
    return s


def extras_this_month(con):
    r = con.execute('SELECT COALESCE(SUM(amount_inr), 0) AS s FROM ai_topups WHERE month=?', (this_month(),)).fetchone()
    return float(r['s'] or 0)


def plan(con):
    """Credits -> organisation limit -> default per user -> reserve (kept aside for extras).

    reserve = credit balance - what is still promised to the team this month
            (organisation limit + extras given, minus what is already spent this month).
    None values mean "not set"; reserve is None until credits are recorded."""
    cr = credits(con)
    balance_inr = cr['remaining_usd'] * pricing.usd_inr(con) if cr['entries'] else None
    org = _num(get_setting(con, S_ORG))
    default = _num(get_setting(con, S_USER_DEFAULT))
    extras = extras_this_month(con)
    org_used = _spent_inr(con)
    promised_left = max(0.0, (org or 0) + extras - org_used)   # still spendable by the team this month
    n_users = con.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    n_default = con.execute('SELECT COUNT(*) FROM users u LEFT JOIN ai_limits l ON l.user_id=u.id '
                            'WHERE l.user_id IS NULL OR l.monthly_inr IS NULL').fetchone()[0]
    return {'credits': cr, 'balance_inr': balance_inr, 'org_inr': org, 'default_inr': default, 'extras_inr': extras,
            'org_used_inr': org_used, 'org_effective_inr': None if org is None else org + extras,
            'promised_left_inr': promised_left, 'n_users': n_users, 'n_default_users': n_default,
            'default_total_inr': None if default is None else default * n_default,
            'reserve_inr': None if balance_inr is None else balance_inr - promised_left}


def credits(con):
    """Credits added by admins vs estimated spend since the first credit entry."""
    rows = con.execute('SELECT * FROM ai_credits ORDER BY at DESC, id DESC').fetchall()
    added = sum(float(r['amount_usd']) for r in rows)
    since = min((r['at'] for r in rows), default=None)
    used = 0.0
    if since:
        used = float(con.execute('SELECT COALESCE(SUM(cost_usd), 0) AS c FROM ai_usage WHERE at >= ?', (since,)).fetchone()['c'] or 0)
    return {'entries': rows, 'added_usd': added, 'used_usd': used, 'remaining_usd': added - used, 'since': since}
