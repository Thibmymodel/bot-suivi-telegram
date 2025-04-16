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
from telegram import Update, Bot
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

# --- FASTAPI ---
app = FastAPI()
logger.info("🚀 FastAPI instance déclarée")

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

# --- INIT BOT (FORCÉ AU LANCEMENT AVEC THREAD) ---
init_done = False

async def init_bot():
    global init_done
    if init_done:
        return
    try:
        logger.info("🚦 Initialisation auto du bot Telegram...")
        logger.info("⏳ Étape 1 : await telegram_app.initialize()")
        await telegram_app.initialize()
        logger.info("✅ Étape 1 réussie : Telegram app initialisée")

        logger.info("⏳ Étape 2 : lancement telegram_app.start() en tâche de fond")
        asyncio.create_task(telegram_app.start())
        logger.info("✅ Étape 2 réussie : Bot lancé")

        telegram_ready.set()
        logger.info("⏳ Étape 3 : enregistrement du webhook chez Telegram")
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                data={"url": f"{RAILWAY_URL}/webhook"}
            )
            logger.info(f"🔗 Webhook setWebhook() → Status: {res.status_code} | Body: {res.text}")
        logger.info("✅ Étape 3 réussie : webhook actif")

        init_done = True
    except Exception as e:
        logger.exception("❌ Échec init_bot()")

# Lance dans un thread secondaire sécurisé avec loop propre
threading.Thread(target=lambda: asyncio.run(init_bot()), daemon=True).start()

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
# (Pas modifié pour le moment)
