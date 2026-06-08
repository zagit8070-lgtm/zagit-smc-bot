import logging
import json
import os
from datetime import datetime
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

# ─── DATA ─────────────────────────────────────────────────────────────────────
def load_trades():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_trades(trades):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)

# ─── НОВОСТИ ──────────────────────────────────────────────────────────────────
def fetch_news_today():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
        results = []
        for item in data:
            if item.get("impact") != "High": continue
            if item.get("country") != "USD": continue
            raw_date = item.get("date", "")
            try:
                dt_utc = datetime.fromisoformat(raw_date)
                dt_msk = dt_utc.astimezone(MOSCOW_TZ)
                if dt_msk.strftime("%Y-%m-%d") != today: continue
                results.append({"title": item.get("title",""), "time": dt_msk.strftime("%H:%M")})
            except: continue
        return sorted(results, key=lambda x: x["time"])
    except Exception as e:
        logger.error(f"Ошибка новостей: {e}")
        return []

def format_news_message(news_list):
    today = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")
    if not news_list:
        return f"📰 *Новости USD на {today}*\n\n✅ Важных новостей нет — торгуем спокойно!"
    text = f"📰 *Важные новости USD на {today}*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for n in news_list:
        h, m = int(n['time'].split(':')[0]), int(n['time'].split(':')[1])
        m_before = f"{h}:{m-15:02d}" if m >= 15 else f"{h-1}:{60+m-15:02d}"
        m_after = f"{h}:{m+15:02d}" if m <= 44 else f"{h+1}:{m-45:02d}"
        text += f"🔴 *{n['time']} МСК* — {n['title']}\n"
        text += f"   ⚠️ Не входить: {m_before}–{m_after}\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n⚠️ За 15–30 мин до и после — *не входить!*"
    return text

