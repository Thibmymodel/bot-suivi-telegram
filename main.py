import os
import io
import re
import pytesseract
import shutil
import logging
import datetime
import httpx
from PIL import Image
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.ext import Defaults, CallbackContext

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Forcer chemin vers Tesseract
TESSERACT_PATH = "/usr/bin/tesseract"
if not os.path.exists(TESSERACT_PATH):
    logger.warning("❌ Tesseract introuvable à /usr/bin/tesseract. L'OCR échouera.")
else:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

# Configuration du bot Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Création de l'application FastAPI et Telegram
app_fastapi = FastAPI()
application = Application.builder().token(TELEGRAM_TOKEN).build()

# ----------- Fonctions principales -----------

def extract_text_from_image(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(image)

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.photo:
            return

        file = await context.bot.get_file(update.message.photo[-1].file_id)
        image_bytes = await file.download_as_bytearray()

        text = extract_text_from_image(image_bytes)
        await update.message.reply_text(f"🧾 Texte extrait :\n{text[:1000]}")

    except Exception as e:
        logger.error("Erreur lors de l'OCR", exc_info=True)
        await update.message.reply_text("❌ Erreur lors de l'analyse de l'image.")

# ----------- FastAPI Webhook -----------

@app_fastapi.post("/webhook")
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Erreur webhook", exc_info=True)
        return {"status": "error", "detail": str(e)}

@app_fastapi.on_event("startup")
async def on_startup():
    async with httpx.AsyncClient() as client:
        url = f"{BASE_URL}/setWebhook"
        webhook_url = os.getenv("RENDER_EXTERNAL_URL", "https://bot-suivi-telegram.onrender.com") + "/webhook"
        await client.post(url, json={"url": webhook_url})
    logger.info("✅ Bot Telegram lancé avec succès via webhook.")

@app_fastapi.on_event("shutdown")
async def on_shutdown():
    logger.info("Bot arrêté proprement.")

# ----------- Handlers Telegram -----------

application.add_handler(MessageHandler(filters.PHOTO, handle_image))