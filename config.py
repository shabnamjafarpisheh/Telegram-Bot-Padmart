"""
Shared configuration for Padmart booking bots.
Mirrors the Streamlit app: halls, pricing, slot hours, cancellation rule.
"""

import os

# ── Bot tokens (set as environment variables, never hardcode) ──
USER_BOT_TOKEN  = os.environ.get("PADMART_USER_BOT_TOKEN", "")
ADMIN_BOT_TOKEN = os.environ.get("PADMART_ADMIN_BOT_TOKEN", "")

# ── Admin Telegram user IDs allowed to use the admin bot ──
# Get your numeric ID by messaging @userinfobot on Telegram.
# Set as a comma-separated env var, e.g. PADMART_ADMIN_IDS="123456789,987654321"
_admin_ids_env = os.environ.get("PADMART_ADMIN_IDS", "").strip()
ADMIN_IDS = [int(x) for x in _admin_ids_env.split(",") if x.strip().isdigit()]

# ── Database ──
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "padmart.db")

# ── Halls (mirrors HALLS dict in the Streamlit app) ──
HALLS = {
    "grand":   {"name_en": "Grand Studio", "name_fa": "پلاتو بزرگ",
                "price": 70000, "icon": "🎪"},
    "studio2": {"name_en": "Studio 2",     "name_fa": "پلاتو ۲",
                "price": 70000, "icon": "🎭"},
}

# ── Booking hours: 09:00 -> 20:00 last start (1h slots, stackable) ──
SLOT_STARTS  = list(range(9, 21))   # 09..20
SLOT_END_CAP = 21                   # last possible end hour
MAX_DURATION = 6                    # hours
DAYS_AHEAD   = 14                   # how far into the future users can book

# ── Cancellation policy: free if cancelled 48h+ before start ──
CANCEL_FREE_HOURS = 48

# ── Max active bookings per user (matches Streamlit app) ──
MAX_ACTIVE_PER_USER = 5

CURRENCY_SUFFIX = "تومان"


def fmt_price(n: int) -> str:
    return f"{n:,} {CURRENCY_SUFFIX}"
