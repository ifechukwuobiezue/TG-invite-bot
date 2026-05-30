# TG-invite-bot

To get the id of a picture add

async def cmd_getfileid(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id not in ADMIN_IDS:
return
with open(FLYER_PATH, "rb") as f:
msg = await update.message.reply_photo(photo=f)
file_id = msg.photo[-1].file_id
await update.message.reply_text(f"`{file_id}`", parse_mode="Markdown")
