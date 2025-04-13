import os
import json
import asyncio
import logging
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder, MessageHandler, ContextTypes,
    CommandHandler, filters
)
import gspread
from google.oauth2.service_account import Credentials
from PIL import Image, ImageEnhance, ImageOps
import pytesseract
import threading

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "TOKEN_PAR_DEFAUT")
GROUP_ID = int(os.getenv("GROUP_ID", -1002317321058))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1__RzRpZKj0kg8Cl0QB-D91-hGKKff9SqsOQRE0GvReE")
REPLY_DELAY = int(os.getenv("REPLY_DELAY", 5))  # minutes
SHEET_NAME = os.getenv("SHEET_NAME", "Données Journalières")

# ==================== GOOGLE SHEET ====================
try:
    credentials_json = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
    credentials_dict = json.loads(credentials_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID)
    worksheet = sheet.worksheet(SHEET_NAME)
except Exception as e:
    logging.exception("❌ Erreur lors de l'initialisation Google Sheets :")
    raise

# ==================== FASTAPI ====================
app_fastapi = FastAPI()

@app_fastapi.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
    except Exception as e:
        logging.exception("❌ Erreur webhook :")
    return {"ok": True}

# ==================== OCR / IMAGE ====================
def try_ocr_variants(path):
    try:
        img = Image.open(path)
        variants = [
            img,
            ImageOps.grayscale(img),
            ImageEnhance.Contrast(ImageOps.grayscale(img)).enhance(2),
            ImageOps.invert(ImageOps.grayscale(img)),
            img.resize((img.size[0]*2, img.size[1]*2))
        ]
        for v in variants:
            txt = pytesseract.image_to_string(v, lang="eng+fra")
            if any(x in txt.lower() for x in ["followers", "abonnés", "publications", "suivi(e)s"]):
                return txt
    except Exception as e:
        logging.warning("❌ OCR échoué pour l'image : %s", path)
    return None

def extract_info_from_image(path):
    text = try_ocr_variants(path)
    if not text:
        return "inconnu", "ECHEC OCR ❌", -1

    lines = text.split("\n")
    network = "Instagram" if "followers" in text.lower() or "suivi" in text.lower() else \
              "Twitter" if "twitter" in text.lower() or "tweets" in text.lower() else \
              "TikTok" if "j'aime" in text.lower() else "Threads" if "threads" in text.lower() else "inconnu"

    account = next((l.strip().split()[0] for l in lines if "@" in l), "inconnu")
    followers = -1

    for l in lines:
        if any(x in l.lower() for x in ["abonnés", "followers"]):
            try:
                digits = ''.join(c if c.isdigit() or c in ",.kKmM" else "" for c in l).replace(",", ".")
                if "k" in digits.lower():
                    followers = int(float(digits.lower().replace("k", "")) * 1000)
                elif "m" in digits.lower():
                    followers = int(float(digits.lower().replace("m", "")) * 1_000_000)
                else:
                    followers = int(float(digits))
            except:
                followers = -1
            break

    if account == "inconnu" or followers == -1:
        return network, "ECHEC OCR ❌", -1
    return network, account, followers

# ==================== STOCKAGE & ÉTAT ====================
pending_images = {}

def get_previous_count(account_name):
    try:
        records = worksheet.get_all_records()
        for row in reversed(records):
            if row.get('Compte') == account_name and int(row.get('Abonnés', 0)) > 0:
                return int(row['Abonnés'])
    except Exception as e:
        logging.warning("⚠️ Erreur lecture ancienne valeur : %s", e)
    return 0

# ==================== HANDLER IMAGE ====================
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != GROUP_ID or not update.message.photo:
        return

    try:
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        path = f"temp_{update.message.message_id}.jpg"
        await file.download_to_drive(path)

        logging.info(f"📸 Image téléchargée : {path}")

        if GROUP_ID not in pending_images:
            pending_images[GROUP_ID] = {"files": [], "timestamp": datetime.now()}
        pending_images[GROUP_ID]["files"].append(path)
    except Exception as e:
        logging.exception("❌ Erreur téléchargement image Telegram :")

# ==================== TRAITEMENT IMAGES ====================
async def handle_pending(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for uid, data in list(pending_images.items()):
        if (now - data["timestamp"]).total_seconds() < REPLY_DELAY * 60:
            continue

        count = 0
        for path in data["files"]:
            net, acc, foll = extract_info_from_image(path)
            if acc == "ECHEC OCR ❌":
                continue

            today = datetime.now().strftime("%Y-%m-%d")
            rows = worksheet.get_all_records()
            if any(r.get("Date") == today and r.get("Compte") == acc for r in rows):
                continue

            try:
                delta = foll - get_previous_count(acc) if foll > 0 else 0
                assistant = context.bot.get_chat(uid).username or "Inconnu"
                worksheet.append_row([today, assistant, net, acc, foll, delta])
                count += 1
            except Exception as e:
                logging.warning("⚠️ Erreur ajout ligne Google Sheet : %s", e)

        if count:
            await context.bot.send_message(chat_id=uid, text=f"🤖 {today} – {count} comptes détectés et ajoutés ✅")
        del pending_images[uid]

# ==================== LANCEMENT DU BOT ====================
bot_app = Application.builder().token(BOT_TOKEN).build()
bot_app.add_handler(MessageHandler(filters.PHOTO, handle_image))
bot_app.job_queue.run_repeating(handle_pending, interval=REPLY_DELAY * 60)

# ==================== THREAD ASYNC ====================
def start_bot():
    asyncio.run(bot_app.initialize())
    asyncio.run(bot_app.start())
    logging.info("✅ Bot Telegram prêt à recevoir les mises à jour via webhook")

threading.Thread(target=start_bot).start()

# ==================== LANCEMENT LOCAL ====================
if __name__ == "__main__":
    import uvicorn
    logging.info("🚀 Lancement local du serveur webhook sur http://localhost:8000")
    uvicorn.run("main:app_fastapi", host="0.0.0.0", port=8000, reload=True)
