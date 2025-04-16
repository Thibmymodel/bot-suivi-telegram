import os
import re
import io
import json
import datetime
import logging
from PIL import Image, ImageEnhance, ImageOps
import pytesseract
import gspread
import shutil
from oauth2client.service_account import ServiceAccountCredentials
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# --- CONFIG LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIG FASTAPI ---
app = FastAPI()

# --- ENV VARIABLES ---
PORT = int(os.getenv("PORT", 8000))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_URL")
GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GENERAL_TOPIC_NAME = "General"

# --- SETUP TESSERACT ---
TESSERACT_PATH = shutil.which("tesseract")
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH if TESSERACT_PATH else "tesseract"

# --- SETUP GOOGLE SHEETS ---
credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
creds_dict = json.loads(credentials_json)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Données Journalières")

# --- TELEGRAM APPLICATION ---
telegram_app = Application.builder().token(BOT_TOKEN).build()
bot = Bot(token=BOT_TOKEN)

# --- UTILITY FUNCTIONS ---
def preprocess_image(image: Image.Image) -> Image.Image:
    image = image.convert("L")  # Niveaux de gris
    image = ImageOps.autocontrast(image)
    image = image.resize((image.width * 2, image.height * 2))
    return image

def extract_text_from_image(image_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image = preprocess_image(image)
        return pytesseract.image_to_string(image)
    except Exception as e:
        logger.error(f"Erreur OCR : {e}")
        return ""

def detect_network(text: str) -> str:
    text = text.lower()
    if "followers" in text and "likes" in text:
        return "TikTok"
    elif "publications" in text and "abonnés" in text:
        return "Instagram"
    elif "abonnements" in text and "abonnés" in text and "rejoint" in text:
        return "Twitter"
    elif "threads" in text:
        return "Threads"
    else:
        return "Non détecté"

def extract_account_and_followers(text: str) -> tuple[str, int]:
    username_match = re.search(r"@\w+", text)
    account = username_match.group() if username_match else "Non détecté"

    number_match = re.findall(r"\d+[\.,]?\d*\s*[kK]", text)
    if number_match:
        raw = number_match[0].replace(" ", "").lower().replace(",", ".")
        number = float(re.findall(r"\d+\.?\d*", raw)[0]) * 1000
        return account, int(number)

    number_match = re.findall(r"\b\d{3,6}\b", text)
    if number_match:
        return account, int(number_match[0])

    return account, -1

def get_last_value_for_account(account: str, assistant: str) -> int:
    try:
        records = sheet.get_all_records()
        records = [r for r in records if r["Compte"] == account and r["Assistant"] == assistant]
        if not records:
            return 0
        last = sorted(records, key=lambda x: x["Date"], reverse=True)[0]
        return int(last["Abonnés"])
    except:
        return 0

def write_to_sheet(date: str, assistant: str, network: str, account: str, followers: int):
    previous = get_last_value_for_account(account, assistant)
    evolution = followers - previous if previous > 0 else ""
    sheet.append_row([date, assistant, network, account, followers, evolution])

# --- MAIN HANDLER ---
@telegram_app.message_handler(filters.PHOTO)
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        thread_name = update.message.message_thread_id
        topic_name = update.message.forum_topic_name
        assistant_name = topic_name.replace("SUIVI ", "").strip().upper()
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")

        photo = await update.message.photo[-1].get_file()
        image_bytes = await photo.download_as_bytearray()
        text = extract_text_from_image(image_bytes)

        accounts_data = []

        for match in re.finditer(r"@\w+", text):
            snippet = text[match.start():match.start()+100]  # zone locale autour du @
            account, followers = extract_account_and_followers(snippet)
            if followers == -1:
                continue
            network = detect_network(text)
            accounts_data.append((account, followers, network))
            write_to_sheet(date_str, f"@{assistant_name.lower()}", network, account, followers)

        if accounts_data:
            msg = f"🤖 {date_str} – {assistant_name} – {len(accounts_data)} compte{'s' if len(accounts_data)>1 else ''} détecté{'s' if len(accounts_data)>1 else ''} et ajouté{'s' if len(accounts_data)>1 else ''} ✅"
        else:
            msg = f"❌ {date_str} – {assistant_name} – Analyse OCR impossible"

        # Envoi dans le sujet "General"
        await bot.send_message(
            chat_id=GROUP_ID,
            text=msg,
            message_thread_id=get_thread_id_by_name("General")
        )

    except Exception as e:
        logger.exception("Erreur lors du traitement de l'image")
        await bot.send_message(
            chat_id=GROUP_ID,
            text=f"❌ {date_str} – {assistant_name} – Analyse OCR impossible",
            message_thread_id=get_thread_id_by_name("General")
        )

# --- THREAD ID FETCH ---
def get_thread_id_by_name(name: str) -> int:
    forum_topics = bot.get_forum_topic_list(chat_id=GROUP_ID)
    for topic in forum_topics.topics:
        if topic.name.lower() == name.lower():
            return topic.message_thread_id
    return None

# --- FASTAPI ROUTES ---
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

@app.get("/")
def root():
    return {"status": "Bot operationnel"}

# --- RUN APP ---
if __name__ == "__main__":
    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=f"{RAILWAY_URL}/webhook"
    )
