"""
Padmart — user-facing booking bot.
No payment is collected — this only reserves a slot.

Flow:
  /start or /book
    -> choose hall
    -> choose date (next N days)
    -> choose duration
    -> choose start time (only free slots shown)
    -> confirm name & phone (remembered after first booking)
    -> confirm -> reservation saved

  /myreservations  -> list + cancel buttons (shows 48h cancellation policy)
  /cancel <id>      -> cancel directly by id

Run:
    export PADMART_USER_BOT_TOKEN="123:abc..."
    python user_bot.py
"""

import logging
from datetime import date, timedelta

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ConversationHandler, filters,
)

import db
from config import (
    USER_BOT_TOKEN, HALLS, SLOT_STARTS, SLOT_END_CAP, MAX_DURATION,
    DAYS_AHEAD, MAX_ACTIVE_PER_USER, fmt_price, ADMIN_IDS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("padmart.user_bot")

# ── Conversation states ──
CHOOSE_HALL, CHOOSE_DATE, CHOOSE_DUR, CHOOSE_START, ASK_NAME, ASK_PHONE, CONFIRM = range(7)


def slot_label(start: int, dur: int) -> str:
    return f"{start:02d}:00–{start+dur:02d}:00"


# ─────────────────────────────────────────────
#  /start /book
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Padmart Acting School booking bot.\n\n"
        "/book — reserve a hall\n"
        "/myreservations — view & cancel your bookings\n\n"
        "ℹ️ This only reserves your slot — no payment is taken here.\n"
        "Cancellation policy: free if cancelled 48h+ before your start time, "
        "otherwise the full amount applies."
    )


async def cmd_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if db.count_active_for_user(user_id) >= MAX_ACTIVE_PER_USER:
        await update.message.reply_text(
            f"⚠ You already have {MAX_ACTIVE_PER_USER} active bookings — "
            "that's the limit. Cancel one with /myreservations to book another."
        )
        return ConversationHandler.END

    kb = [
        [InlineKeyboardButton(f"{h['icon']} {h['name_en']} — {fmt_price(h['price'])}/hr", callback_data=f"hall:{k}")]
        for k, h in HALLS.items()
    ]
    await update.message.reply_text("🎭 Choose a hall:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_HALL


async def on_hall_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    hall_key = q.data.split(":", 1)[1]
    context.user_data["hall_key"] = hall_key

    today = date.today()
    kb = []
    row = []
    for i in range(DAYS_AHEAD):
        d = today + timedelta(days=i)
        label = d.strftime("%a %d %b")
        row.append(InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    hall = HALLS[hall_key]
    await q.edit_message_text(
        f"{hall['icon']} {hall['name_en']} selected.\n📅 Choose a date:",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return CHOOSE_DATE


async def on_date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date_str = q.data.split(":", 1)[1]
    context.user_data["date_str"] = date_str

    kb = [
        [InlineKeyboardButton(f"{n}h", callback_data=f"dur:{n}") for n in range(1, MAX_DURATION + 1)]
    ]
    await q.edit_message_text(
        f"📅 {date_str} selected.\n⏱ Choose duration:",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return CHOOSE_DUR


async def on_duration_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    dur = int(q.data.split(":", 1)[1])
    context.user_data["duration"] = dur

    hall_key  = context.user_data["hall_key"]
    date_str  = context.user_data["date_str"]
    starts = db.available_starts(hall_key, date_str, dur, SLOT_STARTS, SLOT_END_CAP)

    if not starts:
        await q.edit_message_text(
            "😕 No free slots for that hall/date/duration. Try /book again with a different option."
        )
        return ConversationHandler.END

    kb, row = [], []
    for s in starts:
        row.append(InlineKeyboardButton(slot_label(s, dur), callback_data=f"start:{s}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    await q.edit_message_text(
        f"⏱ {dur}h selected.\n🕐 Choose start time:",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return CHOOSE_START


async def on_start_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    start_hour = int(q.data.split(":", 1)[1])
    context.user_data["start_hour"] = start_hour

    # double-check it's still free (race condition guard)
    hall_key = context.user_data["hall_key"]
    date_str = context.user_data["date_str"]
    dur      = context.user_data["duration"]
    if not db.can_book(hall_key, date_str, start_hour, dur):
        await q.edit_message_text("⚠ That slot was just taken. Please /book again.")
        return ConversationHandler.END

    profile = db.get_profile(update.effective_user.id)
    if profile and profile.get("full_name") and profile.get("phone"):
        context.user_data["full_name"] = profile["full_name"]
        context.user_data["phone"] = profile["phone"]
        return await show_confirmation(update, context, use_query=True)

    await q.edit_message_text("🕐 Slot selected.\n\n👤 What's your full name?")
    return ASK_NAME


async def on_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["full_name"] = update.message.text.strip()
    await update.message.reply_text("📞 What's your phone number?")
    return ASK_PHONE


async def on_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    db.upsert_profile(
        update.effective_user.id,
        context.user_data["full_name"],
        context.user_data["phone"],
    )
    return await show_confirmation(update, context, use_query=False)


async def show_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, use_query: bool):
    d = context.user_data
    hall = HALLS[d["hall_key"]]
    total = hall["price"] * d["duration"]
    sl = slot_label(d["start_hour"], d["duration"])

    text = (
        "📋 *Confirm your booking*\n\n"
        f"{hall['icon']} Hall: {hall['name_en']}\n"
        f"📅 Date: {d['date_str']}\n"
        f"🕐 Time: {sl}\n"
        f"👤 Name: {d['full_name']}\n"
        f"📞 Phone: {d['phone']}\n"
        f"💰 Total: {fmt_price(total)}\n\n"
        "_No payment is collected here — this only reserves the slot._\n"
        "_Cancel 48h+ before start for free; less than 48h incurs the full charge._"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm:yes"),
         InlineKeyboardButton("❌ Cancel", callback_data="confirm:no")]
    ])

    if use_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    return CONFIRM


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "confirm:no":
        await q.edit_message_text("❌ Booking cancelled. Use /book to start again.")
        context.user_data.clear()
        return ConversationHandler.END

    d = context.user_data
    hall = HALLS[d["hall_key"]]
    total = hall["price"] * d["duration"]

    if not db.can_book(d["hall_key"], d["date_str"], d["start_hour"], d["duration"]):
        await q.edit_message_text("⚠ Sorry, that slot was just taken. Please /book again.")
        context.user_data.clear()
        return ConversationHandler.END

    user = update.effective_user
    res_id = db.create_reservation(
        tg_user_id=user.id,
        tg_username=user.username or "",
        full_name=d["full_name"],
        phone=d["phone"],
        hall_key=d["hall_key"],
        date_str=d["date_str"],
        start_hour=d["start_hour"],
        duration=d["duration"],
        price=total,
    )

    sl = slot_label(d["start_hour"], d["duration"])
    await q.edit_message_text(
        f"🎉 Booking confirmed!\n\n"
        f"🆔 {res_id}\n"
        f"{hall['icon']} {hall['name_en']} · {d['date_str']} · {sl}\n"
        f"💰 {fmt_price(total)}\n\n"
        "Use /myreservations to view or cancel this booking."
    )

    # notify admins
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"🆕 New booking {res_id}\n"
                f"{hall['icon']} {hall['name_en']} · {d['date_str']} · {sl}\n"
                f"👤 {d['full_name']} · 📞 {d['phone']}\n"
                f"💰 {fmt_price(total)}",
            )
        except Exception as e:
            log.warning("Could not notify admin %s: %s", admin_id, e)

    context.user_data.clear()
    return ConversationHandler.END


async def cmd_cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Booking flow cancelled.")
    return ConversationHandler.END


# ─────────────────────────────────────────────
#  /myreservations
# ─────────────────────────────────────────────
async def cmd_my_reservations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reservations = db.list_user_reservations(user_id, only_active=True)

    if not reservations:
        await update.message.reply_text("You have no active reservations.")
        return

    for r in reservations:
        hall = HALLS[r["hall_key"]]
        sl = slot_label(r["start_hour"], r["duration"])
        fee, is_free = db.cancellation_fee(r)
        note = "✅ Free cancellation (48h+ left)" if is_free else "⚠ Late cancel — full charge applies"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Cancel this booking", callback_data=f"cancelres:{r['id']}")
        ]])
        await update.message.reply_text(
            f"🆔 {r['id']}\n"
            f"{hall['icon']} {hall['name_en']} · {r['date_str']} · {sl}\n"
            f"💰 {fmt_price(r['price'])}\n"
            f"{note}",
            reply_markup=kb,
        )


