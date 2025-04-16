import os
import io
import re
import json
import shutil
import logging
import datetime
from PIL import Image, ImageOps
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi import FastAPI
from contextlib import asynccontextmanager
from telegram import Update, Bot, Message
from telegram.ext import Application, ContextTypes, MessageHandler, filters
import pytesseract
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import httpx
import asyncio
import threading

# --- LOGS ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_URL", "http://localhost:8000").rstrip("/")
GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
logger.info(f"🔑 BOT_TOKEN: {'PRÉSENT' if BOT_TOKEN else 'ABSENT'}")
logger.info(f"🔑 RAILWAY_URL: {RAILWAY_URL}")
logger.info(f"🔑 GROUP_ID: {GROUP_ID}")
logger.info(f"🔑 SPREADSHEET_ID: {SPREADSHEET_ID}")

# --- TELEGRAM ---
telegram_app = Application.builder().token(BOT_TOKEN).build()
bot = telegram_app.bot
telegram_ready = asyncio.Event()

# --- TESSERACT ---
pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "tesseract"
logger.info(f"✅ Tesseract détecté : {pytesseract.pytesseract.tesseract_cmd}")

# --- GOOGLE SHEET ---
creds_dict = json.loads(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Données Journalières")
logger.info("✅ Connexion Google Sheets réussie")

# --- FASTAPI + LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    def runner():
        async def start():
            try:
                logger.info("🚦 Initialisation LIFESPAN → Telegram bot")
                await telegram_app.initialize()
                logger.info("✅ Telegram app initialisée")

                # 📸 Handler images
                telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
                logger.info("🧩 Handler photo enregistré")

                asyncio.create_task(telegram_app.start())
                logger.info("🚀 Bot Telegram lancé en tâche de fond")
                telegram_ready.set()
                async with httpx.AsyncClient() as client:
                    res = await client.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                        data={"url": f"{RAILWAY_URL}/webhook"}
                    )
                    logger.info(f"🔗 Webhook enregistré → {res.status_code} | {res.text}")
            except Exception as e:
                logger.exception("❌ Échec init Telegram")
        asyncio.run(start())
    threading.Thread(target=runner, daemon=True).start()
    yield

app = FastAPI(lifespan=lifespan)
logger.info("🚀 FastAPI instance déclarée (avec lifespan)")

@app.get("/")
async def root():
    logger.info("📡 Ping reçu sur /")
    return {"status": "Bot opérationnel"}

@app.get("/force-webhook")
async def force_webhook():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                data={"url": f"{RAILWAY_URL}/webhook"}
            )
        logger.info(f"✅ Webhook forcé : {response.text}")
        return {"webhook_response": response.json()}
    except Exception as e:
        logger.error(f"❌ Erreur lors du reset webhook : {e}")
        return {"error": str(e)}

# --- ROUTE WEBHOOK ---
@app.post("/webhook")
async def webhook(req: Request):
    logger.info("📩 Webhook reçu → traitement en cours...")
    try:
        await telegram_ready.wait()
        raw = await req.body()
        logger.info(f"🧾 Contenu brut reçu : {raw[:200]}")
        update = Update.de_json(json.loads(raw), bot)
        logger.info(f"🧠 Update reçu : {update.to_dict()}")
        await telegram_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.exception("❌ Erreur route /webhook")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

# --- HANDLERS ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("📷 Image reçue ! Tentative de téléchargement...")
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        image = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

        logger.info("🧪 OCR en cours...")
        gray = ImageOps.grayscale(image)
        cropped = gray.crop((0, 0, gray.width, int(gray.height * 0.4)))
        upscaled = cropped.resize((cropped.width * 2, cropped.height * 2))
        text = pytesseract.image_to_string(upscaled)

        logger.info(f"🔍 Résultat OCR brut :\n{text}")
        await update.message.reply_text("📸 Image reçue et analysée avec succès.")
    except Exception as e:
        logger.exception("❌ Erreur lors du traitement de l'image")
        await update.message.reply_text("❌ Erreur lors du traitement de l'image.")