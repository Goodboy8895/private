import os
import logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

# -----------------------------------------------------------------------------
# ENV & Clients
# -----------------------------------------------------------------------------
load_dotenv()
TELEGRAM_TOKEN       = os.getenv("TELEGRAM_TOKEN")
MONGODB_URI          = os.getenv("MONGODB_URI")
MONGO_DB_NAME        = os.getenv("MONGO_DB_NAME", "finance")
TZ_NAME              = os.getenv("TZ", "Asia/Seoul")
TZ                   = ZoneInfo(TZ_NAME)
USE_POLLING          = os.getenv("USE_POLLING", "0") == "1"  # локально удобно

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("dmk-bot")

# Mongo
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client[MONGO_DB_NAME]
tx: Collection = db["transactions"]

# индексы
tx.create_index([("user_id", ASCENDING), ("date", ASCENDING)])
tx.create_index([("user_id", ASCENDING), ("kind", ASCENDING), ("date", ASCENDING)])
tx.create_index([("user_id", ASCENDING), ("category_text", ASCENDING), ("date", DESCENDING)])

DATE_FORMAT = "%Y-%m-%d"

# -----------------------------------------------------------------------------
# Категории
# -----------------------------------------------------------------------------
EXPENSES: Dict[str, List[str]] = {
    "🏠 Быт и жильё": [
        "Аренда / ипотека", "Коммунальные услуги", "Интернет и ТВ",
        "Хозяйственные товары", "Мебель и техника",
    ],
    "🚗 Транспорт и авто": [
        "Топливо / зарядка", "Обслуживание / ремонт", "Страховка / налоги",
        "Штрафы / пошлины", "Такси / транспорт",
    ],
    "🍽 Еда и продукты": ["Продукты", "Кафе и рестораны", "Доставка еды", "Алкоголь / сигареты"],
    "🧍‍♂️ Здоровье и уход": ["Медицина", "Фитнес / спорт", "Уход за собой"],
    "👕 Одежда и аксессуары": ["Одежда и обувь", "Аксессуары", "Ремонт одежды"],
    "💻 Техника и электроника": ["Гаджеты", "Подписки / ПО", "Аксессуары"],
    "👨‍👩‍👧 Семья и дети": ["Подарки", "Образование / кружки", "Домашние животные"],
    "🌏 Путешествия и досуг": ["Билеты и проживание", "Развлечения", "Отпуск / туризм"],
    "💳 Финансы и обязательства": ["Долги / кредиты", "Переводы / помощь", "Инвестиции / крипта"],
    "🧠 Саморазвитие и образование": ["Книги / курсы", "Языки / репетиторы"],
    "💬 Связь и коммуникации": ["Мобильная связь", "Соцсети / реклама", "Тех. услуги"],
    "🧾 Прочее": ["Штрафы / комиссии", "Неожиданные расходы", "Благотворительность"],
}

INCOME: Dict[str, List[str]] = {
    "👔 Основной доход": ["Зарплата", "Подработка", "Сдельная оплата"],
    "👨‍👩‍👧 Личные поступления": ["Переводы", "Подарки / помощь", "Возвраты долгов"],
    "✈️ Прочие поступления": ["Продажа вещей", "Возврат налогов", "Прочее"],
}

DAYS_MAP = {"сегодня": 1, "неделя": 7, "неделя2": 14, "неделя3": 21, "месяц": 31}

# -----------------------------------------------------------------------------
# Helpers: dates & formatting
# -----------------------------------------------------------------------------
def today_local() -> date:
    return datetime.now(TZ).date()

def parse_date_token(tok: Optional[str]) -> Optional[date]:
    if not tok:
        return None
    t = tok.strip().lower()
    if t in ("вчера", "yesterday"):
        return today_local() - timedelta(days=1)
    try:
        return datetime.strptime(t, "%Y-%m-%d").date()
    except Exception:
        return None

