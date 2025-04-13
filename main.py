import os
import logging
import pytesseract
import shutil
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)
from google.oauth2.service_account import Credentials
import gspread
import asyncio
from datetime import datetime

# === CONFIG ===
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DELAY_SECONDS = 180  # 3 minutes

# === SETUP LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === FASTAPI APP ===
app = FastAPI()
bot_app = None  # Sera initialisé à la fin

# === GOOGLE SHEET ===
creds = Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/spreadsheets"])
client = gspread.authorize(creds)
spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1__RzRpZKj0kg8Cl0QB-D91-hGKKff9SqsOQRE0GvReE/edit#gid=1190306575")
sheet = spreadsheet.worksheet("Données")

# === OCR UTILS ===
def try_ocr(image_path):
    langs = ["eng+fra", "fra", "eng"]
    for lang in langs:
        try:
            text = pytesseract.image_to_string(image_path, lang=lang)
            if text.strip():
                return text
        except Exception as e:
            logger.warning(f"OCR failed with lang {lang}: {e}")
    return ""

def extract_info_from_image(file_path):
    text = try_ocr(file_path)
    # Extraction de données personnalisée
    return "Instagram", "@unknown", 1234  # À remplacer par parsing réel

# === HANDLER PRINCIPAL ===
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return

    file = await update.message.photo[-1].get_file()
    file_path = f"temp_{datetime.now().timestamp()}.jpg"
    await file.download_to_drive(file_path)
    logger.info(f"Image téléchargée : {file_path}")

    await asyncio.sleep(DELAY_SECONDS)

    try:
        reseau, username, abonnés = extract_info_from_image(file_path)

        # Envoi confirmation Telegram
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Données détectées :\nRéseau : {reseau}\nNom : {username}\nAbonnés : {abonnés}"
        )

        # Insertion Google Sheets
        sheet.append_row([str(datetime.now()), reseau, username, abonnés])
        logger.info(f"✅ Données ajoutées à Google Sheets pour {username}")

    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Erreur lors du traitement : {e}")
        logger.error(f"Erreur traitement image : {e}")
    finally:
        try:
            os.remove(file_path)
        except Exception:
            pass

# === FASTAPI ENDPOINT POUR TELEGRAM ===
@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.update_queue.put(update)
    return {"status": "ok"}

# === STARTUP FASTAPI + TELEGRAM ===
@app.on_event("startup")
async def startup():
    global bot_app
    bot_app = (
        ApplicationBuilder()
        .token(TOKEN)
        .concurrent_updates(True)
        .build()
    )
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    await bot_app.initialize()
    await bot_app.start()
    logger.info("✅ Bot Telegram prêt à recevoir les mises à jour via webhook")
