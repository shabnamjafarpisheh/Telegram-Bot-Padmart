# Padmart Telegram Booking Bots

Two bots, one shared SQLite database:

- **`user_bot.py`** — guests book a hall/date/time. No payment is collected,
  just a reservation. Mirrors your Streamlit app's halls, pricing, slot hours,
  and 48-hour cancellation policy.
- **`admin_bot.py`** — you (the owner) check what's booked, search bookings,
  see quick revenue stats, and cancel bookings — all from a separate Telegram
  chat/bot.

## 1. Create the two bots

Message **@BotFather** on Telegram:

```
/newbot   -> name it e.g. "Padmart Booking"   -> get a token (user bot)
/newbot   -> name it e.g. "Padmart Admin"     -> get a token (admin bot)
```

## 2. Find your Telegram numeric ID

Message **@userinfobot** — it replies with your ID. Put it in
`config.py` under `ADMIN_IDS`:

```python
ADMIN_IDS = [123456789]
```

This restricts the admin bot to you only. If you leave it empty, the
admin bot is open to anyone who finds it — not recommended once you're
not actively testing.

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Set your bot tokens and run

```bash
export PADMART_USER_BOT_TOKEN="111111:AAA..."
export PADMART_ADMIN_BOT_TOKEN="222222:BBB..."

python user_bot.py     # in one terminal / process
python admin_bot.py    # in another terminal / process
```

Both processes read/write the same `data/padmart.db` SQLite file, so a
booking made through the user bot is immediately visible to the admin bot.

## User bot commands

- `/start` — intro
- `/book` — start a reservation (hall → date → duration → time → name/phone → confirm)
- `/myreservations` — list your active bookings, cancel with one tap
  (shows whether cancellation is free or full-charge based on the 48h rule)

## Admin bot commands

- `/start` — menu
- `/today` — bookings for today
- `/week` — bookings in the next 7 days
- `/all` — all upcoming active bookings
- `/hall grand` or `/hall studio2` — bookings for one hall
- `/find <name or phone>` — search
- `/stats` — active booking count + expected revenue, split by hall

Every listed booking has an inline **🗑 Cancel** button with a
confirm step.

## Notes / things to adjust for your setup

- **Halls & pricing** live in `config.py` (`HALLS` dict) — keep this in
  sync with your Streamlit app if both run side by side, or eventually
  point both at the same database.
- **Running 24/7**: for production, run both scripts under something like
  `systemd`, `pm2`, Docker, or a process manager — `app.run_polling()` is
  fine for getting started but needs a supervisor to survive crashes/restarts.
  Polling is simplest; switching to webhooks is an option later if you need
  faster delivery or are deploying behind a public HTTPS endpoint.
- **Single instance**: SQLite is fine as long as only one process writes
  at a time, which is the case here (each bot is a separate process, but
  SQLite handles that concurrency fine for this volume).
- **No payment integration**: by design, per your request — this only
  reserves a slot. If you ever want to add Telegram Payments or a manual
  "mark as paid" admin action, that's a small additive change to the
  `reservations` table (e.g. a `paid` column) and an admin command.
