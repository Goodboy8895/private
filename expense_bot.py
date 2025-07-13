import os
import logging
from datetime import datetime, timedelta

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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
# (при желании можно также подключить базы для доходов и долгов)
# NOTION_DB_INCOME  = os.getenv("NOTION_DB_INCOME")
# NOTION_DB_DEBTS   = os.getenv("NOTION_DB_DEBTS")
# NOTION_DB_DEBTORS = os.getenv("NOTION_DB_DEBTORS")

# инициализируем http-клиент Notion
notion = Client(auth=NOTION_TOKEN)

# логирование в консоль
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%d"


# -----------------------------------------------------------------------------
# 2. Хэндлер команды /start
# -----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда /start: присылает клавиатуру с кнопками периодов отчета
    """
    keyboard = [
        [KeyboardButton("Сегодня"), KeyboardButton("Неделя"), KeyboardButton("Неделя2")],
        [KeyboardButton("Неделя3"), KeyboardButton("Месяц")],
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! Выберите период отчёта или отправьте расход в формате:\n"
        "<категория> <сумма>\n\n"
        "Например: еда 6400",
        reply_markup=markup,
    )


# -----------------------------------------------------------------------------
# 3. Хэндлер для кнопок отчёта
# -----------------------------------------------------------------------------
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    При нажатии на одну из кнопок: Сегодня, Неделя, Неделя2, Неделя3, Месяц
    собираем из Notion все записи за выбранный период и шлём сводку.
    """
    cmd = update.message.text.lower()
    days_map = {
        "сегодня": 1,
        "неделя": 7,
        "неделя2": 14,
        "неделя3": 21,
        "месяц": 31,
    }

    if cmd not in days_map:
        return  # не наша кнопка

    days = days_map[cmd]
    end = datetime.now()
    start = end - timedelta(days=days - 1)

    # запрос к Notion
    query = {
        "filter": {
            "and": [
                {"property": "Дата", "date": {"on_or_after": start.strftime(DATE_FORMAT)}},
                {"property": "Дата", "date": {"on_or_before": end.strftime(DATE_FORMAT)}},
            ]
        }
    }
    result = notion.databases.query(database_id=NOTION_DB_EXPENSES, **query)

    # собираем суммы по категориям
    totals = {}
    for page in result.get("results", []):
        props = page["properties"]
        cat = props["Категория"]["title"][0]["plain_text"]
        val = props["Сумма"]["number"]
        totals[cat] = totals.get(cat, 0) + val

    if not totals:
        await update.message.reply_text("Нет расходов за этот период.")
        return

    # формируем текст отчёта
    text = f"📊 Расходы за {days} дн:\n"
    total_sum = 0
    for cat, val in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        text += f"• {cat}: {val:.2f}\n"
        total_sum += val
    text += f"\n🔹 Итого: {total_sum:.2f}"

    await update.message.reply_text(text)


# -----------------------------------------------------------------------------
# 4. Хэндлер обычного сообщения — сохранение расхода
# -----------------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Ожидаем текст вида "<категория> <сумма>". 
    Сохраняем новую страницу в БД расходов Notion.
    """
    text = update.message.text.strip()
    parts = text.split()

    # проверяем формат
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
# 5. Поднятие приложения и Webhook на Render
# -----------------------------------------------------------------------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.Regex("^(Сегодня|Неделя|Неделя2|Неделя3|Месяц)$"),
            report
        )
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # запускаем webhook-сервер
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        url_path=TELEGRAM_TOKEN,  # 
        webhook_url=f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}/{TELEGRAM_TOKEN}"
    )


if __name__ == "__main__":
    main()
