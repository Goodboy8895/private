import os
import logging
from datetime import datetime, timedelta

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from notion_client import Client
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# 1) Окружение и клиенты
# -----------------------------------------------------------------------------
load_dotenv()

TELEGRAM_TOKEN       = os.getenv("TELEGRAM_TOKEN")
NOTION_TOKEN         = os.getenv("NOTION_TOKEN")
NOTION_DB_EXPENSES   = os.getenv("NOTION_DB_EXPENSES")

notion = Client(auth=NOTION_TOKEN)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%d"

# -----------------------------------------------------------------------------
# 2) Справочники Классов → Подкатегорий (из твоего списка)
# -----------------------------------------------------------------------------
EXPENSES = {
    "🏠 Быт и жильё": [
        "Аренда / ипотека",
        "Коммунальные услуги",
        "Интернет и ТВ",
        "Хозяйственные товары",
        "Мебель и техника",
    ],
    "🚗 Транспорт и авто": [
        "Топливо / зарядка",
        "Обслуживание / ремонт",
        "Страховка / налоги",
        "Штрафы / пошлины",
        "Такси / транспорт",
    ],
    "🍽 Еда и продукты": [
        "Продукты",
        "Кафе и рестораны",
        "Доставка еды",
        "Алкоголь / сигареты",
    ],
    "🧍‍♂️ Здоровье и уход": [
        "Медицина",
        "Фитнес / спорт",
        "Уход за собой",
    ],
    "👕 Одежда и аксессуары": [
        "Одежда и обувь",
        "Аксессуары",
        "Ремонт одежды",
    ],
    "💻 Техника и электроника": [
        "Гаджеты",
        "Подписки / ПО",
        "Аксессуары",
    ],
    "👨‍👩‍👧 Семья и дети": [
        "Подарки",
        "Образование / кружки",
        "Домашние животные",
    ],
    "🌏 Путешествия и досуг": [
        "Билеты и проживание",
        "Развлечения",
        "Отпуск / туризм",
    ],
    "💳 Финансы и обязательства": [
        "Долги / кредиты",
        "Переводы / помощь",
        "Инвестиции / крипта",
    ],
    "🧠 Саморазвитие и образование": [
        "Книги / курсы",
        "Языки / репетиторы",
    ],
    "💬 Связь и коммуникации": [
        "Мобильная связь",
        "Соцсети / реклама",
        "Тех. услуги",
    ],
    "🧾 Прочее": [
        "Штрафы / комиссии",
        "Неожиданные расходы",
        "Благотворительность",
    ],
}

INCOME = {
    "👔 Основной доход": [
        "Зарплата",
        "Подработка",
        "Сдельная оплата",
    ],
    "👨‍👩‍👧 Личные поступления": [
        "Переводы",
        "Подарки / помощь",
        "Возвраты долгов",
    ],
    "✈️ Прочие поступления": [
        "Продажа вещей",
        "Возврат налогов",
        "Прочее",
    ],
}

# Периоды для отчётов
DAYS_MAP = {
    "сегодня": 1,
    "неделя": 7,
    "неделя2": 14,
    "неделя3": 21,
    "месяц": 31,
}

# -----------------------------------------------------------------------------
# 3) Вспомогательные функции
# -----------------------------------------------------------------------------
def build_report_text(days: int) -> str:
    end = datetime.now()
    start = end - timedelta(days=days - 1)

    query = {
        "filter": {
            "and": [
                {"property": "Дата", "date": {"on_or_after": start.strftime(DATE_FORMAT)}},
                {"property": "Дата", "date": {"on_or_before": end.strftime(DATE_FORMAT)}},
            ]
        }
    }
    result = notion.databases.query(database_id=NOTION_DB_EXPENSES, **query)

    totals = {}
    for page in result.get("results", []):
        props = page.get("properties", {})
        title_arr = (props.get("Категория", {}) or {}).get("title", [])
        cat = title_arr[0]["plain_text"] if title_arr else "Без категории"
        val = (props.get("Сумма") or {}).get("number") or 0
        totals[cat] = totals.get(cat, 0) + (val or 0)

    if not totals:
        return "Нет расходов за этот период."

    lines = [f"📊 Расходы за {days} дн:"]
    total_sum = 0
    for cat, val in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"• {cat}: {val:.2f}")
        total_sum += val
    lines.append(f"\n🔹 Итого: {total_sum:.2f}")
    return "\n".join(lines)