# ─── ОБУЧЕНИЕ SMC ─────────────────────────────────────────────────────────────
LEARN_TOPICS = {
    "structure": """📚 *Рыночная структура (BOS / CHoCH)*

*Что это:*
Основа SMC — понимание куда движется рынок через серию хаёв и лоёв.

*BOS (Break of Structure) — пробой структуры:*
Цена пробивает предыдущий хай/лой → структура продолжается
— Новый хай выше предыдущего = бычья структура
— Новый лой ниже предыдущего = медвежья структура

*CHoCH (Change of Character) — смена характера:*
Цена впервые пробивает структуру в противоположную сторону → сигнал разворота

*📌 Пример на XAUUSD:*
```
Медвежья структура:
Хай 4450 → Лой 4380 → Хай 4420 → Лой 4350
Каждый хай ниже предыдущего = продолжение вниз

CHoCH:
Лой 4350 → Хай 4430 (пробил 4420) = смена на бычью!
Теперь ищем покупки
```

*Правило для торговли:*
— Торгуй только в направлении структуры H4
— CHoCH на H4 = смена bias на весь день
— BOS на M15 подтверждает направление для входа

*Частые ошибки:*
❌ Путать BOS и CHoCH
❌ Торговать против структуры H4
❌ Не обновлять bias после CHoCH""",

    "liquidity": """📚 *Ликвидность и её снятие*

*Что это:*
Ликвидность — скопление стоп-лоссов трейдеров за очевидными уровнями. Smart Money целенаправленно идут туда чтобы забрать эти ордера.

*Где находится ликвидность:*
— BSL (Buy Side): над хаями, над линиями сопротивления
— SSL (Sell Side): под лоями, под линиями поддержки
— За круглыми числами (4400, 4350, 4300)

*Sweep (снятие ликвидности):*
Цена ненадолго пробивает уровень, собирает стопы и разворачивается

*📌 Пример на XAUUSD:*
```
Азиатский диапазон: лой 4310, хай 4330
Лондон открылся: цена пошла вниз до 4305
(сняла стопы под азиатским лоем 4310)
Затем резкий разворот вверх до 4360

Вход: после разворота от 4310
SL: 4300 (ниже sweep)
TP: 4360 (следующая ликвидность)
R:R = 1:5 ✅
```

*Как торговать:*
1. Отметь хай и лой азиатской сессии
2. На Лондоне жди sweep одного из уровней
3. После разворота — вход в сторону sweep
4. TP — противоположная ликвидность

*Частые ошибки:*
❌ Входить во время sweep (до разворота)
❌ Не ждать подтверждения разворота на M1
❌ Ставить SL прямо на уровень ликвидности""",

    "ob": """📚 *Order Block (OB) — Ордер Блок*

*Что это:*
Зона где крупные игроки разместили большие ордера перед сильным импульсом. Цена возвращается туда чтобы "закрыть" позиции и продолжить движение.

*Как выглядит:*
— Бычий OB: последняя медвежья свеча перед импульсом вверх
— Медвежий OB: последняя бычья свеча перед импульсом вниз

*📌 Пример на XAUUSD:*
```
H4 график, 5 июня 2026:
Бычья свеча: open 4390 → close 4408
Следующая — сильный медвежий импульс до 4323

🔴 Медвежий OB = зона 4390–4408
Цена вернулась в зону → отбой вниз
Вход: 4400, SL: 4415, TP: 4355
R:R = 1:3 ✅
```

*Признаки сильного OB:*
— От него начался импульс 3+ свечей
— OB ещё не был митигирован
— Совпадает с FVG (confluence)
— На уровне структурного хая/лоя

*Как торговать:*
1. Нашёл OB на H4
2. Жди откат цены в зону
3. На M1 — подтверждение (разворотная свеча)
4. SL за границу OB
5. TP — следующая зона ликвидности

*Частые ошибки:*
❌ Торговать митигированный OB
❌ Вход без подтверждения на M1
❌ Игнорировать структуру H4""",

    "fvg": """📚 *Fair Value Gap (FVG) — Имбаланс*

*Что это:*
Зона где цена двигалась так быстро что не успела закрыть все ордера. Образуется при сильном импульсе — три свечи где тело средней не перекрывается тенями первой и третьей.

*Как определить:*
— Берём 3 свечи
— FVG = пространство между хаем свечи 1 и лоем свечи 3
— Чем больше зазор — тем сильнее магнит

*📌 Пример на XAUUSD:*
```
Импульс вниз на M15:
Свеча 1: хай 4420
Свеча 2: большая медвежья (тело импульса)
Свеча 3: лой 4390

FVG = зона 4390–4420
Цена вернулась в FVG → продолжила вниз
Вход: 4410, SL: 4425, TP: 4370
R:R = 1:2.7 ✅
```

*Типы FVG:*
— Бычий FVG: магнит для цены снизу, поддержка
— Медвежий FVG: магнит для цены сверху, сопротивление

*Confluence с OB:*
Если FVG находится внутри OB = приоритетный сетап 🎯
Цена притягивается туда сильнее всего

*Частые ошибки:*
❌ Входить при первом касании без подтверждения
❌ Торговать закрытый FVG (цена уже прошла его полностью)
❌ Игнорировать направление структуры""",

    "confluence": """📚 *Confluence — Зона совпадения*

*Что это:*
Confluence = несколько факторов указывают на одну зону. Чем больше совпадений — тем выше вероятность отработки.

*Что должно совпадать:*
— OB + FVG в одной зоне (минимум)
— Уровень структуры (хай/лой)
— Круглое число (4400, 4350)
— Зона ликвидности
— Совпадение на нескольких ТФ

*Оценка качества сетапа:*
```
2 фактора = средний сетап
3 фактора = хороший сетап
4+ факторов = приоритетный сетап 🎯
```

*📌 Пример на XAUUSD:*
```
Зона 4338–4346:
✅ Медвежий OB на H4
✅ FVG внутри зоны
✅ Уровень предыдущего лоя структуры
✅ Близко к круглому числу 4340

4 фактора = максимальный приоритет
Результат: цена отбилась и пошла вниз на 80 пунктов
```

*Правило:*
Не входи в сделку если менее 2 факторов confluence.
OB + FVG = минимальный стандарт для входа.

*Частые ошибки:*
❌ Входить по одному фактору
❌ Не проверять совпадение на H4 и M15
❌ Игнорировать структурные уровни""",

    "breaker": """📚 *Breaker Block — Блок пробоя*

*Что это:*
Бывший OB который был пробит ценой. После пробоя он меняет роль — бычий OB становится медвежьим сопротивлением и наоборот.

*Как формируется:*
1. Был бычий OB (поддержка)
2. Цена пробила его вниз (структурный BOS вниз)
3. Бывший бычий OB = теперь Breaker Block (сопротивление)

*📌 Пример на XAUUSD:*
```
Бычий OB на H1: зона 4380–4395
Цена держалась там несколько раз
Затем сильный импульс пробил 4380 вниз

Breaker Block = зона 4380–4395
Цена вернулась в 4385 (в зону) → отбой вниз
Вход: 4383, SL: 4398, TP: 4343
R:R = 1:2.7 ✅
```

*Отличие от OB:*
— OB = зона где цена ещё не была после формирования
— Breaker = зона где цена уже была и пробила

*Почему работает:*
Трейдеры у которых были стопы за OB теперь хотят выйти в безубыток — они продают при возврате цены в зону

*Частые ошибки:*
❌ Путать Breaker с обычным OB
❌ Не проверять был ли пробой структурным (BOS)
❌ Торговать Breaker против тренда H4""",

    "mitigation": """📚 *Митигация блоков*

*Что это:*
Митигация = цена вернулась в зону OB/FVG и "закрыла" ордера крупного игрока. После митигации зона теряет силу.

*Признаки митигации:*
— Цена полностью прошла через зону OB
— FVG закрыт (тела свечей заполнили зазор)
— Цена провела в зоне много времени

*Частичная митигация:*
Цена коснулась верхней/нижней части OB но не прошла насквозь — зона ещё работает

*📌 Пример на XAUUSD:*
```
Медвежий OB: зона 4390–4408
Первое касание: цена зашла до 4395, отбилась → OB работает ✅
Второе касание: цена зашла до 4408, отбилась → частичная митигация ⚠️
Третье касание: цена прошла через 4410 → OB митигирован ❌

После митигации эту зону не торгуем!
```

*Правило:*
Торгуй только немитигированные OB — те куда цена ещё не возвращалась после формирования.

*Как проверить:*
На графике должна быть чёткая импульсная свеча ОТ зоны без последующего возврата в неё

*Частые ошибки:*
❌ Торговать митигированный OB снова и снова
❌ Не проверять историю зоны перед входом
❌ Считать частичную митигацию полной""",

    "inducement": """📚 *Inducement — Ловушка перед входом*

*Что это:*
Inducement = ложное движение которое Smart Money создают чтобы заманить розничных трейдеров в неправильную сторону перед настоящим движением.

*Как выглядит:*
— Небольшой пробой уровня (sweep малой ликвидности)
— Ложный BOS на младшем ТФ
— "Приманка" перед настоящим входом

*📌 Пример на XAUUSD:*
```
Цена падает к зоне OB 4320–4335
Перед OB делает маленький sweep вниз до 4318
(Inducement — ловушка для медведей)
Розничные трейдеры открывают продажи
Smart Money разворачивают цену вверх от OB
Вход на покупку: 4325, SL: 4312, TP: 4385
R:R = 1:4.6 ✅
```

*Признаки Inducement:*
— Небольшое движение против тренда перед OB
— Быстро разворачивается без закрепления
— Снимает очевидные стопы розничных трейдеров

*Как использовать:*
Видишь inducement перед OB = дополнительное подтверждение что зона сильная, Smart Money активно защищают её

*Частые ошибки:*
❌ Путать inducement с настоящим разворотом
❌ Входить во время inducement
❌ Игнорировать inducement как сигнал подтверждения""",

    "entry": """📚 *Правила входа в сделку*

*Алгоритм входа по SMC:*

*Шаг 1 — Определи bias (H4):*
— Структура бычья → ищем покупки
— Структура медвежья → ищем продажи
— CHoCH на H4 → меняем направление

*Шаг 2 — Найди зону (H1/M15):*
— OB + FVG в направлении bias
— Минимум 2 фактора confluence
— Зона немитигированная

*Шаг 3 — Жди цену в зону*
— Не входи раньше времени
— Цена должна сама прийти к зоне

*Шаг 4 — Подтверждение на M1:*
— Продажа: зелёная свеча касания → следующая закрылась красной
— Покупка: красная свеча касания → следующая закрылась зелёной

*Шаг 5 — Вход и управление:*
— SL: за границу OB + 3–5 пунктов буфер
— TP1: ближайшая ликвидность (минимум 1:2)
— TP2: следующая зона (1:3 и выше)

*📌 Пример на XAUUSD:*
```
Bias H4: медвежий (серия lower highs)
Зона M15: медвежий OB 4390–4408 + FVG
Цена пришла в зону: 4395
M1 подтверждение: зелёная → красная свеча
Вход: 4393
SL: 4412 (за OB + буфер)
TP: 4355 (ближайший лой)
R:R = 1:2 ✅
```

*Правило дисциплины:*
Нет подтверждения на M1 = нет входа. Всегда.""",

    "risk": """📚 *Риск-менеджмент*

*Основные правила:*

*1. Риск на сделку: 1–2% депозита*
```
Депозит $1000:
Риск 1% = $10 на сделку
Риск 2% = $20 на сделку
```

*2. Расчёт размера позиции:*
```
Лот = (Депозит × Риск%) / (SL в пунктах × стоимость пункта)

Пример XAUUSD:
Депозит: $1000
Риск: 1% = $10
SL: 15 пунктов
Стоимость пункта 0.01 лота = $0.10

Лот = $10 / (15 × $10) = 0.07 лота
```

*3. Дневной лимит убытков: 3–5%*
Потерял 3% за день → стоп торговли до следующего дня

*4. Минимальное R:R = 1:2*
Не входи если потенциал меньше 1:2
Цель всегда 1:3

*5. Максимум 2–3 сделки в день*
Качество важнее количества

*📌 Пример расчёта для XAUUSD:*
```
Депозит: $500
Риск: 1% = $5
Вход: 4393, SL: 4408 = 15 пунктов
Стоимость пункта (0.01 лот) = $0.10

Лот = $5 / (15 × $0.10) = 0.033 → округляем до 0.03
```

*Правило психологии:*
Убыток по правилам = хорошая сделка.
Прибыль без правил = плохая сделка.""",

    "sessions": """📚 *Торговые сессии для XAUUSD*

*🇬🇧 Лондон: 10:00–12:00 МСК — ГЛАВНЫЙ ПРИОРИТЕТ*

Почему:
— Открытие Европы = первый большой объём дня
— Smart Money снимают ликвидность азиатского диапазона
— Самые чёткие OB и FVG формируются здесь
— 60–70% лучших сетапов на золоте — Лондон

Что происходит:
1. Азия сформировала диапазон (хай и лой)
2. Лондон делает sweep одной из сторон
3. Разворот и импульс в противоположную сторону
4. Вход на откате в OB/FVG

*🇺🇸 Нью-Йорк: 15:00–17:00 МСК — ВТОРОЙ ПРИОРИТЕТ*

Почему:
— Открытие США = второй большой объём
— Часто продолжает или разворачивает Лондон
— Активно при выходе данных (15:30 МСК)
— Хорошие сетапы если пропустил Лондон

*❌ Когда НЕ торговать:*
— 00:00–09:00 МСК: азия, вялое движение, много ложных сигналов
— 12:00–15:00 МСК: обед, низкий объём, непредсказуемо
— После 17:00 МСК: активность падает, спред растёт

*📌 Идеальный день:*
```
09:00 — получил новости от бота
09:30 — подготовился, отметил зоны H4
09:45 — проверил азиатский диапазон
10:00 — ТОРГОВЛЯ (Лондон открылся)
12:00 — стоп торговли
14:30 — подготовка к NY
15:00 — ТОРГОВЛЯ (NY открылся)  
17:00 — стоп торговли, разбор сделок
```""",

    "checklist": """📚 *Чеклист перед входом — разбор каждого пункта*

*Почему чеклист важен:*
90% ошибок трейдеров — эмоциональные решения. Чеклист убирает эмоции.

*Разбор 8 пунктов:*

*1️⃣ Направление по азиатской сессии?*
Азия сформировала диапазон — это твой первый фильтр.
Цена выше середины азии = бычий bias
Цена ниже середины азии = медвежий bias

*2️⃣ Есть OB или FVG на H4?*
Без зоны — нет сделки. Зона должна быть чёткой, видимой, немитигированной.

*3️⃣ Есть confluence (OB + FVG)?*
Минимальный стандарт. Одного фактора недостаточно.

*4️⃣ Подтверждение на M1?*
Самый важный пункт для точного входа.
Без подтверждения — не входим даже если всё остальное идеально.

*5️⃣ Проверил новости?*
Бот присылает новости в 09:00. Нет новостей 3🐂 в ближайшие 30 мин = безопасно.

*6️⃣ Время торговли правильное?*
10:00–12:00 или 15:00–17:00 МСК. В другое время — не входим.

*7️⃣ R:R не менее 1:2?*
Если до цели меньше чем в 2 раза расстояние до SL — пропускаем.

*8️⃣ Размер позиции рассчитан?*
Используй /risk чтобы рассчитать лот. Никогда не угадывай.

*Правило:*
7 из 8 = можно рассмотреть.
8 из 8 = входим уверенно. 🎯"""
}

