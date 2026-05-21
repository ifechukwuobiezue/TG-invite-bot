import os
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

load_dotenv()

BOT_TOKEN     = os.getenv("BOT_TOKEN")
CHANNEL_ID    = int(os.getenv("CHANNEL_ID"))
ADMIN_IDS     = [int(x) for x in os.getenv("ADMIN_IDS").split(",")]
FLYER_FILE_ID = os.getenv("FLYER_FILE_ID")
db            = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO)

# Tracks which user an admin is currently approving or denying
ADMIN_STATE = {}  # {admin_id: {"action": "approve"/"deny", "user_id": int, "username": str}}

# Hardcoded packages — change here anytime, no Supabase needed
PACKAGES = [
    {"name": "1 Min",    "duration_days": 0,   "duration_minutes": 1},
    {"name": "1 Month",  "duration_days": 30},
    {"name": "3 Months", "duration_days": 90},
    {"name": "1 Year",   "duration_days": 365},
]


# ── Scheduled: kick expired members ──────────────────────────────────────────

async def job_kick_expired(context):
    now     = datetime.now(timezone.utc).isoformat()
    expired = db.table("members").select("user_id, username").lte("expiry", now).eq("removed", False).execute().data

    for m in expired:
        try:
            await context.bot.ban_chat_member(CHANNEL_ID, m["user_id"])
            await context.bot.unban_chat_member(CHANNEL_ID, m["user_id"])
            db.table("members").update({"removed": True}).eq("user_id", m["user_id"]).execute()
            for admin in ADMIN_IDS:
                await context.bot.send_message(admin,
                    f"🚪 *Evicted:* {m['username'] or m['user_id']} — subscription expired.",
                    parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Kick failed for {m['user_id']}: {e}")


# ── Scheduled: send reminder DMs ─────────────────────────────────────────────

async def job_reminders(context):
    now = datetime.now(timezone.utc)

    for days in [3, 1]:
        start   = (now + timedelta(days=days, hours=-1)).isoformat()
        end     = (now + timedelta(days=days, hours=1)).isoformat()
        members = db.table("members").select("user_id, username, expiry").gte("expiry", start).lte("expiry", end).eq("removed", False).execute().data

        for m in members:
            expiry = datetime.fromisoformat(m["expiry"]).strftime("%Y-%m-%d")
            try:
                await context.bot.send_message(m["user_id"],
                    f"⏰ *Heads up!* Your subscription expires in *{days} day{'s' if days > 1 else ''}* ({expiry}).\n\n"
                    f"Renew now → contact @AthenasHub 🙏",
                    parse_mode="Markdown")
            except:
                for admin in ADMIN_IDS:
                    await context.bot.send_message(admin,
                        f"⚠️ Couldn't DM {m['username'] or m['user_id']} — sub expires in {days}d.",
                        parse_mode="Markdown")


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *Athena's Hub*!\n\n"
        "🔷 *Payment:* Use /pay to see pricing and payment details.\n"
        "🔷 *Renew Subscription:* Use /renew to stay active. 🔄\n\n"
        "❓ Got questions? Reach out to @AthenasHub",
        parse_mode="Markdown")


# ── /pay ──────────────────────────────────────────────────────────────────────

async def cmd_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = (
        "💳 *Payment Details*\n\n"
        "🏦 *Bank:* Kuda Bank\n"
        "💰 *Account Number:* `2003661688`\n"
        "👤 *Account Name:* Paul-Mary Chukwuka Omile\n\n"
        "After payment kindly send your receipt here.\n\n"
        "_For non-Nigerians, kindly DM @AthenasHub for a different payment method._"
    )
    await update.message.reply_photo(photo=FLYER_FILE_ID, caption=caption, parse_mode="Markdown")


# ── /renew ────────────────────────────────────────────────────────────────────

async def cmd_renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔄 To renew, make your payment and send the receipt *right here*.\n\n"
        "Admin will review it and your access will be extended once approved ✅\n\n"
        "Need payment details? Use /pay",
        parse_mode="Markdown")


# ── User sends receipt ────────────────────────────────────────────────────────

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = f"@{user.username}" if user.username else user.first_name

    await update.message.reply_text(
        "✅ *Receipt received!*\n\nOur team will review shortly. You'll get your invite link here once approved.",
        parse_mode="Markdown")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{user.id}:{name}"),
        InlineKeyboardButton("❌ Deny",    callback_data=f"deny:{user.id}:{name}")
    ]])

    for admin in ADMIN_IDS:
        await context.bot.forward_message(admin, update.effective_chat.id, update.message.message_id)
        await context.bot.send_message(admin,
            f"👋 Hey chief! {name} just sent a payment receipt.\nReceipt is above 👆",
            parse_mode="Markdown",
            reply_markup=keyboard)


# ── Admin taps Approve → show package buttons ─────────────────────────────────

