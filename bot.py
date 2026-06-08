import logging
import json
import os
import re
from datetime import datetime, time
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
import urllib.request

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
DATA_FILE = "trades.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PAIR, DIRECTION, SETUP, ENTRY, SL, TP, NOTE = range(7)

def load_trades():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_trades(trades):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)

CHECKLIST = """✅ *Чеклист перед входом в сделку*

1️⃣ Определил направление по азиатской сессии?
2️⃣ Есть OB или FVG на старшем ТФ?
3️⃣ Есть confluence? (OB + FVG в одной зоне)
4️⃣ Подтверждение на 1М получено?
5️⃣ Проверил новости?
6️⃣ Время торговли правильное? (Лондон 10–12 / NY 15–17 МСК)
7️⃣ R:R не менее 1:2?
8️⃣ Размер позиции рассчитан?

Если все 8 ✅ — входи. Если нет — пропусти."""

# ─── ПАРСИНГ FOREX FACTORY ────────────────────────────────────────────────────
def fetch_news_today():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
        results = []

        for item in data:
            # Только USD новости с импактом High
            if item.get("impact") != "High":
                continue
            if item.get("country") != "USD":
                continue

            # Парсим время
            raw_date = item.get("date", "")
            try:
                # Формат: "2026-06-08T12:30:00-04:00"
                dt_utc = datetime.fromisoformat(raw_date)
                dt_msk = dt_utc.astimezone(MOSCOW_TZ)
                if dt_msk.strftime("%Y-%m-%d") != today:
                    continue
                time_str = dt_msk.strftime("%H:%M")
            except Exception:
                continue

            results.append({
                "title": item.get("title", ""),
                "time": time_str
            })

        return sorted(results, key=lambda x: x["time"])

    except Exception as e:
        logger.error(f"Ошибка парсинга новостей: {e}")
        return []

def format_news_message(news_list):
    today = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")

    if not news_list:
        return f"📰 *Новости USD 3🐂 на {today}*\n\n✅ Важных новостей нет — можно торговать спокойно!"

    text = f"📰 *Важные новости USD на {today}*\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    for n in news_list:
        text += f"🔴 *{n['time']} МСК* — {n['title']}\n"
        text += f"   ⚠️ Не входить с {n['time'][:-2]}{int(n['time'][-2:])-15:02d} до {n['time'][:-2]}{int(n['time'][-2:])+15:02d}\n\n"

    text += "━━━━━━━━━━━━━━━━━━━━\n"
    text += "⚠️ За 15–30 мин до и после — *не входить в сделки!*"
    return text