LEARN_MENU = """📚 *Обучение SMC — XAUUSD*

Выбери тему:

*Основы:*
/learn structure — Структура рынка (BOS/CHoCH)
/learn liquidity — Ликвидность и снятие
/learn ob — Order Block
/learn fvg — Fair Value Gap
/learn confluence — Confluence зоны

*Продвинутое:*
/learn breaker — Breaker Block
/learn mitigation — Митигация блоков
/learn inducement — Inducement (ловушка)

*Торговля:*
/learn entry — Правила входа
/learn risk — Риск-менеджмент
/learn sessions — Торговые сессии
/learn checklist — Разбор чеклиста"""

CHECKLIST_TEXT = """✅ *Чеклист перед входом в сделку*

1️⃣ Направил direction по азиатской сессии?
   _Азия бычья → покупки / медвежья → продажи_
2️⃣ Есть OB или FVG на H4?
3️⃣ Есть confluence? (OB + FVG)
4️⃣ Подтверждение на M1?
   _Buy: красная → зелёная / Sell: зелёная → красная_
5️⃣ Проверил новости? (нет 3🐂 в ближайшие 30 мин?)
6️⃣ Время правильное? (Лондон 10–12 / NY 15–17 МСК)
7️⃣ R:R не менее 1:2?
8️⃣ Размер позиции рассчитан? (/risk)

━━━━━━━━━━━━━━━━━━━━
Все 8 ✅ — входи. Меньше 7 — пропусти сделку."""