def chunk_buttons(labels, prefix, per_row=1):
    rows, row = [], []
    for i, label in enumerate(labels):
        row.append(InlineKeyboardButton(label, callback_data=f"{prefix}:{i}"))
        if len(row) == per_row:
            rows.append(row); row = []
    if row: rows.append(row)
    return rows

# -----------------------------------------------------------------------------
# Reports
# -----------------------------------------------------------------------------
def aggregate_by_category(user_id: int, start_date: date, end_date: date, kind: Optional[str] = None):
    match: Dict = {
        "user_id": user_id,
        "date": {"$gte": start_date.strftime(DATE_FORMAT), "$lte": end_date.strftime(DATE_FORMAT)},
    }
    if kind:
        match["kind"] = kind

    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$category_text", "total": {"$sum": "$amount"}}},
        {"$sort": {"total": -1}},
    ]
    return list(tx.aggregate(pipeline))

def build_report_text_by_kind(days: int, user_id: int, kind: str) -> str:
    """kind: 'income' | 'expense'"""
    end_date = today_local()
    start_date = end_date - timedelta(days=days - 1)

    data = aggregate_by_category(user_id, start_date, end_date, kind=kind)
    if not data:
        return f"Нет записей ({'Доходы' if kind=='income' else 'Расходы'}) за период {start_date} — {end_date}."

    lines = [f"📊 {('Доходы' if kind=='income' else 'Расходы')} за {days} дн. ({start_date} — {end_date}):"]
    total = 0.0
    for item in data:
        lines.append(f"• {item['_id']}: {item['total']:.2f}")
        total += float(item["total"])
    lines.append(f"\nИтого: {total:.2f}")
    return "\n".join(lines)

# -----------------------------------------------------------------------------
# Inline menus
# -----------------------------------------------------------------------------
def make_root_inline_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        # Ввод записей
        [InlineKeyboardButton("➖ Расход", callback_data="pick:type:expense"),
         InlineKeyboardButton("➕ Доход",  callback_data="pick:type:income")],
        # ОТЧЁТЫ — отдельно по видам
        [InlineKeyboardButton("📈 Отчёт: Доходы",  callback_data="rkind:income"),
         InlineKeyboardButton("📉 Отчёт: Расходы", callback_data="rkind:expense")],
    ])

def make_period_menu_for_kind(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Сегодня", callback_data=f"rund:{kind}:1"),
         InlineKeyboardButton("Неделя",  callback_data=f"rund:{kind}:7")],
        [InlineKeyboardButton("Неделя2", callback_data=f"rund:{kind}:14"),
         InlineKeyboardButton("Неделя3", callback_data=f"rund:{kind}:21")],
        [InlineKeyboardButton("Месяц",    callback_data=f"rund:{kind}:31")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back:root")],
    ])

def make_class_menu(kind: str) -> InlineKeyboardMarkup:
    classes = list(EXPENSES.keys()) if kind == "expense" else list(INCOME.keys())
    rows = chunk_buttons(classes, prefix=f"pick:class:{kind}", per_row=1)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:root")])
    return InlineKeyboardMarkup(rows)

def make_subcategory_menu(kind: str, class_idx: int, user_data: dict) -> InlineKeyboardMarkup:
    classes = list(EXPENSES.keys()) if kind == "expense" else list(INCOME.keys())
    chosen_class = classes[class_idx]
    user_data["chosen_class"] = chosen_class
    user_data["chosen_kind"] = kind
    subs = EXPENSES[chosen_class] if kind == "expense" else INCOME[chosen_class]
    rows = chunk_buttons(subs, prefix=f"pick:sub:{kind}:{class_idx}", per_row=1)
    rows.append([InlineKeyboardButton("⬅️ Назад к классам", callback_data=f"back:class:{kind}")])
    return InlineKeyboardMarkup(rows)

