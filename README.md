# Uzbek Student AI Bot 🤖

A free Telegram bot giving Uzbek students AI access for homework, translation, and general questions. Built on Groq (fast Llama 3.3 70B) with Uzbek Latin interface.

## Features

- 💬 **Three modes**: Chat, Homework helper, Uzbek↔English translator
- 📜 **Persistent memory**: Remembers last 10 conversation turns per user
- 📊 **Rate limiting**: 100 messages per user per day (protects your quota)
- 🇺🇿 **Uzbek Latin interface**: All commands and responses in Uzbek
- ⚡ **Fast**: Groq Llama 3.3 70B (~750 tokens/second)
- 💸 **Free to run**: Free tier on Render + Groq's free 14,400 requests/day

## Deployment Guide (~10 minutes total)

### Step 1 — Create the Telegram bot (1 min)

1. Open Telegram and message `@BotFather`
2. Send `/newbot`
3. Choose a name (shown in chat): e.g., `Uzbek AI Yordamchi`
4. Choose a username (must end in `bot`): e.g., `UzbekAiYordamchiBot`
5. **Copy the token** — looks like `8123456789:AAH-xxxxxxxxxxxxxxxxxxxxxx`

While you're with BotFather, also set:
- `/setdescription` → "Bepul AI yordamchi — uy vazifasi, tarjima va savollar uchun"
- `/setcommands` → paste this:
  ```
  rejim - Rejimni o'zgartirish (Suhbat / Uy vazifasi / Tarjima)
  yangi - Yangi suhbat boshlash
  limit - Bugungi qolgan savollarni ko'rish
  yordam - Yordam
  ```

### Step 2 — Get a Groq API key (1 min)

1. Go to [console.groq.com/keys](https://console.groq.com/keys)
2. Sign up with Google (no credit card needed)
3. Click **Create API Key**
4. **Copy the key** — starts with `gsk_...`

### Step 3 — Push code to GitHub (3 min)

1. Create a new GitHub repo (private is fine), e.g., `uzbek-ai-bot`
2. Upload these files:
   - `bot.py`
   - `requirements.txt`
   - `render.yaml`
   - `.gitignore`
   - `README.md`

### Step 4 — Deploy to Render (5 min)

1. Go to [render.com](https://render.com) → sign up (free, with GitHub)
2. Click **New +** → **Web Service**
3. Connect your GitHub repo
4. Render auto-detects `render.yaml`. Confirm settings:
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Plan**: Free
5. Add environment variables:
   - `TELEGRAM_TOKEN` → paste your bot token from Step 1
   - `GROQ_API_KEY` → paste your Groq key from Step 2
   - **Leave `WEBHOOK_URL` empty for now**
6. Click **Create Web Service**
7. Wait for first deploy (~3 min). You'll get a URL like `https://uzbek-ai-bot-abc1.onrender.com`
8. Now go to **Environment** tab → set `WEBHOOK_URL` to that URL → save
9. The service will auto-redeploy. Done! ✅

### Step 5 — Test it

Open Telegram, find your bot by username, send `/start`. You should get the Uzbek welcome message.

---

## Keeping the bot awake (optional but recommended)

Render's free tier sleeps after 15 minutes of inactivity. The first message after sleep takes ~30 seconds to wake the bot. To prevent this:

1. Sign up free at [uptimerobot.com](https://uptimerobot.com)
2. Add a new **HTTP(s)** monitor
3. URL: your Render URL (e.g., `https://uzbek-ai-bot-abc1.onrender.com`)
4. Check interval: 5 minutes
5. Save. Now your bot stays awake 24/7.

---

## Customization

### Change the daily limit

In `bot.py`:

```python
DAILY_LIMIT = 100  # change this number
```

### Add another mode

In `bot.py`, add to the `PROMPTS` and `MODE_NAMES` dicts:

```python
PROMPTS["coding"] = "Sen dasturlash bo'yicha yordamchi san..."
MODE_NAMES["coding"] = "💻 Dasturlash"
```

Then add a button in `cmd_mode()`.

### Switch primary AI model

In `bot.py`:

```python
GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
```

Reorder or replace with any [Groq-supported model](https://console.groq.com/docs/models).

---

## Important note about data persistence

**Render's free tier filesystem is ephemeral** — the SQLite database (`/tmp/bot.db`) resets when:
- The service restarts (sleep/wake cycle, redeploy)
- Render moves the container

This means rate-limit counters and chat histories occasionally reset. For your students this is mostly invisible, but if you want true persistence, switch to a free Postgres database:

- [Supabase](https://supabase.com) — generous free tier
- [Neon](https://neon.tech) — free Postgres
- [Render Postgres](https://render.com/docs/databases) — free for 90 days

(I can help you migrate the storage layer when you're ready.)

---

## Promoting your bot

You already have a Telegram channel — add a pinned post like:

> 🤖 *Bepul AI yordamchi — talabalar uchun!*
>
> Uy vazifasi, tarjima, savollar — hammasi bepul.
>
> 👉 [@UzbekAiYordamchiBot](https://t.me/UzbekAiYordamchiBot)
>
> Kuniga 100 ta savol berishingiz mumkin.

---

## Troubleshooting

**Bot doesn't respond at all**
- Check Render logs for errors
- Verify `WEBHOOK_URL` is set to your actual Render URL (no trailing slash)
- Make sure environment variables are saved

**"AI hozir mavjud emas" error in bot**
- Check Render logs — likely an issue with `GROQ_API_KEY`
- Or you've hit Groq's free tier limit (14,400 req/day total across all users)

**First message is slow (~30 sec)**
- Normal on Render free tier (cold start)
- Set up UptimeRobot (see above) to keep bot awake

---

## Tech stack

- Python 3.11
- python-telegram-bot 21.6 (async)
- httpx (async HTTP)
- SQLite (state)
- Groq Cloud (Llama 3.3 70B)
- Render (free hosting)

Made for Uzbek students 🇺🇿