SESSIONS_TEXT = """⏰ *Лучшее время для XAUUSD*

🇬🇧 *Лондон 10:00–12:00 МСК* — главный приоритет
• Снятие ликвидности азиатского диапазона
• Самые чёткие OB и FVG
• Ложный пробой хая/лоя азии → разворот

🇺🇸 *Нью-Йорк 15:00–17:00 МСК* — второй приоритет
• Продолжение или разворот Лондона
• Активно при выходе данных США (15:30)

❌ *Не торговать:*
• 00:00–09:00 — азия, вялое движение
• 12:00–15:00 — обед, низкий объём
• После 17:00 — активность затухает

━━━━━━━━━━━━━━━━━━━━
_Используй /learn sessions для подробного разбора_"""

# ─── КОМАНДЫ ──────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📝 Новая сделка"), KeyboardButton("📊 Статистика")],
        [KeyboardButton("✅ Чеклист"), KeyboardButton("📰 Новости")],
        [KeyboardButton("⏰ Сессии"), KeyboardButton("📚 Обучение")],
        [KeyboardButton("❓ Помощь")]
    ]
    await update.message.reply_text(
        "👋 Привет, Загит!\n\nЯ твой трейдинг-ассистент по SMC на XAUUSD 🥇\n\n"
        "Помогу логировать сделки, напоминать о сессиях и новостях, обучаться SMC.\n\n"
        "Выбери действие:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def checklist_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(CHECKLIST_TEXT, parse_mode="Markdown")

async def sessions_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(SESSIONS_TEXT, parse_mode="Markdown")

async def learn_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(LEARN_MENU, parse_mode="Markdown")

async def learn_topic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(LEARN_MENU, parse_mode="Markdown")
        return
    topic = args[0].lower()
    if topic in LEARN_TOPICS:
        await update.message.reply_text(LEARN_TOPICS[topic], parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"Тема *{topic}* не найдена.\n\n" + LEARN_MENU,
            parse_mode="Markdown"
        )

async def news_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Загружаю новости...")
    news = fetch_news_today()
    await update.message.reply_text(format_news_message(news), parse_mode="Markdown")

# ─── RISK CALCULATOR ──────────────────────────────────────────────────────────
async def risk_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 3:
        await update.message.reply_text(
            "🧮 *Калькулятор позиции XAUUSD*\n\n"
            "Использование:\n`/risk <депозит> <риск%> <SL в пунктах>`\n\n"
            "Пример:\n`/risk 1000 1 15`\n"
            "_Депозит $1000, риск 1%, SL 15 пунктов_",
            parse_mode="Markdown"
        )
        return
    try:
        deposit = float(args[0])
        risk_pct = float(args[1])
        sl_points = float(args[2])
        risk_amount = deposit * risk_pct / 100
        point_value_001 = 0.10
        lot = risk_amount / (sl_points * point_value_001 * 10)
        lot = round(lot, 2)
        tp2 = sl_points * 2
        tp3 = sl_points * 3
        text = f"""🧮 *Расчёт позиции XAUUSD*

━━━━━━━━━━━━━━━━━━━━
💰 Депозит: ${deposit:,.0f}
⚠️ Риск: {risk_pct}% = ${risk_amount:.2f}
🛑 SL: {sl_points:.0f} пунктов

📊 *Размер позиции: {lot} лот*

🎯 Цели:
• TP 1:2 = {tp2:.0f} пунктов (${risk_amount*2:.2f})
• TP 1:3 = {tp3:.0f} пунктов (${risk_amount*3:.2f})
━━━━━━━━━━━━━━━━━━━━
_Минимальный лот XAUUSD = 0.01_"""
        await update.message.reply_text(text, parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("Неверный формат. Пример: /risk 1000 1 15")

# ─── DRAWDOWN ─────────────────────────────────────────────────────────────────
async def drawdown_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    trades = load_trades()
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    today_trades = [t for t in trades if t.get("date","").startswith(today) and t.get("result") is not None]
    today_pnl = sum(t["result"] for t in today_trades)
    losses_today = [t for t in today_trades if t["result"] < 0]

    if today_pnl >= 0:
        emoji = "✅"
        status = f"Сегодня в плюсе: +${today_pnl:.2f}"
    elif today_pnl > -50:
        emoji = "⚠️"
        status = f"Убыток сегодня: ${today_pnl:.2f} — будь осторожен"
    else:
        emoji = "🛑"
        status = f"Стоп торговли! Убыток: ${today_pnl:.2f}"

    text = f"""{emoji} *Дневной контроль убытков*

━━━━━━━━━━━━━━━━━━━━
📅 Сегодня сделок: {len(today_trades)}
❌ Убыточных: {len(losses_today)}
💸 P&L за день: {'+'if today_pnl>=0 else ''}${today_pnl:.2f}

{status}
━━━━━━━━━━━━━━━━━━━━
_Лимит: 3 убытка подряд = стоп на сегодня_"""
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── STREAK ───────────────────────────────────────────────────────────────────
async def streak_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    trades = load_trades()
    closed = [t for t in trades if t.get("result") is not None]
    if not closed:
        await update.message.reply_text("Закрытых сделок пока нет.")
        return

    current_streak = 0
    streak_type = None
    for t in reversed(closed):
        r = t["result"]
        if streak_type is None:
            streak_type = "win" if r > 0 else "loss"
            current_streak = 1
        elif (streak_type == "win" and r > 0) or (streak_type == "loss" and r < 0):
            current_streak += 1
        else:
            break

    max_win = 0
    max_loss = 0
    cur_w = cur_l = 0
    for t in closed:
        if t["result"] > 0:
            cur_w += 1; cur_l = 0
            max_win = max(max_win, cur_w)
        else:
            cur_l += 1; cur_w = 0
            max_loss = max(max_loss, cur_l)

    if streak_type == "win":
        emoji = "🔥" * min(current_streak, 5)
        status = f"Серия побед: {current_streak} подряд! {emoji}"
    else:
        status = f"⚠️ Серия убытков: {current_streak} подряд — будь осторожен!"

    text = f"""🏆 *Серии сделок*

━━━━━━━━━━━━━━━━━━━━
{status}

📊 Рекорды:
• Лучшая серия побед: {max_win} подряд
• Худшая серия убытков: {max_loss} подряд
━━━━━━━━━━━━━━━━━━━━"""
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── TRADE CONVERSATION ───────────────────────────────────────────────────────
async def trade_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [["XAUUSD"]]
    await update.message.reply_text(
        "📝 *Новая сделка XAUUSD*\n\nПодтверди пару:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return PAIR

async def trade_pair(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["pair"] = "XAUUSD"
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
    await update.message.reply_text("Заметка (сессия, причина) или /skip:")
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
        "pair": "XAUUSD",
        "direction": d.get("direction"),
        "setup": d.get("setup"),
        "entry": d.get("entry"),
        "sl": d.get("sl"),
        "tp": d.get("tp"),
        "note": d.get("note",""),
        "result": None
    }
    trades = load_trades()

    # Проверка серии убытков
    closed = [t for t in trades if t.get("result") is not None]
    last3 = closed[-3:] if len(closed) >= 3 else []
    if len(last3) == 3 and all(t["result"] < 0 for t in last3):
        await update.message.reply_text(
            "🚨 *Внимание!*\n\nУ тебя 3 убытка подряд.\n"
            "По правилам риск-менеджмента — стоп торговли на сегодня.\n"
            "Сделка записана, но подумай дважды перед следующим входом.",
            parse_mode="Markdown"
        )

    trades.append(trade)
    save_trades(trades)

    keyboard = [
        ["📝 Новая сделка", "📊 Статистика"],
        ["✅ Чеклист", "📰 Новости"],
        ["⏰ Сессии", "📚 Обучение"],
        ["❓ Помощь"]
    ]
    summary = f"""✅ *Сделка сохранена!*

📌 XAUUSD | {trade['direction']} | {trade['setup']}
💰 Вход: `{trade['entry']}`
🛑 SL: `{trade['sl']}`
🎯 TP: `{trade['tp']}`
📅 {trade['date']}
📝 {trade['note'] or '—'}

_ID: {trade['id']}_
_/result {trade['id']} +150_"""
    await update.message.reply_text(summary, parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [["📝 Новая сделка","📊 Статистика"],["✅ Чеклист","📰 Новости"],["⏰ Сессии","📚 Обучение"]]
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
        pnl = float(args[1].replace("+",""))
    except ValueError:
        await update.message.reply_text("Неверный формат.")
        return
    trades = load_trades()
    for t in trades:
        if t["id"] == trade_id:
            t["result"] = pnl
            save_trades(trades)
            emoji = "✅" if pnl > 0 else "❌"
            await update.message.reply_text(f"{emoji} Результат: {'+'if pnl>0 else ''}${pnl:.2f}")
            return
    await update.message.reply_text("Сделка не найдена.")

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    trades = load_trades()
    if not trades:
        await update.message.reply_text("Сделок пока нет. Добавь первую через 📝 Новая сделка")
        return
    closed = [t for t in trades if t.get("result") is not None]
    total = len(trades)
    wins = len([t for t in closed if t["result"] > 0])
    losses = len([t for t in closed if t["result"] < 0])
    total_pnl = sum(t["result"] for t in closed)
    wr = round(wins/len(closed)*100) if closed else 0
    avg_win = sum(t["result"] for t in closed if t["result"]>0)/wins if wins else 0
    avg_loss = abs(sum(t["result"] for t in closed if t["result"]<0)/losses) if losses else 0
    rr = f"{avg_win/avg_loss:.2f}" if avg_loss else "—"
    ob = len([t for t in trades if "OB" in t.get("setup","")])
    fvg = len([t for t in trades if t.get("setup")=="FVG"])
    conf = len([t for t in trades if t.get("setup")=="OB + FVG"])

    text = f"""📊 *Статистика — XAUUSD SMC*

━━━━━━━━━━━━━━━━━━━━
📈 *Общее*
• Всего сделок: {total}
• Закрыто: {len(closed)}
• Побед: {wins} | Убытков: {losses}
• Winrate: {wr}%
• P&L: {'+'if total_pnl>=0 else ''}${total_pnl:.2f}
• Средний R:R: {rr}

━━━━━━━━━━━━━━━━━━━━
🎯 *По сетапам*
• OB: {ob} сделок
• FVG: {fvg} сделок
• OB+FVG: {conf} сделок

━━━━━━━━━━━━━━━━━━━━
_Открытых: {total-len(closed)}_"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = """❓ *Команды бота*

📝 Новая сделка — логировать по шагам
📊 Статистика — winrate и P&L
✅ Чеклист — 8 пунктов SMC
📰 Новости — USD новости на сегодня
⏰ Сессии — время торговли XAUUSD
📚 Обучение — курс SMC по темам

/learn <тема> — урок по теме
/risk <депозит> <риск%> <SL> — калькулятор
/drawdown — контроль убытков за день
/streak — серия побед/убытков
/result <ID> <сумма> — записать результат
/cancel — отменить действие"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📊 Статистика": await stats(update, ctx)
    elif text == "✅ Чеклист": await checklist_cmd(update, ctx)
    elif text == "📰 Новости": await news_info(update, ctx)
    elif text == "⏰ Сессии": await sessions_cmd(update, ctx)
    elif text == "📚 Обучение": await learn_menu(update, ctx)
    elif text == "❓ Помощь": await help_command(update, ctx)

# ─── SCHEDULER ────────────────────────────────────────────────────────────────
async def morning_news(bot, chat_id):
    news = fetch_news_today()
    await bot.send_message(chat_id=chat_id, text=format_news_message(news), parse_mode="Markdown")

async def remind_london(bot, chat_id):
    await bot.send_message(chat_id=chat_id, parse_mode="Markdown",
        text="🇬🇧 *Лондонская сессия через 30 минут!*\n\n⏰ 10:00–12:00 МСК\n\n"
             "• Отметь хай и лой азиатского диапазона\n"
             "• Обнови OB и FVG зоны на H4\n"
             "• Проверь новости дня (/news)\n\n✅ /checklist")

async def remind_ny(bot, chat_id):
    await bot.send_message(chat_id=chat_id, parse_mode="Markdown",
        text="🇺🇸 *Нью-Йоркская сессия через 30 минут!*\n\n⏰ 15:00–17:00 МСК\n\n"
             "• Проверь структуру после Лондона\n"
             "• Обнови зоны на H1/M15\n"
             "• Проверь данные США (возможны в 15:30)\n\n✅ /checklist")

async def weekly_report(bot, chat_id):
    trades = load_trades()
    from datetime import timedelta
    week_ago = (datetime.now(MOSCOW_TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
    week_trades = [t for t in trades if t.get("date","") >= week_ago]
    closed = [t for t in week_trades if t.get("result") is not None]
    wins = len([t for t in closed if t["result"] > 0])
    losses = len([t for t in closed if t["result"] < 0])
    pnl = sum(t["result"] for t in closed)
    wr = round(wins/len(closed)*100) if closed else 0
    text = f"""📊 *Еженедельный отчёт XAUUSD*

━━━━━━━━━━━━━━━━━━━━
Сделок за неделю: {len(week_trades)}
Закрыто: {len(closed)}
Побед: {wins} | Убытков: {losses}
Winrate: {wr}%
P&L: {'+'if pnl>=0 else ''}${pnl:.2f}
━━━━━━━━━━━━━━━━━━━━
_Хорошей недели! Торгуй по правилам 🥇_"""
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")

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
    app.add_handler(CommandHandler("checklist", checklist_cmd))
    app.add_handler(CommandHandler("news", news_info))
    app.add_handler(CommandHandler("sessions", sessions_cmd))
    app.add_handler(CommandHandler("learn", learn_topic))
    app.add_handler(CommandHandler("risk", risk_cmd))
    app.add_handler(CommandHandler("drawdown", drawdown_cmd))
    app.add_handler(CommandHandler("streak", streak_cmd))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("result", result_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex("^(📊|✅|📰|⏰|📚|❓)"),
        button_handler
    ))

    YOUR_CHAT_ID = int(os.getenv("CHAT_ID","0"))
    if YOUR_CHAT_ID:
        scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
        scheduler.add_job(morning_news, "cron", hour=9, minute=0, day_of_week="mon-fri", args=[app.bot, YOUR_CHAT_ID])
        scheduler.add_job(remind_london, "cron", hour=9, minute=30, day_of_week="mon-fri", args=[app.bot, YOUR_CHAT_ID])
        scheduler.add_job(remind_ny, "cron", hour=14, minute=30, day_of_week="mon-fri", args=[app.bot, YOUR_CHAT_ID])
        scheduler.add_job(weekly_report, "cron", day_of_week="sun", hour=20, minute=0, args=[app.bot, YOUR_CHAT_ID])
        scheduler.start()

    print("🤖 Загит SMC Bot v2 запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
