import os
import io
import re
import json
import shutil
import logging
import datetime
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
        logger.info(f"👾️ Contenu brut reçu (200c max) : {raw[:200]}")
        update_dict = json.loads(raw)
        logger.info(f"📨 JSON complet reçu : {json.dumps(update_dict, indent=2)[:1000]}")
        update = Update.de_json(update_dict, bot)
        logger.info(f"😮 Update transformé avec succès → {update}")
        await telegram_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.exception("❌ Erreur route /webhook")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

# --- OCR UTILS ---
def detect_social_network(text):
    text = text.lower()
    if "followers" in text and "suivis" in text:
        return "tiktok"
    elif "publications" in text and "abonnés" in text:
        return "instagram"
    elif "threads" in text:
        return "threads"
    elif "abonnements" in text and "abonnés" in text:
        return "twitter"
    return "unknown"

def clean_number(value):
    value = value.lower().replace(" ", "").replace(",", ".")
    if 'k' in value:
        return int(float(value.replace('k', '')) * 1000)
    if 'm' in value:
        return int(float(value.replace('m', '')) * 1_000_000)
    return int(float(value))

# --- HANDLERS ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("📷 Image reçue ! Tentative de téléchargement...")
    try:
        await asyncio.sleep(120)

        message_id = update.message.message_id
        if message_id in already_processed:
            logger.info(f"⏰ Message {message_id} déjà traité. Ignoré.")
            return
        already_processed.add(message_id)
        logger.info(f"📌 Nouveau message ID ajouté aux traités : {message_id}")

        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        image = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

        logger.info("🤪 OCR en cours...")
        gray = ImageOps.grayscale(image)
        contrast = ImageEnhance.Contrast(gray).enhance(2.5)
        sharpened = contrast.filter(ImageFilter.SHARPEN)
        cropped = sharpened.crop((0, 0, sharpened.width, int(sharpened.height * 0.42)))
        resized = cropped.resize((cropped.width * 2, cropped.height * 2))
        text = pytesseract.image_to_string(resized)
        logger.info(f"🔍 Résultat OCR brut :\n{text}")

        if not text.strip():
            raise ValueError("OCR vide")

        network = detect_social_network(text)
        logger.info(f"🌐 Réseau détecté : {network}")

        date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        va_name = "GENERAL"
        if update.message.is_topic_message and update.message.reply_to_message:
            topic_name = update.message.reply_to_message.forum_topic_created.name
            if topic_name.upper().startswith("SUIVI "):
                va_name = topic_name[6:].strip()

        all_usernames = re.findall(r"@([a-zA-Z0-9_.]{3,30})", text)
        username = all_usernames[0] if all_usernames else None
        logger.warning(f"👀 OCR username détecté : {username if username else 'Non trouvé'}")

        followers_match = re.search(r"(\d{1,3}(?:[.,\s]\d{1,3})*)\s*(abonn[ée]s|followers)", text, re.IGNORECASE)

        if not followers_match:
            logger.warning("🔍 Recherche secondaire pour les abonnés...")
            match = re.findall(r"\b(\d{1,3}(?:[.,\s]\d{1,3})*)\b", text)
            for number in match:
                if "followers" in text.lower() or "abonnés" in text.lower():
                    followers_match = re.match(r".*", number)  # Mock pour l'utiliser ensuite
                    followers = clean_number(number)
                    break

        if not username or not followers_match:
            logger.warning(f"👀 OCR abonnés détecté : Non trouvé")
            raise ValueError("Nom d'utilisateur ou abonnés introuvable dans l'OCR")

        if 'followers' not in text.lower() and 'abonn' not in text.lower():
            raise ValueError("Mention abonnés absente")

        followers = clean_number(followers_match.group(1))
        logger.warning(f"👀 OCR abonnés détecté : {followers}")

        sheet.append_row([date, network, va_name, f"@{username}", followers, "="])
        logger.info(f"✅ Données ajoutées à Google Sheet pour @{username} → {followers} abonnés")

        message = f"🧰 {date} - {va_name} - 1 compte détecté et ajouté ✅"
        await context.bot.send_message(chat_id=GROUP_ID, message_thread_id=None, text=message)

    except Exception as e:
        logger.exception("❌ Erreur lors du traitement de l'image")
        try:
            date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            va_name = "GENERAL"
            if update.message.is_topic_message and update.message.reply_to_message:
                topic_name = update.message.reply_to_message.forum_topic_created.name
                if topic_name.upper().startswith("SUIVI "):
                    va_name = topic_name[6:].strip()
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"❌ {date} - {va_name} - Analyse OCR impossible"
            )
        except Exception:
            logger.warning("❌ Impossible d'envoyer un message d'erreur dans Général")