def chunk_buttons(labels, prefix, per_row=2):
    """
    Разбивает список подписей на Inline-кнопки с данным префиксом callback_data.
    prefix: 'type:expense', 'class:expense:...', 'sub:expense:...'
    """
    rows = []
    row = []
    for i, label in enumerate(labels):
        row.append(InlineKeyboardButton(label, callback_data=f"{prefix}:{i}"))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows

# -----------------------------------------------------------------------------
# 4) /start — reply-меню + кнопка Меню
# -----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("Сегодня"), KeyboardButton("Неделя"), KeyboardButton("Неделя2")],
        [KeyboardButton("Неделя3"), KeyboardButton("Месяц")],
        [KeyboardButton("📍 Меню")]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! Выбери период отчёта или отправь расход в формате:\n"
        "<категория> <сумма>\n\n"
        "Напр.: еда 6400",
        reply_markup=markup,
    )

# -----------------------------------------------------------------------------
# 5) Inline-меню: верхний уровень (тип записи)
# -----------------------------------------------------------------------------
def make_root_inline_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖ Расход", callback_data="pick:type:expense"),
            InlineKeyboardButton("➕ Доход",  callback_data="pick:type:income"),
        ],
        [
            InlineKeyboardButton("📊 Отчёты: Сегодня", callback_data="report:1"),
            InlineKeyboardButton("Неделя", callback_data="report:7"),
        ],
        [
            InlineKeyboardButton("Неделя2", callback_data="report:14"),
            InlineKeyboardButton("Неделя3", callback_data="report:21"),
        ],
        [
            InlineKeyboardButton("Месяц", callback_data="report:31"),
        ],
    ])

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выбери действие:", reply_markup=make_root_inline_menu())

# -----------------------------------------------------------------------------
# 6) Выбор Класса (после выбора типа)
# -----------------------------------------------------------------------------
def make_class_menu(kind: str) -> InlineKeyboardMarkup:
    # kind: 'expense' | 'income'
    classes = list(EXPENSES.keys()) if kind == "expense" else list(INCOME.keys())
    rows = chunk_buttons(classes, prefix=f"pick:class:{kind}", per_row=1)  # по одному в строке — удобнее
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:root")])
    return InlineKeyboardMarkup(rows)

# -----------------------------------------------------------------------------
# 7) Выбор Подкатегории (после выбора класса)
# -----------------------------------------------------------------------------
def make_subcategory_menu(kind: str, class_idx: int, user_data: dict) -> InlineKeyboardMarkup:
    classes = list(EXPENSES.keys()) if kind == "expense" else list(INCOME.keys())
    # запомним текущий класс текстом
    chosen_class = classes[class_idx]
    user_data["chosen_class"] = chosen_class
    user_data["chosen_kind"] = kind

    subs = EXPENSES[chosen_class] if kind == "expense" else INCOME[chosen_class]
    rows = chunk_buttons(subs, prefix=f"pick:sub:{kind}:{class_idx}", per_row=1)
    rows.append([InlineKeyboardButton("⬅️ Назад к классам", callback_data=f"back:class:{kind}")])
    return InlineKeyboardMarkup(rows)

# -----------------------------------------------------------------------------
# 8) Хэндлер отчётов (reply-кнопки)
# -----------------------------------------------------------------------------
async def report_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = (update.message.text or "").lower()
    if cmd not in DAYS_MAP:
        return
    days = DAYS_MAP[cmd]
    text = build_report_text(days)
    await update.message.reply_text(text)

