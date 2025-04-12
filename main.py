import os
import json
import pytesseract
from PIL import Image, ImageEnhance, ImageOps
from datetime import datetime
import logging
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters
)
from fastapi import FastAPI, Request
import uvicorn
import asyncio
import threading

# -------------------------------
# 📦 Configuration des variables
# -------------------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "MISSING_BOT_TOKEN")
GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID", "-1"))
REPLY_DELAY = 5  # minutes

print("VERSION TESSERACT ➜", os.popen("tesseract --version").read())

# -------------------------------
# 🔐 Authentification Google Sheets
# -------------------------------
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
try:
    credentials_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not credentials_json:
        raise ValueError("Clé GOOGLE_APPLICATION_CREDENTIALS_JSON manquante")
    credentials_dict = json.loads(credentials_json)
    credentials = Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)
    gc = gspread.authorize(credentials)
    sheet = gc.open_by_key(os.getenv("SPREADSHEET_ID"))
    worksheet = sheet.worksheet("Données Journalières")
except Exception as e:
    print(f"❌ Erreur d'accès à Google Sheets : {e}")
    raise

# -------------------------------
# 🧠 OCR adaptatif
# -------------------------------
def try_ocr_variants(image_path):
    try:
        img = Image.open(image_path)
        variants = [
            img,
            ImageOps.grayscale(img),
            ImageEnhance.Contrast(ImageOps.grayscale(img)).enhance(2),
            ImageOps.invert(ImageOps.grayscale(img)),
            img.resize((img.size[0]*2, img.size[1]*2))
        ]
        for variant in variants:
            text = pytesseract.image_to_string(variant, lang="eng+fra")
            if any(word in text.lower() for word in ["followers", "abonnés", "suivis", "publications"]):
                return text
    except Exception as e:
        print(f"❌ Erreur OCR : {e}")
    return None

# -------------------------------
# 🔍 Extraction depuis l’image
# -------------------------------
def extract_info_from_image(image_path):
    text = try_ocr_variants(image_path)
    if not text:
        return "inconnu", "ECHEC OCR ❌", -1

    print("======== TEXTE OCR DÉTECTÉ =========")
    print(text)
    print("====================================")

    network, account, followers = "inconnu", "inconnu", -1
    lines = text.splitlines()
    text_lower = text.lower()

    if "threads" in text_lower:
        network = "Threads"
    elif "tiktok" in text_lower:
        network = "TikTok"
    elif "twitter" in text_lower or "tweets" in text_lower:
        network = "Twitter"
    elif "followers" in text_lower or "suivi" in text_lower or "publications" in text_lower:
        network = "Instagram"

    for line in lines:
        if "@" in line and account == "inconnu":
            account = line.strip().split()[0]
        if any(keyword in line.lower() for keyword in ["followers", "abonnés"]):
            digits = ''.join([c if c.isdigit() or c in "kKmM.," else '' for c in line]).replace(",", ".")
            if 'k' in digits.lower():
                followers = int(float(digits.lower().replace("k", "")) * 1000)
            elif 'm' in digits.lower():
                followers = int(float(digits.lower().replace("m", "")) * 1000000)
            elif digits:
                followers = int(float(digits))

    if account == "inconnu" or followers == -1:
        account = "ECHEC OCR ❌"

    return network, account, followers

# -------------------------------
# 📈 Lecture de l’évolution J-1
# -------------------------------
def get_previous_count(account_name):
    try:
        all_rows = worksheet.get_all_records()
        for row in reversed(all_rows):
            if row.get("Compte") == account_name and isinstance(row.get("Abonnés"), int):
                return row["Abonnés"]
    except Exception as e:
        print(f"❌ Erreur lecture ancienne valeur : {e}")
    return 0

# -------------------------------
# 🧠 Traitement des images
# -------------------------------
pending_images = {}

async def handle_pending(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for user_id in list(pending_images.keys()):
        user_data = pending_images[user_id]
        if (now - user_data["timestamp"]).total_seconds() > REPLY_DELAY * 60:
            results = []
            for file_path in user_data["files"]:
                res = extract_info_from_image(file_path)
                today = datetime.now().strftime("%Y-%m-%d")

                try:
                    all_rows = worksheet.get_all_records()
                    if any(r.get("Date") == today and r.get("Compte") == res[1] for r in all_rows):
                        continue
                except Exception as e:
                    print(f"❌ Erreur lecture ligne existante : {e}")

                previous = get_previous_count(res[1])
                evolution = res[2] - previous if res[2] > 0 else 0

                try:
                    username = "@inconnu"
                    try:
                        user = await context.bot.get_chat(user_id)
                        username = f"@{user.username}" if user.username else "@inconnu"
                    except Exception:
                        pass

                    worksheet.append_row([
                        today,
                        username,
                        res[0],
                        res[1],
                        res[2],
                        evolution
                    ])
                    results.append(res)
                except Exception as e:
                    print(f"❌ Erreur ajout Google Sheet : {e}")

            if results:
                await context.bot.send_message(chat_id=user_id, text=f"🤖 {today} – {len(results)} comptes détectés et ajoutés ✅")
            del pending_images[user_id]

# -------------------------------
# 🖼️ Gestion des images Telegram
# -------------------------------
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message.chat_id != GROUP_ID:
            return
        if not update.message.photo:
            return

        file = await context.bot.get_file(update.message.photo[-1].file_id)
        file_path = f"temp_{update.message.message_id}.jpg"
        await file.download_to_drive(file_path)

        user_id = update.message.chat_id
        print(f"📸 Image reçue et stockée : {file_path}")

        if user_id not in pending_images:
            pending_images[user_id] = {"files": [], "timestamp": datetime.now()}
        pending_images[user_id]["files"].append(file_path)

    except Exception as e:
        print(f"❌ Erreur lors du téléchargement de l’image : {e}")

# -------------------------------
# 🤖 Bot Telegram + thread sécurisé
# -------------------------------
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.PHOTO, handle_image))
app.job_queue.run_repeating(handle_pending, interval=REPLY_DELAY * 60)

async def run_bot():
    await app.initialize()
    await app.start()
    print("🤖 Bot Telegram prêt à recevoir les mises à jour via webhook")

# Thread sécurisé pour Render
def start_bot():
    asyncio.run(run_bot())

threading.Thread(target=start_bot).start()

# -------------------------------
# 🌐 Webhook FastAPI
# -------------------------------
app_fastapi = FastAPI()

@app_fastapi.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        body = await request.json()
        update = Update.de_json(body, app.bot)
        await app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        print(f"❌ Erreur webhook Telegram : {e}")
        return {"status": "error"}

# -------------------------------
# 🚀 Lancement local
# -------------------------------
if __name__ == "__main__":
    print("🚀 Lancement local du serveur webhook sur http://localhost:8000")
    uvicorn.run(app_fastapi, host="0.0.0.0", port=8000, reload=True)
