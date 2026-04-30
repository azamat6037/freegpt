"""
Uzbek Student AI Bot
A Telegram bot that gives Uzbek students free AI access via Groq.

Features:
- Three modes: Chat, Homework helper, Translator
- Persistent chat history per user (SQLite)
- Daily rate limiting (100 messages/day per user)
- Uzbek Latin interface
"""

import os
import asyncio
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ────────── Config ──────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", 8080))

DAILY_LIMIT = 100
HISTORY_TURNS = 10  # remember last N user/assistant pairs
DB_PATH = os.environ.get("DB_PATH", "/tmp/bot.db")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]  # fast model first

# ────────── System prompts (Uzbek Latin) ──────────
PROMPTS = {
    "chat": (
        "Sen o'zbek talabalar uchun do'stona AI yordamchisan. "
        "Aniq, foydali va qisqa javob ber. "
        "Foydalanuvchi boshqa tilda yozmasa, har doim o'zbek tilida (lotin yozuvida) javob ber. "
        "Murakkab mavzularni oddiy misollar bilan tushuntir."
    ),
    "homework": (
        "Sen tajribali o'qituvchisan. Matematika, fizika, kimyo, biologiya, "
        "tarix, geografiya va boshqa fanlar bo'yicha o'quvchilarga yordam berasan. "
        "Har bir muammoni qadamma-qadam yech va har bir qadamni tushuntir. "
        "Formulalarni va tushunchalarni aniq ko'rsat. "
        "O'quvchining o'zi o'rganishi uchun javobni shoshilmasdan, batafsil yoz. "
        "O'zbek tilida (lotin yozuvida) javob ber."
    ),
    "translate": (
        "Sen O'zbekcha-Inglizcha professional tarjimon san. "
        "Foydalanuvchi yuborgan matnning tilini avtomatik aniqla. "
        "Agar matn o'zbek tilida bo'lsa, to'g'ri inglizchaga tarjima qil. "
        "Agar matn ingliz tilida bo'lsa, o'zbekchaga (lotin yozuvida) tarjima qil. "
        "Faqat tarjimani yoz, izoh yoki qo'shimcha matn yozma. "
        "Idiomalar va madaniy iboralar uchun eng tabiiy tarjimani tanla."
    ),
}

MODE_NAMES = {
    "chat": "💬 Suhbat",
    "homework": "📚 Uy vazifasi",
    "translate": "🌐 Tarjima",
}

MODE_DESCRIPTIONS = {
    "chat": "Umumiy suhbat — istalgan savol bering",
    "homework": "Uy vazifasi — qadamma-qadam tushuntirish",
    "translate": "Tarjima — o'zbekcha ↔ inglizcha",
}

