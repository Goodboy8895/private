import os
import logging
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InputFile, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import pandas as pd
from collections import Counter

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_top_categories(n=5):
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    thirty_days_ago = datetime.today() - timedelta(days=30)
    payload = {
        "filter": {
            "property": "Date",
            "date": {"on_or_after": thirty_days_ago.strftime("%Y-%m-%d")}
        }
    }
    response = requests.post(url, headers=NOTION_HEADERS, json=payload)
    results = response.json().get("results", [])

    counter = Counter()
    for item in results:
        title = item.get("properties", {}).get("Expense Item", {}).get("title", [{}])[0].get("text", {}).get("content", "")
        if title:
            counter[title] += 1

    return [cat for cat, _ in counter.most_common(n)]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top_cats = get_top_categories()
    keyboard = [["/week", "/week2"], ["/week3", "/month"]] + [[KeyboardButton(cat)] for cat in top_cats]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет!\n\n" +
        "📌 Просто напиши: категория сумма (например: еда 6500)\n\n" +
        "📊 Или выбери одну из команд ниже для анализа:",
        reply_markup=reply_markup
    )

def get_expenses(start_date: datetime, end_date: datetime):
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "Date", "date": {"on_or_after": start_date.strftime("%Y-%m-%d")}},
                {"property": "Date", "date": {"on_or_before": end_date.strftime("%Y-%m-%d")}}
            ]
        }
    }
    response = requests.post(url, headers=NOTION_HEADERS, json=payload)
    results = response.json().get("results", [])

    data = {}
    for item in results:
        props = item.get("properties", {})
        category = props.get("Expense Item", {}).get("title", [{}])[0].get("text", {}).get("content", "")
        amount = props.get("Amount", {}).get("number", 0)
        data[category] = data.get(category, 0) + amount
    return data

def get_date_range(command):
    today = datetime.today()
    if command == "week":
        return today - timedelta(days=7), today
    elif command == "week2":
        return today - timedelta(days=14), today
    elif command == "week3":
        return today - timedelta(days=21), today
    elif command == "month":
        return today.replace(day=1), today
    else:
        return today, today

async def send_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text.replace("/", "").split("@")[0]
    start_date, end_date = get_date_range(command)
    data = get_expenses(start_date, end_date)

    if not data:
        await update.message.reply_text("Нет расходов за указанный период.")
        return

    summary = f"📊 Расходы с {start_date.date()} по {end_date.date()}:\n"
    for cat, amt in data.items():
        summary += f"{cat}: {amt} ₩\n"
    await update.message.reply_text(summary)

    df = pd.DataFrame(list(data.items()), columns=["Категория", "Сумма"])
    file_path = "/tmp/summary.xlsx"
    df.to_excel(file_path, index=False)
    await update.message.reply_document(InputFile(file_path, filename="summary.xlsx"))

def get_last_entry_for_category(category):
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Expense Item",
            "title": {"equals": category}
        },
        "page_size": 1,
        "sorts": [{"timestamp": "created_time", "direction": "descending"}]
    }
    response = requests.post(url, headers=NOTION_HEADERS, json=payload)
    results = response.json().get("results", [])

    if results:
        item = results[0]
        page_id = item["id"]
        amount = item["properties"]["Amount"].get("number", 0)
        return page_id, amount
    return None, None

def update_notion_page(page_id, new_amount):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    data = {
        "properties": {
            "Amount": {"number": new_amount},
            "Date": {"date": {"start": datetime.today().strftime("%Y-%m-%d")}}
        }
    }
    response = requests.patch(url, headers=NOTION_HEADERS, json=data)
    return response.status_code == 200

def create_new_notion_entry(category, amount):
    url = "https://api.notion.com/v1/pages"
    data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Expense Item": {"title": [{"text": {"content": category}}]},
            "Amount": {"number": amount},
            "Date": {"date": {"start": datetime.today().strftime("%Y-%m-%d")}}
        }
    }
    response = requests.post(url, headers=NOTION_HEADERS, json=data)
    return response.status_code == 200

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        category, amount = text.split()
        amount = int(amount)
        page_id, previous_amount = get_last_entry_for_category(category)

        if page_id:
            success = update_notion_page(page_id, previous_amount + amount)
        else:
            success = create_new_notion_entry(category, amount)

        if success:
            await update.message.reply_text("✅ Сохранено в Notion.")
        else:
            await update.message.reply_text("❌ Не удалось сохранить.")
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("⚠️ Неверный формат. Пример: еда 6400")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["week", "week2", "week3", "month"], send_summary))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    main()
