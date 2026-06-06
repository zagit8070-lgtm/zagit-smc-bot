import logging
import json
import os
from datetime import datetime, time
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

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
   _Азия бычья → ищу покупки / медвежья → продажи_

2️⃣ Есть OB или FVG на старшем ТФ?
   _🟢 OB или 🟣 FVG чётко обозначены?_

3️⃣ Есть confluence? (OB + FVG в одной зоне)
   _Приоритетный сетап — оба совпадают_

4️⃣ Подтверждение на 1М получено?
   _Buy: красная → зелёная свеча_
   _Sell: зелёная → красная свеча_

5️⃣ Проверил новости?
   _Нет новостей 3🐂 в ближайшие 30 мин?_

6️⃣ Время торговли правильное?
   _Лондон 10:00–12:00 МСК или NY 15:00–17:00 МСК_

7️⃣ R:R не менее 1:2?
   _Цель минимум 1:3_

8️⃣ Размер позиции рассчитан по риск-менеджменту?

━━━━━━━━━━━━━━━━━━━━
Если все 8 пунктов ✅ — входи. Если нет — пропусти сделку."""

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📝 Новая сделка"), KeyboardButton("📊 Статистика")],
        [KeyboardButton("✅ Чеклист"), KeyboardButton("📰 Новости")],
        [KeyboardButton("❓ Помощь")]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "👋 Привет, Загит!\n\nЯ твой трейдинг-ассистент по SMC.\n"
        "Помогу логировать сделки, напоминать о сессиях и новостях.\n\n"
        "Выбери действие:",
        reply_markup=markup
    )

async def checklist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(CHECKLIST, parse_mode="Markdown")

async def news_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = """📰 *Ключевые новости для XAUUSD и EURUSD*

🔴 *NFP* — Первая пятница месяца, 15:30 МСК
   _Не входить за 30 мин до и 30 мин после_

🔴 *CPI* — ~10–12 числа, 15:30 МСК
   _Сильно двигает золото и доллар_

🔴 *Решение ФРС* — каждые 6 недель, 21:00 МСК
   _+пресс-конференция Пауэлла через 30 мин_

🟡 *Другие USD новости 3🐂*:
   • ADP Employment (каждую среду)
   • Retail Sales (~15-го числа)
   • GDP (~конец месяца)