async def callback_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return

    parts   = query.data.split(":")
    user_id = int(parts[1])
    name    = parts[2]

    ADMIN_STATE[query.from_user.id] = {"action": "approve", "user_id": user_id, "username": name}

    keyboard = [[InlineKeyboardButton(
        f"{p['name']} ({p['duration_days']}d)",
        callback_data=f"pkg:{user_id}:{i}:{name}"
    )] for i, p in enumerate(PACKAGES)]

    await query.edit_message_text(
        f"Select the package for {name}:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard))


# ── Admin picks package → save + send invite ──────────────────────────────────

async def callback_pkg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return

    parts   = query.data.split(":")
    user_id = int(parts[1])
    pkg_idx = int(parts[2])
    name    = parts[3]

    ADMIN_STATE.pop(query.from_user.id, None)

    pkg  = PACKAGES[pkg_idx]
    now  = datetime.now(timezone.utc)

    # Support minute-based packages for testing
    if pkg.get("duration_minutes"):
        delta = timedelta(minutes=pkg["duration_minutes"])
    else:
        delta = timedelta(days=pkg["duration_days"])

    expiry = now + delta

    existing = db.table("members").select("expiry").eq("user_id", user_id).execute().data
    if existing:
        base   = datetime.fromisoformat(existing[0]["expiry"])
        expiry = (base if base > now else now) + delta
        db.table("members").update({
            "expiry": expiry.isoformat(), "package": pkg["name"], "removed": False,
            "username": name
        }).eq("user_id", user_id).execute()
    else:
        db.table("members").insert({
            "user_id": user_id, "username": name, "package": pkg["name"],
            "expiry": expiry.isoformat(), "added_at": now.isoformat(), "removed": False
        }).execute()

    try:
        link = (await context.bot.create_chat_invite_link(
            CHANNEL_ID, member_limit=1, name=f"user_{user_id}"
        )).invite_link

        await context.bot.send_message(user_id,
            f"🎉 *Payment Approved!*\n\n"
            f"Package: *{pkg['name'].capitalize()}* ({pkg['duration_days']} days)\n"
            f"Expires: `{expiry.strftime('%Y-%m-%d')}`\n\n"
            f"Your one-time invite link — works once, just for you:\n{link}\n\n"
            f"Welcome to Athena's Hub! 🙌",
            parse_mode="Markdown")

        await query.edit_message_text(
            f"✅ {name} approved on *{pkg['name']}*. Invite link sent.",
            parse_mode="Markdown")

    except Exception as e:
        await query.edit_message_text(
            f"✅ Saved but couldn't DM {name}:\n`{e}`",
            parse_mode="Markdown")


# ── Admin taps Deny → ask for reason ─────────────────────────────────────────

async def callback_deny(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return

    parts   = query.data.split(":")
    user_id = int(parts[1])
    name    = parts[2]

    ADMIN_STATE[query.from_user.id] = {"action": "deny", "user_id": user_id, "username": name}

    await query.edit_message_text(
        f"Type your reason for denying {name} and send it.\n"
        f"The bot will forward it to them.",
        parse_mode="Markdown")


# ── Catch all text — handles deny reason OR wrong input ──────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id

    # If this admin is waiting to give a deny reason, handle it
    if admin_id in ADMIN_STATE and ADMIN_STATE[admin_id]["action"] == "deny":
        state   = ADMIN_STATE.pop(admin_id)
        user_id = state["user_id"]
        name    = state["username"]
        reason  = update.message.text

        try:
            await context.bot.send_message(user_id,
                f"❌ *Payment Not Approved*\n\nReason: _{reason}_\n\n_For any issues please contact @AthenasHub for help_",
                parse_mode="Markdown")
            await update.message.reply_text(f"Done. Reason sent to {name}.")
        except:
            await update.message.reply_text(f"Couldn't DM {name} — they may not have started the bot.")
        return

    # Everyone else gets the wrong input message
    await update.message.reply_text(
        "⚠️ We only accept a *screenshot* or *PDF* as payment proof.\n\n"
        "Go to your bank app, take a screenshot of the successful transaction, and send it here.",
        parse_mode="Markdown")


# ── Admin commands ────────────────────────────────────────────────────────────

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    now     = datetime.now(timezone.utc).isoformat()
    members = db.table("members").select("user_id, username, expiry, package").gt("expiry", now).eq("removed", False).order("expiry").execute().data

    if not members:
        return await update.message.reply_text("No active members.")

    lines = ["*Active Members:*\n"]
    for m in members:
        days_left = (datetime.fromisoformat(m["expiry"]) - datetime.now(timezone.utc)).days
        pkg       = m.get("package") or "?"
        name      = m["username"] or str(m["user_id"])
        lines.append(f"• {name} — {pkg} ({days_left}d left)")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or not context.args:
        return
    user_id = int(context.args[0])
    await context.bot.ban_chat_member(CHANNEL_ID, user_id)
    await context.bot.unban_chat_member(CHANNEL_ID, user_id)
    db.table("members").update({"removed": True}).eq("user_id", user_id).execute()
    await update.message.reply_text(f"✅ `{user_id}` removed.", parse_mode="Markdown")


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await job_kick_expired(context)
    await update.message.reply_text("✅ Expiry check done.")


# ── Boot ──────────────────────────────────────────────────────────────────────

app = Application.builder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start",  cmd_start))
app.add_handler(CommandHandler("pay",    cmd_pay))
app.add_handler(CommandHandler("renew",  cmd_renew))
app.add_handler(CommandHandler("list",   cmd_list))
app.add_handler(CommandHandler("remove", cmd_remove))
app.add_handler(CommandHandler("check",  cmd_check))

app.add_handler(MessageHandler(
    (filters.PHOTO | filters.Document.PDF) & filters.ChatType.PRIVATE,
    handle_receipt
))

app.add_handler(MessageHandler(
    filters.ChatType.PRIVATE & ~filters.COMMAND,
    handle_text
))

app.add_handler(CallbackQueryHandler(callback_approve, pattern=r"^approve:"))
app.add_handler(CallbackQueryHandler(callback_deny,    pattern=r"^deny:"))
app.add_handler(CallbackQueryHandler(callback_pkg,     pattern=r"^pkg:"))

app.job_queue.run_repeating(job_kick_expired, interval=60, first=10)
app.job_queue.run_repeating(job_reminders,    interval=3600, first=120)

print("🤖 Bot running...")
app.run_polling()