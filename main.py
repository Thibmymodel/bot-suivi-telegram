import os
import io
import re
import pytesseract
import shutil
import logging
import datetime
import httpx
from PIL import Image
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.ext import Defaults, CallbackContext

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔍 Détection dynamique de Tesseract
def detect_tesseract_path():
    path = shutil.which("tesseract")
    if path:
        logger.info(f"✅ Tesseract trouvé automatiquement à : {path}")
        return path
    elif os.path.exists("/usr/bin/tesseract"):
        logger.warning("❌ Tesseract non trouvé automatiquement. Utilisation du chemin par défaut : /usr/bin/tesseract")
        return "/usr/bin/tesseract"
    else:
        logger.critical("❌❌ Tesseract introuvable même au chemin par défaut. OCR indisponible.")
        return None

tesseract_path = detect_tesseract_path()
pytesseract.pytesseract.tesseract_cmd = tesseract_path
logger.info(f"📌 pytesseract utilisera : {pytesseract.pytesseract.tesseract_cmd}")

# Test exécution directe
if tesseract_path:
    try:
        import subprocess
        result = subprocess.run([tesseract_path, "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("🧪 Tesseract fonctionne correctement.")
        else:
            logger.error("❌ Erreur lors du test de Tesseract.")
    except Exception as e:
        logger.error("❌ Exception lors de l'exécution de Tesseract", exc_info=True)

# Configuration du bot Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_BOT_TOKEN non défini dans les variables d'environnement.")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Création de l'application FastAPI et Telegram
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Initialisation FastAPI
app_fastapi = FastAPI()

@app_fastapi.on_event("startup")
async def on_startup():
    try:
        async with httpx.AsyncClient() as client:
            url = f"{BASE_URL}/setWebhook"
            webhook_url = os.getenv("RENDER_EXTERNAL_URL", "https://bot-suivi-telegram.onrender.com") + "/webhook"
            response = await client.post(url, json={"url": webhook_url})
            if response.status_code == 200:
                logger.info("✅ Webhook Telegram configuré.")
            else:
                logger.warning(f"⚠️ Webhook non configuré. Code HTTP: {response.status_code}")
    except Exception as e:
        logger.error("Erreur lors de la configuration du webhook", exc_info=True)

@app_fastapi.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Arrêt du bot Telegram.")

# ----------- Fonctions principales -----------

def extract_text_from_image(image_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        return pytesseract.image_to_string(image)
    except Exception as e:
        logger.error("❌ Erreur lors de l'extraction OCR", exc_info=True)
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
        logger.error("Erreur lors du traitement de l'image", exc_info=True)
        await update.message.reply_text("❌ Une erreur est survenue lors de l'analyse de l'image.")

# ----------- Webhook FastAPI -----------

@app_fastapi.post("/webhook")
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return JSONResponse(content={"status": "ok"}, status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error("Erreur webhook", exc_info=True)
        return JSONResponse(content={"status": "error", "detail": str(e)}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

# ----------- Handlers Telegram -----------

application.add_handler(MessageHandler(filters.PHOTO, handle_image))

logger.info("✅ Serveur FastAPI prêt.")
