import os
import io
import re
import json
import shutil
import logging
import datetime
from difflib import get_close_matches
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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

# --- DOUBLONS ---
already_processed = set()

# --- CHARGEMENT DES HANDLES ---
try:
    with open("known_handles.json", "r", encoding="utf-8") as f:
        KNOWN_HANDLES = json.load(f)
    logger.info("📂 known_handles.json chargé avec succès")
except Exception as e:
    KNOWN_HANDLES = {}
    logger.warning(f"⚠️ Échec chargement known_handles.json : {e}")

def corriger_username(username_ocr: str, reseau: str) -> str:
    handles = KNOWN_HANDLES.get(reseau.lower(), [])
    username_ocr_clean = username_ocr.strip().encode("utf-8", "ignore").decode()
    candidats = get_close_matches(username_ocr_clean.lower(), handles, n=1, cutoff=0.6)
    if candidats:
        logger.info(f"🔁 Correction OCR : '{username_ocr}' → '{candidats[0]}'")
        return candidats[0]
    return username_ocr

# --- HANDLER PHOTO ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message
        if not message or not message.photo:
            return

        thread_id = message.message_thread_id
        reply = message.reply_to_message
        if not reply or not hasattr(reply, "forum_topic_created"):
            return

        topic_name = reply.forum_topic_created.name
        if not topic_name.startswith("SUIVI "):
            return

        assistant = topic_name.replace("SUIVI ", "").strip().upper()
        photo = message.photo[-1]

        file = await bot.get_file(photo.file_id)
        img_bytes = await file.download_as_bytearray()
        image = Image.open(io.BytesIO(img_bytes))
        width, height = image.size
        cropped = image.crop((0, 0, width, int(height * 0.4)))
        enhanced = ImageOps.autocontrast(cropped)

        text = pytesseract.image_to_string(enhanced)
        logger.info(f"🔍 OCR brut :\n{text}")

        if "tiktok" in text.lower():
            reseau = "tiktok"
        elif "threads" in text.lower():
            reseau = "threads"
        elif "twitter" in text.lower():
            reseau = "twitter"
        else:
            reseau = "instagram"

        usernames = re.findall(r"@([a-zA-Z0-9_.]+)", text)
        username = usernames[0] if usernames else "Non trouvé"
        username = username.strip().encode("utf-8", "ignore").decode()
        logger.info(f"🔎 Username avant correction : '{username}' (réseau : {reseau})")
        username = corriger_username(username, reseau)

        abonnés = None
        match = re.search(r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,3})?)\s*(abonnés|followers|suivis|followers|j'aime|likes)", text, re.IGNORECASE)
        if match:
            abonnés = match.group(1).replace(",", ".").replace(" ", "")
        else:
            logger.warning("⚠️ Aucune donnée d'abonnés trouvée")

        if not username or not abonnés:
            raise ValueError("Nom d'utilisateur ou abonnés introuvable dans l'OCR")

        if message.message_id in already_processed:
            logger.info("⚠️ Message déjà traité, on ignore.")
            return
        already_processed.add(message.message_id)

        today = datetime.datetime.now().strftime("%d/%m/%Y")
        row = [today, assistant, reseau, username, abonnés, ""]
        sheet.append_row(row)

        msg = f"🤖 {today} - {assistant} - 1 compte détecté et ajouté ✅"
        await bot.send_message(chat_id=GROUP_ID, text=msg)

    except Exception as e:
        logger.exception("❌ Erreur traitement handle_photo")
        await bot.send_message(chat_id=GROUP_ID, text=f"❌ {datetime.datetime.now().strftime('%d/%m')} - Analyse OCR impossible")

# --- FASTAPI + LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    def runner():
        async def start():
            try:
                logger.info("🚦 Initialisation LIFESPAN → Telegram bot")
                await telegram_app.initialize()
                logger.info("✅ Telegram app initialisée")

                telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
                logger.info("📷 Handler photo enregistré")

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
    logger.info("📱 Ping reçu sur /")
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

@app.post("/webhook")
async def webhook(req: Request):
    logger.info("📬 Webhook reçu → traitement en cours...")
    try:
        await telegram_ready.wait()
        raw = await req.body()
        logger.info(f"📃️ Contenu brut reçu (200c max) : {raw[:200]}")
        update_dict = json.loads(raw)
        logger.info(f"📸 JSON complet reçu : {json.dumps(update_dict, indent=2)[:1000]}")
        update = Update.de_json(update_dict, bot)
        logger.info(f"😮 Update transformé avec succès → {update}")
        await telegram_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.exception("❌ Erreur route /webhook")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})
