import os
import re
import logging
import httpx
from telegram import (
    Update,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup as Markup,
    ReplyParameters,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)

# ---------- تنظیمات (از Environment Variables خوانده می‌شود) ----------
TOKEN = os.environ["BOT_TOKEN"]
OWNER = int(os.environ["OWNER_ID"])
CHANNEL = os.environ["CHANNEL_ID"]  # مثل @mychannel یا -100123...
if CHANNEL.lstrip("-").isdigit():
    CHANNEL = int(CHANNEL)
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
BASE_URL = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", "8080"))
PERSONA = os.getenv("BOT_PERSONA", "")  # اختیاری: توضیح درباره خودت/کسب‌وکارت

REPLY_SYS = (
    "تو دستیار شخصی صاحب این ربات هستی و پیش‌نویس پاسخ به پیام‌های مردم را می‌نویسی. "
    "به زبانی که فرستنده نوشته (پیش‌فرض فارسی) مودبانه، دقیق و مختصر جواب بده. "
    "اگر مطمئن نیستی حدس نزن و صادقانه بگو. " + PERSONA
)
POST_SYS = (
    "تو نویسنده‌ی محتوای کانال تلگرام هستی. از موضوع یا متن کاربر یک پست جذاب، روان و "
    "خوانا به فارسی بنویس. از ایموجی مناسب اما نه زیاد استفاده کن. فقط متن نهایی پست را بده. "
    + PERSONA
)

HDR_REPLY = "📩 پیام از "
SEP_Q = "\n\n❓ "
SEP_A = "\n\n💡 پیشنهاد پاسخ:\n"
HDR_POST = "📢 پیش‌نویس کانال"

KB_REPLY = Markup(
    [
        [Btn("✅ ارسال", callback_data="send"), Btn("🔄 بازنویسی", callback_data="regen")],
        [Btn("❌ رد", callback_data="cancel")],
    ]
)
KB_POST = Markup(
    [
        [Btn("📢 انتشار", callback_data="pub"), Btn("🔄 بازنویسی", callback_data="regen")],
        [Btn("❌ لغو", callback_data="cancel")],
    ]
)
KB_RAW = Markup(
    [
        [Btn("📢 انتشار همین", callback_data="pubcopy"), Btn("🤖 بهبود با AI", callback_data="improve")],
        [Btn("❌ لغو", callback_data="cancel")],
    ]
)
KB_RAW_MEDIA = Markup(
    [[Btn("📢 انتشار همین", callback_data="pubcopy"), Btn("❌ لغو", callback_data="cancel")]]
)


# ---------- هوش مصنوعی (Gemini) ----------
async def ai(system: str, prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=body, headers={"x-goog-api-key": GEMINI_KEY})
        r.raise_for_status()
        data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


async def safe_ai(system: str, prompt: str) -> str:
    try:
        return await ai(system, prompt)
    except Exception as e:  # noqa
        logging.exception("AI error: %s", e)
        return "⚠️ خطا در تولید پاسخ. خودت روی این پیام ریپلای بزن و پاسخ رو بنویس."


# ---------- ساخت متن پیش‌نویس‌ها ----------
def reply_draft(header: str, q: str, a: str) -> str:
    return f"{header}{SEP_Q}{q[:1000]}{SEP_A}{a[:2500]}"


def post_draft(topic: str, text: str) -> str:
    topic = topic.replace("\n", " ")[:500]
    return f"{HDR_POST}\n🎯 {topic}\n\n{text[:3500]}"


def parse_uid(text: str):
    m = re.search(r"\[(\d+)\]\n\n❓", text)
    return int(m.group(1)) if m else None


# ---------- دستورات ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == OWNER:
        await update.message.reply_text(
            "سلام! 👋 راهنما:\n\n"
            "• هر کسی به ربات پیام بده، من براش پیش‌نویس پاسخ می‌سازم و اول برای تو می‌فرستم؛ "
            "با تایید تو برای اون شخص ارسال می‌شه. (می‌تونی روی پیش‌نویس ریپلای بزنی و متن خودت رو بفرستی)\n"
            "• /post موضوع  ← ساخت پست کانال با AI\n"
            "• هر متن/عکس/ویدیویی بفرستی، می‌تونی مستقیم تو کانال منتشرش کنی."
        )
    else:
        await update.message.reply_text("سلام! 👋 سوالت رو بنویس، به‌زودی پاسخ می‌دم.")