async def on_cancel_reservation_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    res_id = q.data.split(":", 1)[1]
    res = db.get_reservation(res_id)

    if not res or res["tg_user_id"] != update.effective_user.id:
        await q.edit_message_text("⚠ Reservation not found.")
        return
    if res["status"] != "active":
        await q.edit_message_text("This reservation is already cancelled.")
        return

    fee, is_free = db.cancellation_fee(res)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⚠ Confirm cancel (full charge)" if not is_free else "✅ Confirm cancel (free)",
            callback_data=f"reallycancel:{res_id}"
        ),
        InlineKeyboardButton("Keep it", callback_data="keepit"),
    ]])
    msg = (
        f"You're about to cancel {res_id}.\n"
        + ("This is free — more than 48h before start." if is_free
           else f"⚠ Less than 48h before start — full amount ({fmt_price(fee)}) applies.")
    )
    await q.edit_message_text(msg, reply_markup=kb)


async def on_really_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "keepit":
        await q.edit_message_text("Kept — no changes made.")
        return
    res_id = q.data.split(":", 1)[1]
    ok = db.cancel_reservation(res_id)
    if ok:
        await q.edit_message_text(f"✅ {res_id} cancelled.")
        res = db.get_reservation(res_id)
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, f"❌ Booking cancelled: {res_id}")
            except Exception:
                pass
    else:
        await q.edit_message_text("⚠ Could not cancel (already cancelled?).")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    db.init_db()
    if not USER_BOT_TOKEN:
        raise SystemExit("Set PADMART_USER_BOT_TOKEN env var before running.")

    app = Application.builder().token(USER_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("book", cmd_book)],
        states={
            CHOOSE_HALL:  [CallbackQueryHandler(on_hall_chosen, pattern=r"^hall:")],
            CHOOSE_DATE:  [CallbackQueryHandler(on_date_chosen, pattern=r"^date:")],
            CHOOSE_DUR:   [CallbackQueryHandler(on_duration_chosen, pattern=r"^dur:")],
            CHOOSE_START: [CallbackQueryHandler(on_start_chosen, pattern=r"^start:")],
            ASK_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, on_name_received)],
            ASK_PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, on_phone_received)],
            CONFIRM:      [CallbackQueryHandler(on_confirm, pattern=r"^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel_conv)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("myreservations", cmd_my_reservations))
    app.add_handler(CallbackQueryHandler(on_cancel_reservation_btn, pattern=r"^cancelres:"))
    app.add_handler(CallbackQueryHandler(on_really_cancel, pattern=r"^reallycancel:|^keepit$"))

    log.info("User bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