# ─── КОМАНДЫ ──────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📝 Новая сделка"), KeyboardButton("📊 Статистика")],
        [KeyboardButton("✅ Чеклист"), KeyboardButton("📰 Новости")],
        [KeyboardButton("❓ Помощь")]
    ]
    await update.message.reply_text(
        "👋 Привет, Загит!\n\nЯ твой трейдинг-ассистент по SMC.\n"
        "Помогу логировать сделки, напоминать о сессиях и новостях.\n\n"
        "Выбери действие:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def checklist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(CHECKLIST, parse_mode="Markdown")

async def news_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Загружаю новости...")
    news = fetch_news_today()
    await update.message.reply_text(format_news_message(news), parse_mode="Markdown")

async def trade_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [["XAUUSD", "EURUSD"]]
    await update.message.reply_text(
        "📝 *Новая сделка*\n\nВыбери пару:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return PAIR

async def trade_pair(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["pair"] = update.message.text
    keyboard = [["BUY", "SELL"]]
    await update.message.reply_text("Направление?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return DIRECTION

async def trade_direction(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["direction"] = update.message.text
    keyboard = [["OB", "FVG", "OB + FVG"]]
    await update.message.reply_text("Сетап?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return SETUP

async def trade_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["setup"] = update.message.text
    await update.message.reply_text("Цена входа:")
    return ENTRY

async def trade_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["entry"] = update.message.text
    await update.message.reply_text("Stop Loss:")
    return SL

async def trade_sl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["sl"] = update.message.text
    await update.message.reply_text("Take Profit:")
    return TP

async def trade_tp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["tp"] = update.message.text
    await update.message.reply_text("Заметка или /skip:")
    return NOTE

async def trade_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["note"] = update.message.text if update.message.text != "/skip" else ""
    return await save_trade(update, ctx)

async def trade_skip_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["note"] = ""
    return await save_trade(update, ctx)

async def save_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.user_data
    trade = {
        "id": int(datetime.now().timestamp()),
        "date": datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M"),
        "pair": d.get("pair"),
        "direction": d.get("direction"),
        "setup": d.get("setup"),
        "entry": d.get("entry"),
        "sl": d.get("sl"),
        "tp": d.get("tp"),
        "note": d.get("note", ""),
        "result": None
    }
    trades = load_trades()
    trades.append(trade)
    save_trades(trades)

    summary = f"""✅ *Сделка сохранена!*

📌 {trade['pair']} | {trade['direction']} | {trade['setup']}
💰 Вход: `{trade['entry']}`
🛑 SL: `{trade['sl']}`
🎯 TP: `{trade['tp']}`
📅 {trade['date']}
📝 {trade['note'] or '—'}

_ID: {trade['id']}_
_/result {trade['id']} +150_"""

    keyboard = [
        ["📝 Новая сделка", "📊 Статистика"],
        ["✅ Чеклист", "📰 Новости"],
        ["❓ Помощь"]
    ]
    await update.message.reply_text(summary, parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [["📝 Новая сделка", "📊 Статистика"], ["✅ Чеклист", "📰 Новости"]]
    await update.message.reply_text("Отменено.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ConversationHandler.END

async def result_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /result <ID> <сумма>\nПример: /result 1234567890 +150")
        return
    try:
        trade_id = int(args[0])
        pnl = float(args[1].replace("+", ""))
    except ValueError:
        await update.message.reply_text("Неверный формат.")
        return
    trades = load_trades()
    for t in trades:
        if t["id"] == trade_id:
            t["result"] = pnl
            save_trades(trades)
            emoji = "✅" if pnl > 0 else "❌"
            await update.message.reply_text(f"{emoji} Результат: {'+'if pnl>0 else ''}{pnl}$")
            return
    await update.message.reply_text("Сделка не найдена.")

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    trades = load_trades()
    if not trades:
        await update.message.reply_text("Сделок пока нет.")
        return
    closed = [t for t in trades if t["result"] is not None]
    total = len(trades)
    wins = len([t for t in closed if t["result"] > 0])
    losses = len([t for t in closed if t["result"] < 0])
    total_pnl = sum(t["result"] for t in closed)
    wr = round(wins / len(closed) * 100) if closed else 0
    text = f"""📊 *Статистика Загит SMC*

- Всего сделок: {total}
- Закрыто: {len(closed)}
- Побед: {wins} | Убытков: {losses}
- Winrate: {wr}%
- P&L: {'+'if total_pnl>0 else ''}{round(total_pnl,2)}$

По парам:
- XAUUSD: {len([t for t in trades if t['pair']=='XAUUSD'])}
- EURUSD: {len([t for t in trades if t['pair']=='EURUSD'])}

По сетапам:
- OB+FVG: {len([t for t in trades if t['setup']=='OB + FVG'])}
- OB: {len([t for t in trades if t['setup']=='OB'])}
- FVG: {len([t for t in trades if t['setup']=='FVG'])}"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = """❓ *Команды*

📝 Новая сделка — логировать по шагам
📊 Статистика — winrate и P&L
✅ Чеклист — 8 пунктов SMC
📰 Новости — USD новости на сегодня

/result ID сумма — записать результат
/cancel — отменить действие"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📊 Статистика":
        await stats(update, ctx)
    elif text == "✅ Чеклист":
        await checklist(update, ctx)
    elif text == "📰 Новости":
        await news_info(update, ctx)
    elif text == "❓ Помощь":
        await help_command(update, ctx)

# ─── SCHEDULER ────────────────────────────────────────────────────────────────
async def morning_news(bot, chat_id):
    news = fetch_news_today()
    text = format_news_message(news)
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")

async def remind_london(bot, chat_id):
    await bot.send_message(chat_id=chat_id, parse_mode="Markdown",
        text="🇬🇧 *Лондонская сессия через 30 минут!*\n\n⏰ 10:00–12:00 МСК\n\n• Проверь структуру азиатской сессии\n• Отметь OB и FVG зоны\n• Проверь новости дня\n\n✅ /checklist")

async def remind_ny(bot, chat_id):
    await bot.send_message(chat_id=chat_id, parse_mode="Markdown",
        text="🇺🇸 *Нью-Йоркская сессия через 30 минут!*\n\n⏰ 15:00–17:00 МСК\n\n• Проверь структуру лондонской сессии\n• Обнови OB и FVG зоны\n• Проверь новости США\n\n✅ /checklist")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("trade", trade_start),
            MessageHandler(filters.Regex("^📝 Новая сделка$"), trade_start)
        ],
        states={
            PAIR:      [MessageHandler(filters.TEXT & ~filters.COMMAND, trade_pair)],
            DIRECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, trade_direction)],
            SETUP:     [MessageHandler(filters.TEXT & ~filters.COMMAND, trade_setup)],
            ENTRY:     [MessageHandler(filters.TEXT & ~filters.COMMAND, trade_entry)],
            SL:        [MessageHandler(filters.TEXT & ~filters.COMMAND, trade_sl)],
            TP:        [MessageHandler(filters.TEXT & ~filters.COMMAND, trade_tp)],
            NOTE:      [
                CommandHandler("skip", trade_skip_note),
                MessageHandler(filters.TEXT & ~filters.COMMAND, trade_note)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("checklist", checklist))
    app.add_handler(CommandHandler("news", news_info))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("result", result_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex("^(📊|✅|📰|❓)"),
        button_handler
    ))

    YOUR_CHAT_ID = int(os.getenv("CHAT_ID", "0"))

    if YOUR_CHAT_ID:
        scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
        # Утренние новости в 09:00
        scheduler.add_job(morning_news, "cron", hour=9, minute=0,
                          day_of_week="mon-fri", args=[app.bot, YOUR_CHAT_ID])
        # Лондон напоминание в 09:30
        scheduler.add_job(remind_london, "cron", hour=9, minute=30,
                          day_of_week="mon-fri", args=[app.bot, YOUR_CHAT_ID])
        # NY напоминание в 14:30
        scheduler.add_job(remind_ny, "cron", hour=14, minute=30,
                          day_of_week="mon-fri", args=[app.bot, YOUR_CHAT_ID])
        scheduler.start()

    print("🤖 Загит SMC Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
