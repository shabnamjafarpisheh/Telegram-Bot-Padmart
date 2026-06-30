"""
Padmart — admin bot.
Separate bot/chat for the owner to see and manage current bookings.

Commands:
    /start        — menu
    /today        — bookings for today
    /week         — bookings in the next 7 days
    /all          — all upcoming active bookings
    /hall grand   — bookings for a specific hall (grand | studio2)
    /find 0912... — search bookings by phone or name

Each booking is shown with a "Cancel" button.

Run:
    export PADMART_ADMIN_BOT_TOKEN="123:abc..."
    python admin_bot.py
"""

import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)

import db
from config import ADMIN_BOT_TOKEN, ADMIN_IDS, HALLS, fmt_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("padmart.admin_bot")


def is_admin(update: Update) -> bool:
    if not ADMIN_IDS:
        # No allowlist configured — warn but allow (so first-time setup isn't locked out).
        log.warning("ADMIN_IDS is empty in config.py — admin bot is currently OPEN to anyone.")
        return True
    return update.effective_user.id in ADMIN_IDS


def slot_label(start: int, dur: int) -> str:
    return f"{start:02d}:00–{start+dur:02d}:00"


def fmt_reservation(r: dict) -> str:
    hall = HALLS[r["hall_key"]]
    sl = slot_label(r["start_hour"], r["duration"])
    uname = f"@{r['tg_username']}" if r.get("tg_username") else "(no username)"
    return (
        f"🆔 {r['id']}\n"
        f"{hall['icon']} {hall['name_en']} · {r['date_str']} · {sl}\n"
        f"👤 {r['full_name']} · 📞 {r['phone']} · {uname}\n"
        f"💰 {fmt_price(r['price'])}"
    )


async def send_reservation_list(update_or_msg, reservations, empty_text="No bookings found."):
    if not reservations:
        await update_or_msg.reply_text(empty_text)
        return
    for r in reservations:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Cancel", callback_data=f"acancel:{r['id']}")
        ]])
        await update_or_msg.reply_text(fmt_reservation(r), reply_markup=kb)


# ─────────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Not authorized.")
        return
    await update.message.reply_text(
        "👑 Padmart Admin Bot\n\n"
        "/today — bookings for today\n"
        "/week — bookings in the next 7 days\n"
        "/all — all upcoming active bookings\n"
        "/hall grand|studio2 — bookings for one hall\n"
        "/find <text> — search by phone or name\n"
        "/stats — quick revenue summary"
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    today_str = datetime.now().strftime("%Y-%m-%d")
    res = db.list_all_reservations(only_active=True, date_str=today_str)
    await update.message.reply_text(f"📅 Today ({today_str}) — {len(res)} booking(s):")
    await send_reservation_list(update.message, res)


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    res = db.upcoming_active(days_ahead=7)
    await update.message.reply_text(f"📅 Next 7 days — {len(res)} booking(s):")
    await send_reservation_list(update.message, res)


async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    res = db.upcoming_active(days_ahead=60)
    await update.message.reply_text(f"📋 All upcoming bookings — {len(res)} total:")
    await send_reservation_list(update.message, res)


async def cmd_hall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /hall grand  or  /hall studio2\n"
            f"Available: {', '.join(HALLS.keys())}"
        )
        return
    hall_key = context.args[0].strip().lower()
    if hall_key not in HALLS:
        await update.message.reply_text(f"Unknown hall '{hall_key}'. Available: {', '.join(HALLS.keys())}")
        return
    res = db.list_all_reservations(only_active=True, hall_key=hall_key)
    hall = HALLS[hall_key]
    await update.message.reply_text(f"{hall['icon']} {hall['name_en']} — {len(res)} booking(s):")
    await send_reservation_list(update.message, res)


async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /find <name or phone>")
        return
    query = " ".join(context.args).strip().lower()
    all_res = db.list_all_reservations(only_active=True)
    matches = [
        r for r in all_res
        if query in (r["full_name"] or "").lower() or query in (r["phone"] or "")
    ]
    await update.message.reply_text(f"🔍 Found {len(matches)} matching booking(s):")
    await send_reservation_list(update.message, matches)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    active = db.list_all_reservations(only_active=True)
    total_revenue = sum(r["price"] for r in active)
    per_hall = {}
    for r in active:
        per_hall[r["hall_key"]] = per_hall.get(r["hall_key"], 0) + r["price"]

    lines = [
        "📊 *Quick Stats*",
        f"Active bookings: {len(active)}",
        f"Total expected revenue: {fmt_price(total_revenue)}",
        "",
    ]
    for hk, amt in per_hall.items():
        hall = HALLS[hk]
        lines.append(f"{hall['icon']} {hall['name_en']}: {fmt_price(amt)}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
#  CANCEL BUTTON
# ─────────────────────────────────────────────
async def on_admin_cancel_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if ADMIN_IDS and q.from_user.id not in ADMIN_IDS:
        await q.answer("Not authorized.", show_alert=True)
        return
    await q.answer()

    res_id = q.data.split(":", 1)[1]
    res = db.get_reservation(res_id)
    if not res:
        await q.edit_message_text("⚠ Reservation not found.")
        return
    if res["status"] != "active":
        await q.edit_message_text("Already cancelled.")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠ Confirm cancel", callback_data=f"areallycancel:{res_id}"),
        InlineKeyboardButton("Keep", callback_data="akeep"),
    ]])
    await q.edit_message_text(
        fmt_reservation(res) + "\n\n⚠ Cancel this booking?",
        reply_markup=kb,
    )


async def on_admin_really_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if ADMIN_IDS and q.from_user.id not in ADMIN_IDS:
        await q.answer("Not authorized.", show_alert=True)
        return
    await q.answer()

    if q.data == "akeep":
        await q.edit_message_text("Kept — no changes made.")
        return

    res_id = q.data.split(":", 1)[1]
    ok = db.cancel_reservation(res_id)
    if ok:
        await q.edit_message_text(f"✅ {res_id} cancelled by admin.")
    else:
        await q.edit_message_text("⚠ Could not cancel.")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    db.init_db()
    if not ADMIN_BOT_TOKEN:
        raise SystemExit("Set PADMART_ADMIN_BOT_TOKEN env var before running.")

    app = Application.builder().token(ADMIN_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("hall", cmd_hall))
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(on_admin_cancel_btn, pattern=r"^acancel:"))
    app.add_handler(CallbackQueryHandler(on_admin_really_cancel, pattern=r"^areallycancel:|^akeep$"))

    log.info("Admin bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
