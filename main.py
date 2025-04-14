import os
import io
import json
import pytesseract
import datetime
import logging
from fastapi import FastAPI, Request
from PIL import Image
from google.oauth2 import service_account
import gspread
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# 📌 Configuration du logging
logging.basicConfig(level=logging.INFO)

# 🔐 Variables d'environnement (Render)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS = json.loads(os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"))

# 🌐 Instances globales
app_fastapi = FastAPI()
bot_app = None
bot_instance = None


# 📤 OCR simple
def extract_text_from_image(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes))
    text = pytesseract.image_to_string(image)
    return text


# 📥 Gestion des images Telegram
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        return

    photo = update.message.photo[-1]  # Meilleure qualité
    photo_file = await photo.get_file()
    image_bytes = await photo_file.download_as_bytearray()

    text = extract_text_from_image(image_bytes)

    await update.message.reply_text(f"🧾 OCR détecté :\n{text}")


# 🔁 Webhook Telegram
@app_fastapi.post("/webhook")
async def telegram_webhook(request: Request):
    json_data = await request.json()
    update = Update.de_json(json_data, bot_instance)
    await bot_app.process_update(update)
    return {"ok": True}


# 🚀 Démarrage FastAPI
@app_fastapi.on_event("startup")
async def startup_event():
    global bot_app, bot_instance

    # 🔐 Connexion Google Sheets
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDENTIALS)
    gspread_client = gspread.authorize(creds)

    # 🤖 Bot Telegram
    bot_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    bot_instance = bot_app.bot

    # 📷 Handler pour images
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_image))

    # 🌍 Définir le Webhook
    webhook_url = "https://bot-suivi-telegram.onrender.com/webhook"
    await bot_instance.set_webhook(webhook_url)

    # ✅ Lancer l'application bot
    await bot_app.initialize()
    logging.info("✅ Bot Telegram lancé avec succès via webhook.")


# 🧹 Arrêt FastAPI
@app_fastapi.on_event("shutdown")
async def shutdown_event():
    await bot_app.shutdown()
    logging.info("🛑 Bot arrêté proprement.")
