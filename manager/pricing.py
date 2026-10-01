"""Claude API prices used to estimate AI spend (USD per million tokens).

Anthropic list prices as of 2026-09 (Claude API skill reference). Check
https://www.anthropic.com/pricing before relying on the numbers for billing;
the Anthropic Console's Usage page is the source of truth for what was charged.
"""
import os

# model -> (input, output, cache read, cache write 5-min)
PRICES = {
    'claude-opus-5-5': (4.00, 20.00, 0.20, 5.00),
    'claude-sonnet-5-5': (2.00, 10.00, 0.20, 2.50),
    'claude-haiku-4-5': (1.00, 5.00, 0.10, 1.25),
    'claude-fable-5-1': (10.00, 50.00, 0.25, 12.50),
}
DEFAULT_MODEL = 'claude-opus-5-5'

# Fallback ₹ per $ used only until an admin records a credit purchase with its rate (AI Usage -> Credits).
USD_INR = float(os.environ.get('LTH_USD_INR', '88'))


def usd_inr(con):
    """₹ per $ actually paid: average of the credit purchases weighted by dollars
    (e.g. $20 @ ₹87.10 and $50 @ ₹88.40 -> ₹88.03). Falls back to USD_INR before any purchase is recorded."""
    r = con.execute('SELECT SUM(amount_usd * rate_inr) AS inr, SUM(amount_usd) AS usd FROM ai_credits '
                    'WHERE rate_inr IS NOT NULL AND rate_inr > 0').fetchone()
    return round(float(r['inr']) / float(r['usd']), 4) if r and r['usd'] else USD_INR


def cost_usd(model, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    p = PRICES.get(model or '', PRICES[DEFAULT_MODEL])   # unknown model -> priced as the default (Opus 5.5)
    return (input_tokens * p[0] + output_tokens * p[1] + cache_read * p[2] + cache_write * p[3]) / 1_000_000