# -----------------------------------------------------------------------------
# 9) Хэндлер callback'ов
# -----------------------------------------------------------------------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    await q.answer()

    # Отчёты (из inline)
    if data.startswith("report:"):
        try:
            days = int(data.split(":")[1])
        except Exception:
            days = 7
        text = build_report_text(days)
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

    # Выбор типа
    if data.startswith("pick:type:"):
        _, _, kind = data.split(":")
        context.user_data.clear()
        context.user_data["chosen_kind"] = kind
        return await q.message.edit_text(
            f"Выбери КЛАСС ({'Расходы' if kind=='expense' else 'Доходы'}):",
            reply_markup=make_class_menu(kind)
        )

    # Выбор класса
    if data.startswith("pick:class:"):
        # pick:class:<kind>:<idx>
        _, _, kind, idx = data.split(":")
        class_idx = int(idx)
        return await q.message.edit_text(
            "Выбери ПОДКАТЕГОРИЮ:",
            reply_markup=make_subcategory_menu(kind, class_idx, context.user_data)
        )

    # Выбор подкатегории
    if data.startswith("pick:sub:"):
        # pick:sub:<kind>:<class_idx>:<sub_idx>
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
            f"Отправь сумму (напр.: 6400). Можно ввести числом, без категории.",
        )

# -----------------------------------------------------------------------------
# 10) Сохранение расходов по старой схеме и ввод суммы после выбора меню
# -----------------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # Кнопка открыть меню
    if text == "📍 Меню":
        return await show_menu(update, context)

    # Если пользователь только что выбрал подкатегорию — ждём сумму
    if context.user_data.get("await_amount"):
        amount_str = text.replace(",", ".")
        if not amount_str.replace(".", "", 1).isdigit():
            return await update.message.reply_text("⚠️ Введи сумму числом, напр.: 6400")

        amount = float(amount_str)
        kind = context.user_data.get("chosen_kind")
        chosen_class = context.user_data.get("chosen_class")
        chosen_sub = context.user_data.get("chosen_sub")
        category_text = f"{chosen_class} — {chosen_sub}"

        today = datetime.now().strftime(DATE_FORMAT)
        # Сохраняем как расход в текущую БД (у тебя одна БД расходов).
        # Для доходов можно позже завести отдельную БД, пока кладём в эту же — как “Категория” с префиксом.
        if kind == "income":
            category_text = f"[Доход] {category_text}"
        else:
            category_text = f"{category_text}"

        try:
            notion.pages.create(
                parent={"database_id": NOTION_DB_EXPENSES},
                properties={
                    "Категория": {"title": [{"text": {"content": category_text}}]},
                    "Сумма": {"number": amount},
                    "Дата": {"date": {"start": today}},
                }
            )
            await update.message.reply_text(
                f"✅ Сохранено: {category_text} — {amount:.2f}"
            )
        except Exception as e:
            logger.error(f"Ошибка при сохранении в Notion: {e}")
            await update.message.reply_text("❌ Ошибка сохранения.")
        finally:
            # Сброс ожидания суммы
            context.user_data.pop("await_amount", None)

        return

    # Старый формат: "<категория> <сумма>"
    parts = text.split()
    if len(parts) == 2 and parts[1].replace(".", "", 1).isdigit():
        cat, amt_str = parts
        amount = float(amt_str.replace(",", "."))
        today = datetime.now().strftime(DATE_FORMAT)
        try:
            notion.pages.create(
                parent={"database_id": NOTION_DB_EXPENSES},
                properties={
                    "Категория": {"title": [{"text": {"content": cat}}]},
                    "Сумма": {"number": amount},
                    "Дата": {"date": {"start": today}},
                }
            )
            return await update.message.reply_text(f"✅ Сохранено: {cat} {amount:.2f}")
        except Exception as e:
            logger.error(f"Ошибка при сохранении в Notion: {e}")
            return await update.message.reply_text("❌ Ошибка сохранения.")

    # Иначе подсказка
    await update.message.reply_text(
        "Отправь расход в формате: <категория> <сумма> (напр.: еда 6400)\n"
        "или нажми «📍 Меню» для выбора из списка."
    )

# -----------------------------------------------------------------------------
# 11) Регистрация хэндлеров и запуск
# -----------------------------------------------------------------------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))

    # Reply-кнопки периодов
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.Regex("^(Сегодня|Неделя|Неделя2|Неделя3|Месяц)$"),
            report_reply
        )
    )

    # Inline-колбэки (меню / выборы)
    app.add_handler(CallbackQueryHandler(on_callback))

    # Ввод суммы / быстрые сохранения и т.п.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Webhook (Render)
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}/{TELEGRAM_TOKEN}"
    )

if __name__ == "__main__":
    main()