# -----------------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("Сегодня"), KeyboardButton("Неделя"), KeyboardButton("Неделя2")],
        [KeyboardButton("Неделя3"), KeyboardButton("Месяц")],
        [KeyboardButton("Доходы"), KeyboardButton("Расходы")],
        [KeyboardButton("📍 Меню")],
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! Запись идёт по календарной дате отправки (TZ: "
        f"{TZ.key}). Можно добавить дату: `вчера` или `YYYY-MM-DD`.\n"
        "Примеры: `еда 6400`, `еда 6400 вчера`, `еда 6400 2025-10-07`.",
        reply_markup=markup,
    )

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выбери действие:", reply_markup=make_root_inline_menu())

async def report_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = (update.message.text or "").lower()

    # быстрый вход в вид отчёта
    if cmd in ("доходы", "расходы"):
        kind = "income" if cmd == "доходы" else "expense"
        return await update.message.reply_text(
            f"Выбери период — {('Доходы' if kind=='income' else 'Расходы')}:",
            reply_markup=make_period_menu_for_kind(kind)
        )

    # если жмут период без выбора вида — спросим вид
    if cmd in DAYS_MAP:
        return await update.message.reply_text(
            "Что показать?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📈 Доходы", callback_data="rkind:income"),
                 InlineKeyboardButton("📉 Расходы", callback_data="rkind:expense")],
            ])
        )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    await q.answer()

    # Ветвь отчётов по виду
    if data.startswith("rkind:"):
        kind = data.split(":")[1]  # income | expense
        return await q.message.edit_text(
            f"Выбери период — {('Доходы' if kind=='income' else 'Расходы')}:",
            reply_markup=make_period_menu_for_kind(kind)
        )

    if data.startswith("rund:"):
        # rund:<kind>:<days>
        _, kind, days_str = data.split(":")
        try:
            days = int(days_str)
        except Exception:
            days = 7
        user_id = q.from_user.id
        text = build_report_text_by_kind(days, user_id, kind)
        return await q.message.reply_text(text)

    # Назад
    if data == "back:root":
        return await q.message.edit_text("Меню:", reply_markup=make_root_inline_menu())

    if data.startswith("back:class:"):
        kind = data.split(":")[2]
        return await q.message.edit_text(
            f"Выбери КЛАСС ({'Расходы' if kind=='expense' else 'Доходы'}):",
            reply_markup=make_class_menu(kind)
        )

    # Ввод через меню (тип → класс → подкатегория)
    if data.startswith("pick:type:"):
        _, _, kind = data.split(":")
        context.user_data.clear()
        context.user_data["chosen_kind"] = kind
        return await q.message.edit_text(
            f"Выбери КЛАСС ({'Расходы' if kind=='expense' else 'Доходы'}):",
            reply_markup=make_class_menu(kind)
        )

    if data.startswith("pick:class:"):
        _, _, kind, idx = data.split(":")
        class_idx = int(idx)
        return await q.message.edit_text(
            "Выбери ПОДКАТЕГОРИЮ:",
            reply_markup=make_subcategory_menu(kind, class_idx, context.user_data)
        )

    if data.startswith("pick:sub:"):
        _, _, kind, class_idx, sub_idx = data.split(":")
        class_idx, sub_idx = int(class_idx), int(sub_idx)

        classes = list(EXPENSES.keys()) if kind == "expense" else list(INCOME.keys())
        chosen_class = classes[class_idx]
        subs = EXPENSES[chosen_class] if kind == "expense" else INCOME[chosen_class]
        chosen_sub = subs[sub_idx]

        context.user_data["chosen_kind"] = kind
        context.user_data["chosen_class"] = chosen_class
        context.user_data["chosen_sub"] = chosen_sub
        context.user_data["await_amount"] = True

        return await q.message.reply_text(
            f"Выбрано: {('Расход' if kind=='expense' else 'Доход')} → {chosen_class} → {chosen_sub}\n"
            f"Введи сумму и (опционально) дату: `6400`, `6400 вчера`, `6400 2025-10-07`",
        )