# ────────── Database ──────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                mode TEXT DEFAULT 'chat',
                history TEXT DEFAULT '[]',
                daily_count INTEGER DEFAULT 0,
                last_reset_date TEXT,
                first_seen TEXT
            )
        """)
        conn.commit()


def get_user(user_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            today = str(date.today())
            conn.execute(
                "INSERT INTO users (user_id, last_reset_date, first_seen) VALUES (?, ?, ?)",
                (user_id, today, today),
            )
            conn.commit()
            return {
                "user_id": user_id,
                "mode": "chat",
                "history": [],
                "daily_count": 0,
                "last_reset_date": today,
            }
        return {
            "user_id": row["user_id"],
            "mode": row["mode"],
            "history": json.loads(row["history"] or "[]"),
            "daily_count": row["daily_count"],
            "last_reset_date": row["last_reset_date"],
        }


def save_user(user_id: int, **fields):
    if not fields:
        return
    if "history" in fields:
        fields["history"] = json.dumps(fields["history"], ensure_ascii=False)
    with get_db() as conn:
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE users SET {cols} WHERE user_id = ?",
            list(fields.values()) + [user_id],
        )
        conn.commit()


def get_quota_status(user_id: int) -> tuple[bool, int]:
    """Returns (allowed, remaining_now) without consuming quota."""
    today = str(date.today())
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, last_reset_date, first_seen) VALUES (?, ?, ?)",
            (user_id, today, today),
        )
        row = conn.execute(
            "SELECT daily_count, last_reset_date FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row["last_reset_date"] != today:
            conn.execute(
                "UPDATE users SET daily_count = 0, last_reset_date = ? WHERE user_id = ?",
                (today, user_id),
            )
            daily_count = 0
        else:
            daily_count = row["daily_count"]
        conn.commit()
    if daily_count >= DAILY_LIMIT:
        return False, 0
    return True, DAILY_LIMIT - daily_count


def consume_quota(user_id: int) -> tuple[bool, int]:
    """Atomically consume one quota unit. Returns (consumed, remaining_after)."""
    today = str(date.today())
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, last_reset_date, first_seen) VALUES (?, ?, ?)",
            (user_id, today, today),
        )
        row = conn.execute(
            "SELECT daily_count, last_reset_date FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        daily_count = row["daily_count"]
        if row["last_reset_date"] != today:
            daily_count = 0
            conn.execute(
                "UPDATE users SET daily_count = 0, last_reset_date = ? WHERE user_id = ?",
                (today, user_id),
            )

        if daily_count >= DAILY_LIMIT:
            conn.commit()
            return False, 0

        new_count = daily_count + 1
        conn.execute(
            "UPDATE users SET daily_count = ? WHERE user_id = ?",
            (new_count, user_id),
        )
        conn.commit()
        return True, DAILY_LIMIT - new_count


# ────────── AI calls ──────────
async def call_groq(messages: list) -> str:
    """Call Groq with model fallback."""
    last_err = None
    async with httpx.AsyncClient(timeout=25.0) as client:
        for model in GROQ_MODELS:
            try:
                resp = await client.post(
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": 1500,
                        "temperature": 0.7,
                    },
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logging.warning(f"{model} returned {resp.status_code}, trying next")
            except Exception as e:
                last_err = str(e)
                logging.warning(f"{model} failed: {e}, trying next")
    raise RuntimeError(last_err or "All models failed")


# ────────── Telegram handlers ──────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    get_user(user_id)  # ensure record exists
    text = (
        "Salom! Men talabalar uchun bepul AI yordamchiman. 🤖\n\n"
        "Men sizga yordam bera olaman:\n"
        "📚 *Uy vazifasi* — matematika, fizika, kimyo, biologiya, tarix\n"
        "🌐 *Tarjima* — o'zbekcha ↔ inglizcha\n"
        "💬 *Suhbat* — istalgan savol\n\n"
        "*Buyruqlar:*\n"
        "/rejim — rejimni o'zgartirish\n"
        "/yangi — yangi suhbat boshlash\n"
        "/limit — bugungi qolgan savollar\n"
        "/yordam — yordam\n\n"
        f"Kuniga {DAILY_LIMIT} ta savol berishingiz mumkin.\n\n"
        "*Hozirgi rejim:* 💬 Suhbat\n"
        "Savol berishni boshlang!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Yordam*\n\n"
        "Bu bot Uzbekistondagi talabalar uchun bepul AI yordamchidir.\n\n"
        "*Buyruqlar:*\n"
        "/start — botni qayta boshlash\n"
        "/rejim — Suhbat / Uy vazifasi / Tarjima rejimini tanlash\n"
        "/yangi — suhbat tarixini tozalash\n"
        "/limit — bugungi qolgan savollarni ko'rish\n"
        "/yordam — bu yordam\n\n"
        "*Maslahatlar:*\n"
        "• Savollarni aniq va to'liq yozing\n"
        "• Uy vazifasi rejimida qadamma-qadam tushuntirish olasiz\n"
        "• Tarjima rejimida shunchaki matn yuboring — avtomatik tarjima qiladi\n"
        "• Yangi mavzu boshlashda /yangi buyrug'ini ishlating\n\n"
        f"📊 Kuniga {DAILY_LIMIT} ta savol berish mumkin.\n"
        "📜 Oxirgi 10 ta xabar eslab qolinadi."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_user(user_id, history=[])
    await update.message.reply_text(
        "✨ Yangi suhbat boshlandi.\n"
        "Eski tarix tozalandi. Yangi savol bering!"
    )


async def cmd_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    today = str(date.today())
    used = 0 if user["last_reset_date"] != today else user["daily_count"]
    remaining = DAILY_LIMIT - used
    bar_filled = int((used / DAILY_LIMIT) * 10)
    bar = "▓" * bar_filled + "░" * (10 - bar_filled)
    text = (
        f"📊 *Kunlik chegara*\n\n"
        f"`{bar}`  {used}/{DAILY_LIMIT}\n\n"
        f"Qolgan: *{remaining}* ta savol\n\n"
        f"_Chegara har kuni yarim tunda yangilanadi._"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    keyboard = [
        [InlineKeyboardButton(MODE_NAMES["chat"], callback_data="mode_chat")],
        [InlineKeyboardButton(MODE_NAMES["homework"], callback_data="mode_homework")],
        [InlineKeyboardButton(MODE_NAMES["translate"], callback_data="mode_translate")],
    ]
    text = (
        f"*Rejimni tanlang:*\n\n"
        f"💬 *Suhbat* — umumiy savollar\n"
        f"📚 *Uy vazifasi* — qadamma-qadam tushuntirish\n"
        f"🌐 *Tarjima* — o'zbekcha ↔ inglizcha\n\n"
        f"_Hozirgi rejim:_ {MODE_NAMES[user['mode']]}"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def on_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = query.data.replace("mode_", "")
    if mode not in PROMPTS:
        return
    user_id = query.from_user.id
    save_user(user_id, mode=mode, history=[])  # clear history on mode switch
    await query.edit_message_text(
        f"✅ Rejim o'zgartirildi: *{MODE_NAMES[mode]}*\n\n"
        f"_{MODE_DESCRIPTIONS[mode]}_\n\n"
        f"Suhbat tarixi tozalandi. Savol bering!",
        parse_mode="Markdown",
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text:
        return

    # Rate limit check (without consuming quota)
    allowed, remaining = get_quota_status(user_id)
    if not allowed:
        await update.message.reply_text(
            f"⛔ *Kunlik chegaraga yetdingiz* ({DAILY_LIMIT} ta savol)\n\n"
            f"Iltimos ertaga qaytib keling. Chegara yarim tunda yangilanadi.",
            parse_mode="Markdown",
        )
        return

    user = get_user(user_id)
    history = user["history"]
    mode = user["mode"]

    # Build messages for AI
    messages = [{"role": "system", "content": PROMPTS[mode]}]
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    # Show typing indicator
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    try:
        reply = await call_groq(messages)
    except Exception:
        logging.exception("AI call failed")
        await update.message.reply_text(
            "❌ Kechirasiz, AI hozir mavjud emas.\n"
            "Birozdan keyin qayta urinib ko'ring."
        )
        return

    # Consume quota only after a successful AI response.
    consumed, remaining = consume_quota(user_id)
    if not consumed:
        await update.message.reply_text(
            f"⛔ *Kunlik chegaraga yetdingiz* ({DAILY_LIMIT} ta savol)\n\n"
            f"Iltimos ertaga qaytib keling. Chegara yarim tunda yangilanadi.",
            parse_mode="Markdown",
        )
        return

    # Save updated history (keep last N turns)
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    history = history[-(HISTORY_TURNS * 2):]
    save_user(user_id, history=history)

    # Add footer when running low
    footer = ""
    if remaining <= 20:
        footer = f"\n\nBugun qolgan: {remaining} ta savol"

    # Telegram message limit is 4096 chars; split if needed
    full = reply + footer
    if len(full) <= 4000:
        await update.message.reply_text(full)
    else:
        for i in range(0, len(full), 4000):
            await update.message.reply_text(full[i : i + 4000])


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Polite reply for voice messages (not enabled)."""
    await update.message.reply_text(
        "🎤 Hozircha ovozli xabarlarni qayta ishlay olmayman.\n"
        "Iltimos savolingizni matn ko'rinishida yuboring."
    )


def create_application() -> Application:
    """Build and configure telegram application with all handlers."""
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Commands (English + Uzbek aliases)
    app.add_handler(CommandHandler(["start"], cmd_start))
    app.add_handler(CommandHandler(["help", "yordam"], cmd_help))
    app.add_handler(CommandHandler(["new", "yangi"], cmd_new))
    app.add_handler(CommandHandler(["limit"], cmd_limit))
    app.add_handler(CommandHandler(["mode", "rejim"], cmd_mode))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(on_mode_callback, pattern=r"^mode_"))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    return app


# ────────── Entry point ──────────
def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    init_db()

    app = create_application()

    # Python 3.14+ may start without a default event loop in MainThread.
    # python-telegram-bot's run_polling/run_webhook expects one to exist.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    if WEBHOOK_URL:
        logging.info(f"Starting in webhook mode on port {PORT}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook",
            drop_pending_updates=True,
        )
    else:
        logging.info("Starting in polling mode (set WEBHOOK_URL for production)")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
