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

# Détection dynamique de Tesseract
TESSERACT_PATH = "/usr/bin/tesseract"
found_path = shutil.which("tesseract")

if os.path.exists(TESSERACT_PATH):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
    logger.info(f"✅ Tesseract trouvé à {TESSERACT_PATH}")
elif found_path:
    pytesseract.pytesseract.tesseract_cmd = found_path
    logger.info(f"✅ Tesseract détecté dynamiquement à {found_path}")
else:
    logger.warning("❌ Tesseract introuvable. L'OCR échouera.")

# Configuration du bot Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_BOT_TOKEN non défini dans les variables d'environnement.")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Création de l'application FastAPI et Telegram
app_fastapi = FastAPI()
application = Application.builder().token(TELEGRAM_TOKEN).build()

# ----------- Fonctions principales -----------

def extract_text_from_image(image_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        return pytesseract.image_to_string(image)
    except Exception as e:
        logger.error("Erreur lors de l'extraction de texte OCR", exc_info=True)
        return ""

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.photo:
            return

        file = await context.bot.get_file(update.message.photo[-1].file_id)
        image_bytes = await file.download_as_bytearray()
        text = extract_text_from_image(image_bytes)

        if text.strip():
            await update.message.reply_text(f"🧾 Texte extrait :\n{text[:1000]}")
        else:
            await update.message.reply_text("❌ Aucun texte détecté dans l'image.")

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