# --- DB save ---
def save_tx(*, user_id: int, kind: str, category_text: str,
            amount: float, d: date,
            chosen_class: Optional[str] = None,
            chosen_sub: Optional[str] = None):
    doc = {
        "user_id": user_id,
        "kind": kind,  # "expense" | "income"
        "class": chosen_class,
        "subcategory": chosen_sub,
        "category_text": category_text,
        "amount": float(amount),
        "date": d.strftime(DATE_FORMAT),     # календарная дата
        "created_at": datetime.now(TZ),
    }
    tx.insert_one(doc)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # Открыть меню
    if text == "📍 Меню":
        return await show_menu(update, context)

    user_id = update.effective_user.id

    # Если ожидали сумму после выбора из меню
    if context.user_data.get("await_amount"):
        parts = text.replace(",", ".").split()
        if not parts:
            return await update.message.reply_text("⚠️ Введи сумму (и дату опционально): 6400 | 6400 вчера | 6400 2025-10-07")

        amount_str = parts[0]
        if not amount_str.replace(".", "", 1).isdigit():
            return await update.message.reply_text("⚠️ Сумма должна быть числом. Пример: 6400")

        amount = float(amount_str)
        chosen_date = parse_date_token(parts[1]) if len(parts) >= 2 else today_local()

        kind = context.user_data.get("chosen_kind") or "expense"
        chosen_class = context.user_data.get("chosen_class")
        chosen_sub = context.user_data.get("chosen_sub")
        category_text = f"{chosen_class} — {chosen_sub}"

        try:
            save_tx(
                user_id=user_id,
                kind=kind,
                category_text=category_text,
                amount=amount,
                d=chosen_date,
                chosen_class=chosen_class,
                chosen_sub=chosen_sub,
            )
            await update.message.reply_text(
                f"✅ Сохранено: {('Доход' if kind=='income' else 'Расход')} — {category_text} — {amount:.2f} от {chosen_date.strftime(DATE_FORMAT)}"
            )
        except PyMongoError as e:
            logger.error(f"Mongo error: {e}")
            await update.message.reply_text("❌ Ошибка сохранения.")
        finally:
            context.user_data.pop("await_amount", None)
        return

    # Быстрый ввод: "<категория> <сумма> [дата]" — считаем как РАСХОД по умолчанию
    parts = text.replace(",", ".").split()
    if len(parts) >= 2 and parts[1].replace(".", "", 1).isdigit():
        cat = parts[0]
        amount = float(parts[1])
        chosen_date = parse_date_token(parts[2]) if len(parts) >= 3 else today_local()
        try:
            save_tx(
                user_id=user_id,
                kind="expense",
                category_text=cat,
                amount=amount,
                d=chosen_date,
            )
            return await update.message.reply_text(
                f"✅ Сохранено: {cat} {amount:.2f} от {chosen_date.strftime(DATE_FORMAT)}"
            )
        except PyMongoError as e:
            logger.error(f"Mongo error: {e}")
            return await update.message.reply_text("❌ Ошибка сохранения.")

    # Иначе подсказка
    await update.message.reply_text(
        "Отправь запись:\n"
        "• Из меню: тип → класс → подкатегория → введи `сумма [дата]`\n"
        "• Быстрый расход: `<категория> <сумма> [дата]`\n\n"
        "Чтобы получить отчёт, нажми «Доходы» или «Расходы» и выбери период."
    )

# -----------------------------------------------------------------------------
# Bootstrap
# -----------------------------------------------------------------------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # Reply-кнопки — ведут в выбор вида/периода
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.Regex("^(Сегодня|Неделя|Неделя2|Неделя3|Месяц|Доходы|Расходы|📍 Меню)$"),
            report_reply
        )
    )

    # Inline callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # Ввод/сохранение
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if USE_POLLING:
        # Удобно для локалки
        logger.info("Starting POLLING...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        # Webhook (для Render/VPS с доменом и HTTPS)
        logger.info("Starting WEBHOOK...")
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", "10000")),
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}/{TELEGRAM_TOKEN}"
        )

if __name__ == "__main__":
    main()

