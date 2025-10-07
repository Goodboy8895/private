import os
import logging
from datetime import datetime, timedelta

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,        # <<< NEW
    InlineKeyboardMarkup         # <<< NEW
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,         # <<< NEW
    ContextTypes,
    filters,
)
from notion_client import Client
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# 1. Настройка окружения и клиентов
# -----------------------------------------------------------------------------
load_dotenv()  # читает .env рядом с кодом

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

# Карта периодов и дней — используем в разных местах
DAYS_MAP = {
    "сегодня": 1,
    "неделя": 7,
    "неделя2": 14,
    "неделя3": 21,
    "месяц": 31,
}

# -----------------------------------------------------------------------------
# Вспомогательная: собрать текст отчёта по количеству дней
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
        # безопасный разбор свойств
        cat_prop = props.get("Категория", {})
        title_arr = cat_prop.get("title", [])
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

# -----------------------------------------------------------------------------
# 2. /start — reply-клавиатура + кнопка “Меню”
# -----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("Сегодня"), KeyboardButton("Неделя"), KeyboardButton("Неделя2")],
        [KeyboardButton("Неделя3"), KeyboardButton("Месяц")],
        [KeyboardButton("📍 Меню")]  # <<< NEW: откроет inline-кнопки
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! Выберите период отчёта или отправьте расход в формате:\n"
        "<категория> <сумма>\n\n"
        "Например: еда 6400",
        reply_markup=markup,
    )

# -----------------------------------------------------------------------------
# 3. Inline “Меню” — показывает inline-кнопки
# -----------------------------------------------------------------------------
def make_inline_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Сегодня", callback_data="report:1"),
            InlineKeyboardButton("📈 Неделя", callback_data="report:7"),
        ],
        [
            InlineKeyboardButton("📆 Неделя2", callback_data="report:14"),
            InlineKeyboardButton("🗓 Неделя3", callback_data="report:21"),
        ],
        [
            InlineKeyboardButton("🗂 Месяц", callback_data="report:31"),
        ],
        # при желании сюда легко добавить “Экспорт CSV”, “Последние записи” и т.д.
    ])

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # вызывать из сообщения “📍 Меню”
    await update.message.reply_text("Выберите действие:", reply_markup=make_inline_menu())

# -----------------------------------------------------------------------------
# 4. Хэндлер для reply-кнопок отчёта
# -----------------------------------------------------------------------------
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower()
    if cmd not in DAYS_MAP:
        return
    days = DAYS_MAP[cmd]
    text = build_report_text(days)
    await update.message.reply_text(text)

# -----------------------------------------------------------------------------
# 5. Хэндлер inline-колбэков (кнопки под сообщением)
# -----------------------------------------------------------------------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # подтверждаем нажатие

    data = query.data or ""
    if data.startswith("report:"):
        try:
            days = int(data.split(":")[1])
        except Exception:
            days = 7
        text = build_report_text(days)
        await query.message.reply_text(text)

# -----------------------------------------------------------------------------
# 6. Хэндлер обычного сообщения — сохранение расхода
# -----------------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # Перехватываем кнопку “📍 Меню”
    if text == "📍 Меню":
        return await show_menu(update, context)

    parts = text.split()

    # ожидаем "<категория> <сумма>"
    if len(parts) != 2 or not parts[1].replace(".", "", 1).isdigit():
        await update.message.reply_text("⚠️ Неверный формат. Пример: еда 6400")
        return

    cat, amt_str = parts
    amount = float(amt_str)
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
        await update.message.reply_text(f"✅ Сохранено: {cat} {amount:.2f}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении в Notion: {e}")
        await update.message.reply_text("❌ Ошибка сохранения.")

# -----------------------------------------------------------------------------
# 7. Поднятие приложения и Webhook на Render
# -----------------------------------------------------------------------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # команды
    app.add_handler(CommandHandler("start", start))

    # reply-кнопки периодов
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.Regex("^(Сегодня|Неделя|Неделя2|Неделя3|Месяц)$"),
            report
        )
    )

    # inline-колбэки
    app.add_handler(CallbackQueryHandler(on_callback))  # <<< NEW

    # “📍 Меню” + ввод расходов
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}/{TELEGRAM_TOKEN}"
    )

if __name__ == "__main__":
    main()
