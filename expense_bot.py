import os
import json
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# Сохранение в Notion
async def save_to_notion(category, amount):
    url = "https://api.notion.com/v1/pages"
    today = datetime.now().strftime("%Y-%m-%d")

    data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Категория": {"title": [{"text": {"content": category}}]},
            "Сумма": {"number": float(amount)},
            "Дата": {"date": {"start": today}},
        }
    }

    response = requests.post(url, headers=NOTION_HEADERS, json=data)
    return response.status_code == 200

# Получение всех трат
def get_expenses(start_date, end_date):
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "Дата", "date": {"on_or_after": start_date}},
                {"property": "Дата", "date": {"on_or_before": end_date}}
            ]
        }
    }

    response = requests.post(url, headers=NOTION_HEADERS, json=payload)
    results = response.json().get("results", [])
    expenses = defaultdict(float)

    for entry in results:
        props = entry["properties"]
        category = props["Категория"]["title"][0]["plain_text"]
        amount = props["Сумма"]["number"]
        expenses[category] += amount

    return expenses

# Получение топ категорий
def get_top_categories(n=5):
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    all_expenses = get_expenses(start_date, end_date)
    sorted_categories = sorted(all_expenses.items(), key=lambda x: x[1], reverse=True)
    return [name for name, _ in sorted_categories[:n]]

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) == 2 and parts[1].isdigit():
        category, amount = parts
        success = await save_to_notion(category, amount)
        if success:
            await update.message.reply_text("✅ Сохранено в Notion.")
        else:
            await update.message.reply_text("❌ Не удалось сохранить.")
    else:
        await update.message.reply_text("⚠️ Неверный формат. Пример: еда 6400")

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [[KeyboardButton(cat)] for cat in get_top_categories()]
    markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    await update.message.reply_text("Выберите категорию или введите вручную:", reply_markup=markup)

# Анализ расходов
async def send_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text.lower()
    days_map = {
        "week": 7,
        "week2": 14,
        "week3": 21,
        "month": 31
    }

    days = days_map.get(command.replace("/", ""), 7)
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    summary = get_expenses(start_date, end_date)

    if not summary:
        await update.message.reply_text("Нет расходов за этот период.")
        return

    message = f"📊 Расходы за {days} дней:\n"
    for category, total in sorted(summary.items(), key=lambda x: x[1], reverse=True):
        message += f"• {category}: {total:.0f}₸\n"

    await update.message.reply_text(message)

# Webhook запуск на Render
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["week", "week2", "week3", "month"], send_summary))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}"
    )

if __name__ == "__main__":
    main()