━━━━━━━━━━━━━━━━━━━━
⚠️ Правило: за 15–30 мин до новости 3🐂 и 15–30 мин после — *не входить в сделку*."""
    await update.message.reply_text(text, parse_mode="Markdown")

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
    await update.message.reply_text(
        "Направление?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return DIRECTION

async def trade_direction(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["direction"] = update.message.text
    keyboard = [["OB", "FVG", "OB + FVG"]]
    await update.message.reply_text(
        "Сетап?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
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
    await update.message.reply_text("Заметка (сессия, причина, ошибки) или напиши /skip:")
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

_ID сделки: {trade['id']}_
_Используй /result {trade['id']} +150 чтобы записать результат_"""

    keyboard = [
        ["📝 Новая сделка", "📊 Статистика"],
        ["✅ Чеклист", "📰 Новости"],
        ["❓ Помощь"]
    ]
    await update.message.reply_text(
        summary,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [["📝 Новая сделка", "📊 Статистика"], ["✅ Чеклист", "📰 Новости"]]
    await update.message.reply_text(
        "Отменено.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return ConversationHandler.END

async def result_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /result <ID> <результат$>\nПример: /result 1234567890 +150")
        return
    trade_id = int(args[0])
    pnl_str = args[1]
    try:
        pnl = float(pnl_str.replace("+", ""))
    except ValueError:
        await update.message.reply_text("Неверный формат. Пример: /result 1234567890 +150")
        return
    trades = load_trades()
    found = False
    for t in trades:
        if t["id"] == trade_id:
            t["result"] = pnl
            found = True
            break
    if found:
        save_trades(trades)
        emoji = "✅" if pnl > 0 else "❌"
        await update.message.reply_text(f"{emoji} Результат записан: {'+'if pnl>0 else ''}{pnl}$")
    else:
        await update.message.reply_text("Сделка не найдена. Проверь ID.")

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    trades = load_trades()
    if not trades:
        await update.message.reply_text("📊 Сделок пока нет. Добавь первую через 📝 Новая сделка")
        return
    closed = [t for t in trades if t["result"] is not None]
    total = len(trades)
    wins = len([t for t in closed if t["result"] > 0])
    losses = len([t for t in closed if t["result"] < 0])
    total_pnl = sum(t["result"] for t in closed)
    wr = round(wins / len(closed) * 100) if closed else 0
    xau = [t for t in trades if t["pair"] == "XAUUSD"]
    eur = [t for t in trades if t["pair"] == "EURUSD"]
    ob_trades = [t for t in trades if "OB" in t["setup"]]
    fvg_trades = [t for t in trades if t["setup"] == "FVG"]
    confluence = [t for t in trades if t["setup"] == "OB + FVG"]
    text = f"""📊 *Статистика Загит SMC*

━━━━━━━━━━━━━━━━━━━━
📈 *Общее*
- Всего сделок: {total}
- Закрыто: {len(closed)}
- Побед: {wins} | Убытков: {losses}
- Winrate: {wr}%
- Итого P&L: {'+'if total_pnl>0 else ''}{round(total_pnl, 2)}$

━━━━━━━━━━━━━━━━━━━━
📌 *По парам*
- XAUUSD: {len(xau)} сделок
- EURUSD: {len(eur)} сделок

━━━━━━━━━━━━━━━━━━━━
🎯 *По сетапам*
- OB: {len(ob_trades)} сделок
- FVG: {len(fvg_trades)} сделок
- OB + FVG (confluence): {len(confluence)} сделок

━━━━━━━━━━━━━━━━━━━━
_Открытых сделок: {total - len(closed)}_"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = """❓ *Команды бота*

📝 *Новая сделка* — записать сделку по шагам
📊 *Статистика* — winrate, P&L, разбивка по парам
✅ *Чеклист* — проверка перед входом (8 пунктов SMC)
📰 *Новости* — даты NFP, CPI, ФРС

/result <ID> <сумма> — записать результат сделки
_Пример: /result 1234567890 +150_

/cancel — отменить текущее действие"""
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

async def remind_london(bot, chat_id):
    await bot.send_message(
        chat_id=chat_id,
        text="🇬🇧 *Лондонская сессия через 30 минут!*\n\n"
             "⏰ Торговое окно: 10:00–12:00 МСК\n\n"
             "Не забудь:\n"
             "• Проверить структуру азиатской сессии\n"
             "• Отметить OB и FVG зоны\n"
             "• Проверить новости на сегодня\n\n"
             "✅ /checklist",
        parse_mode="Markdown"
    )

async def remind_ny(bot, chat_id):
    await bot.send_message(
        chat_id=chat_id,
        text="🇺🇸 *Нью-Йоркская сессия через 30 минут!*\n\n"
             "⏰ Торговое окно: 15:00–17:00 МСК\n\n"
             "Не забудь:\n"
             "• Проверить структуру лондонской сессии\n"
             "• Обновить OB и FVG зоны\n"
             "• Проверить новости США\n\n"
             "✅ /checklist",
        parse_mode="Markdown"
    )

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
        scheduler.add_job(remind_london, "cron", hour=9, minute=30,
                          day_of_week="mon-fri",
                          args=[app.bot, YOUR_CHAT_ID])
        scheduler.add_job(remind_ny, "cron", hour=14, minute=30,
                          day_of_week="mon-fri",
                          args=[app.bot, YOUR_CHAT_ID])
        scheduler.start()

    print("🤖 Загит SMC Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
