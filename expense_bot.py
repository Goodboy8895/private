import os
import logging
from datetime import datetime, timedelta

from dotenv import load_dotenv
from notion_client import Client
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─── ЗАГРУЗКА ПЕРЕМЕННЫХ ─────────────────────────────────────────────────────────
load_dotenv()  # берём переменные из .env

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DB_EXPENSES = os.getenv("NOTION_DB_EXPENSES")  # ID вашей таблицы «Expenses»

# ─── N O T I O N ────────────────────────────────────────────────────────────────
notion = Client(auth=NOTION_TOKEN)

# ─── ЛОГИ ───────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── ШАБЛОННЫЕ КНОПКИ ───────────────────────────────────────────────────────────
CATEGORIES_KEYBOARD = [
    ["Еда", "Транспорт"],
    ["Жилье", "Связь"],
    ["Развлечения", "Другое"],
]

# ─── /start ─────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Приветственное сообщение.
    Пользователь видит пример формата и кнопки с категориями.
    """
    await update.message.reply_text(
        "👋 Привет! Я бот учёта расходов.\n\n"
        "Просто отправь сообщение вида:\n"
        "<категория> <сумма>\n"
        "Например: Еда 6400\n\n"
        "Или выбери категорию кнопкой ниже:",
        reply_markup=ReplyKeyboardMarkup(CATEGORIES_KEYBOARD, resize_keyboard=True),
    )

# ─── ОТЧЁТЫ ────────────────────────────────────────────────────────────────────
async def send_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка команд /week, /week2, /week3, /month.
    Формирует запрос к Notion, собирает траты за нужный период и отправляет список + итог.
    """
    cmd = update.message.text.lstrip("/").lower()
    days_map = {"week": 7, "week2": 14, "week3": 21, "month": 31}
    days = days_map.get(cmd, 7)

    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    # Запрос в Notion
    query = {
        "database_id": NOTION_DB_EXPENSES,
        "filter": {
            "and": [
                {"property": "Дата", "date": {"on_or_after": start_date}},
                {"property": "Дата", "date": {"on_or_before": end_date}},
            ]
        },
    }
    res = notion.databases.query(**query)
    results = res.get("results", [])

    # Сбор статистики
    totals = {}
    grand_total = 0.0
    for page in results:
        props = page["properties"]
        cat = props["Категория"]["title"][0]["plain_text"]
        amt = props["Сумма"]["number"] or 0
        totals[cat] = totals.get(cat, 0) + amt
        grand_total += amt

    # Ответ
    if not totals:
        await update.message.reply_text("Нет расходов за этот период.")
        return

    text = [f"📊 Расходы за {days} дней:"]
    for cat, amt in sorted(totals.items(), key=lambda x: -x[1]):
        text.append(f"• {cat}: {amt:.0f}")
    text.append(f"\n💰 Итого: {grand_total:.0f}")

    await update.message.reply_text("\n".join(text), reply_markup=ReplyKeyboardRemove())

# ─── ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ──────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Вся логика «без дополнительных кнопок».
    Парсим «Категория Сумма» и создаём новую страницу в базе расходов.
    """
    text = update.message.text.strip()
    parts = text.split()

    # проверяем формат «X Y», где Y — число
    if len(parts) == 2 and parts[1].replace(".", "", 1).isdigit():
        category, amount_str = parts
        amount = float(amount_str)
        try:
            notion.pages.create(
                parent={"database_id": NOTION_DB_EXPENSES},
                properties={
                    "Категория": {"title": [{"text": {"content": category}}]},
                    "Сумма": {"number": amount},
                    "Дата": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
                },
            )
            await update.message.reply_text(
                f"✅ Сохранено: {category} {amount_str}",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception as e:
            logger.error(f"Не удалось сохранить в Notion: {e}")
            await update.message.reply_text("❌ Ошибка при сохранении.")
    else:
        await update.message.reply_text("⚠️ Неверный формат. Пример: еда 6400")

# ─── НЕИЗВЕСТНЫЕ КОМАНДЫ ───────────────────────────────────────────────────────
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❓ Неизвестная команда. Используйте /start")

# ─── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        CommandHandler(["week", "week2", "week3", "month"], send_summary)
    )
    # любые текстовые сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # всё остальное
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    app.run_polling()

if __name__ == "__main__":
    main()