async def post_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(ctx.args).strip()
    if not topic:
        await update.message.reply_text("مثال:\n/post معرفی فواید مطالعه روزانه")
        return
    text = await safe_ai(POST_SYS, topic)
    await update.message.reply_text(post_draft(topic, text), reply_markup=KB_POST)


# ---------- پیام کاربران عادی ----------
async def user_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    name = u.full_name + (f" @{u.username}" if u.username else "")
    question = update.message.text
    await update.message.reply_text("پیامت رسید ✅ به‌زودی پاسخ می‌دم.")
    answer = await safe_ai(REPLY_SYS, question)
    header = f"{HDR_REPLY}{name} [{u.id}]"
    await ctx.bot.send_message(OWNER, reply_draft(header, question, answer), reply_markup=KB_REPLY)


# ---------- پیام‌های خود مالک ----------
async def owner_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    rt = msg.reply_to_message
    # ریپلای روی پیش‌نویس = ارسال متن دلخواه خودت به اون شخص
    if rt and (rt.text or "").startswith(HDR_REPLY) and msg.text:
        uid = parse_uid(rt.text)
        if uid:
            await ctx.bot.send_message(uid, msg.text)
            await msg.reply_text("✅ پاسخ تو برای اون شخص ارسال شد.")
            return
    kb = KB_RAW if msg.text else KB_RAW_MEDIA
    await ctx.bot.send_message(
        OWNER,
        "این محتوا رو تو کانال منتشر کنم؟",
        reply_markup=kb,
        reply_parameters=ReplyParameters(message_id=msg.message_id),
    )


# ---------- دکمه‌ها ----------
async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != OWNER:
        await q.answer()
        return
    await q.answer()
    m, t, d = q.message, q.message.text or "", q.data

    if d == "cancel":
        await m.edit_text("❌ لغو شد.")

    elif d == "send":
        uid = parse_uid(t)
        answer = t.split(SEP_A, 1)[1]
        await ctx.bot.send_message(uid, answer)
        await m.edit_text(t + "\n\n✅ ارسال شد.")

    elif d == "regen" and t.startswith(HDR_REPLY):
        header = t.split(SEP_Q, 1)[0]
        question = t.split(SEP_Q, 1)[1].split(SEP_A, 1)[0]
        answer = await safe_ai(REPLY_SYS, question)
        await m.edit_text(reply_draft(header, question, answer), reply_markup=KB_REPLY)

    elif d == "regen" and t.startswith(HDR_POST):
        topic = t.split("\n")[1].replace("🎯", "", 1).strip()
        text = await safe_ai(POST_SYS, topic)
        await m.edit_text(post_draft(topic, text), reply_markup=KB_POST)

    elif d == "pub":
        body = t.split("\n\n", 1)[1]
        await ctx.bot.send_message(CHANNEL, body)
        await m.edit_text(t + "\n\n✅ منتشر شد.")

    elif d == "pubcopy":
        src = m.reply_to_message
        if not src:
            await m.edit_text("⚠️ پیام اصلی پیدا نشد. دوباره بفرست.")
            return
        await ctx.bot.copy_message(CHANNEL, OWNER, src.message_id)
        await m.edit_text("✅ منتشر شد.")

    elif d == "improve":
        src = m.reply_to_message
        raw = (src.text or src.caption or "") if src else ""
        if not raw:
            await m.edit_text("⚠️ متنی برای بهبود پیدا نشد.")
            return
        text = await safe_ai(POST_SYS, "این متن را بهتر و جذاب‌تر کن:\n" + raw)
        await m.edit_text(post_draft(raw, text), reply_markup=KB_POST)


def main():
    app = Application.builder().token(TOKEN).build()
    owner = filters.User(OWNER)
    private = filters.ChatType.PRIVATE

    app.add_handler(CommandHandler("start", start, filters=private))
    app.add_handler(CommandHandler("post", post_cmd, filters=owner))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(private & owner & ~filters.COMMAND, owner_msg))
    app.add_handler(MessageHandler(private & ~owner & ~filters.COMMAND & filters.TEXT, user_msg))

    if BASE_URL:  # روی Render: حالت webhook
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{BASE_URL.rstrip('/')}/{TOKEN}",
            drop_pending_updates=False,
        )
    else:  # روی کامپیوتر خودت: حالت polling
        app.run_polling()


if __name__ == "__main__":
    main()
