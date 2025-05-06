import json
import io
import re
import datetime
import logging
import os
from difflib import get_close_matches

from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image, ImageOps
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.cloud import vision

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Initialisation Google Sheets
google_creds_gspread_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_GSPREAD")
if not google_creds_gspread_json_str:
    logger.error("La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS_GSPREAD n_est pas définie.")
    exit()
try:
    creds_gspread_dict = json.loads(google_creds_gspread_json_str)
    gspread_creds = ServiceAccountCredentials.from_service_account_info(creds_gspread_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(gspread_creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
    logger.info("Connexion à Google Sheets réussie.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Sheets: {e}")
    exit()

# Initialisation Google Vision AI
google_creds_vision_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if not google_creds_vision_json_str:
    logger.error("La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS (pour Vision) n_est pas définie.")
    exit()
try:
    creds_vision_dict = json.loads(google_creds_vision_json_str)
    vision_creds = ServiceAccountCredentials.from_service_account_info(creds_vision_dict)
    vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)
    logger.info("Client Google Vision AI initialisé avec succès.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Vision AI: {e}")
    exit()

bot = Bot(TOKEN)
already_processed = set()

with open("known_handles.json", "r", encoding="utf-8") as f:
    KNOWN_HANDLES = json.load(f)

def corriger_username(username: str, reseau: str) -> str:
    if reseau == "instagram" and username.startswith("@"):
        return username[1:]
    return username

def normaliser_nombre_followers(nombre_str: str) -> str | None:
    nombre_str_clean = nombre_str.lower().replace(" ", "").replace(".", "").replace(",", "")
    valeur = None
    if "k" in nombre_str_clean:
        valeur = str(int(float(nombre_str_clean.replace("k", "")) * 1000))
    elif "m" in nombre_str_clean:
        valeur = str(int(float(nombre_str_clean.replace("m", "")) * 1000000))
    else:
        try:
            valeur = str(int(nombre_str_clean))
        except ValueError:
            return None
    return valeur

def extraire_followers_tiktok(texte_ocr: str) -> str | None:
    logger.info(f"extraire_followers_tiktok: Texte OCR reçu pour analyse TikTok: {texte_ocr[:200]}...")
    patterns = [
        r"(\d[\d.,\s]*[kKmM]?)\s*(?:followers|abonnés|abonné|fans|abos)", 
        r"(?:followers|abonnés|abonné|fans|abos)\s*(\d[\d.,\s]*[kKmM]?)"
    ]
    for pattern in patterns:
        match = re.search(pattern, texte_ocr, re.IGNORECASE)
        if match:
            nombre_str = match.group(1)
            logger.info(f"extraire_followers_tiktok: Match trouvé avec pattern 	'{pattern}	': 	'{nombre_str}	'") # Correction ici
            nombre_normalise = normaliser_nombre_followers(nombre_str)
            if nombre_normalise:
                logger.info(f"extraire_followers_tiktok: Nombre normalisé: {nombre_normalise}")
                return nombre_normalise
            else:
                logger.warning(f"extraire_followers_tiktok: Impossible de normaliser 	'{nombre_str}	'") # Correction ici

    logger.info("extraire_followers_tiktok: Aucun match avec mot-clé. Tentative de fallback...")
    nombres_bruts = re.findall(r"(\d[\d.,\s]*[kKmM]?)", texte_ocr)
    candidats_normalises = []
    for nb_str in nombres_bruts:
        nb_norm = normaliser_nombre_followers(nb_str)
        if nb_norm:
            candidats_normalises.append(int(nb_norm))
    
    logger.info(f"extraire_followers_tiktok (fallback): Candidats normalisés: {candidats_normalises}")
    if len(candidats_normalises) >= 3: # Souvent Suivis | Followers | J_aime
        logger.info(f"extraire_followers_tiktok (fallback): 3+ nombres trouvés, sélection du 2ème: {candidats_normalises[1]}")
        return str(candidats_normalises[1])
    elif len(candidats_normalises) == 2:
        logger.info(f"extraire_followers_tiktok (fallback): 2 nombres trouvés, sélection du 2ème: {candidats_normalises[1]}")
        return str(candidats_normalises[1])
    elif len(candidats_normalises) == 1:
        logger.info(f"extraire_followers_tiktok (fallback): 1 seul nombre trouvé: {candidats_normalises[0]}")
        return str(candidats_normalises[0])

    logger.warning("extraire_followers_tiktok: Aucun nombre de followers n_a pu être extrait.")
    return None

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("--- Entrée dans handle_photo ---")
    assistant = "INCONNU" # Valeur par défaut pour l_assistant
    today = datetime.datetime.now().strftime("%d/%m/%Y")
    message_status_general = f"🤖 {today} - {assistant} - ❌ Analyse OCR impossible ❌" # Message par défaut en cas d_échec précoce
    donnees_extraites_ok = False

    try:
        message = update.message
        if not message or not message.photo:
            logger.info("handle_photo: Message None ou sans photo, sortie.")
            return

        reply = message.reply_to_message
        if not reply or not hasattr(reply, "forum_topic_created") or not reply.forum_topic_created:
            logger.info("handle_photo: Pas une réponse à un topic valide, sortie.")
            return
            
        topic_name = reply.forum_topic_created.name
        if not topic_name.startswith("SUIVI "):
            logger.info(f"handle_photo: Nom du topic 	'{topic_name}	' non conforme, sortie.") # Correction ici
            return
        
        assistant = topic_name.replace("SUIVI ", "").strip().upper()
        logger.info(f"handle_photo: Assistant extrait: 	'{assistant}	'") # Correction ici
        message_status_general = f"🤖 {today} - {assistant} - ❌ Analyse OCR impossible ❌"

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        img_bytes_io = io.BytesIO()
        await file.download_to_memory(img_bytes_io)
        img_bytes_io.seek(0)
        img_content = img_bytes_io.read()

        image = Image.open(io.BytesIO(img_content))
        width, height = image.size
        cropped_image = image.crop((0, 0, width, int(height * 0.4)))
        enhanced_image = ImageOps.autocontrast(cropped_image)
        byte_arr = io.BytesIO()
        enhanced_image.save(byte_arr, format=	'PNG') # Correction ici (guillemets)
        content_vision = byte_arr.getvalue()

        image_vision = vision.Image(content=content_vision)
        response = vision_client.text_detection(image=image_vision)
        texts = response.text_annotations

        if response.error.message:
            logger.error(f"handle_photo: Erreur API Google Vision: {response.error.message}")
            raise Exception(f"Erreur Google Vision: {response.error.message}")

        ocr_text = texts[0].description if texts else ""
        logger.info(f"🔍 OCR Google Vision brut (premiers 500 caractères):\n{ocr_text[:500]}")

        if not ocr_text:
            logger.warning("handle_photo: OCR n_a retourné aucun texte.")
            return

        if "getallmylinks.com" in ocr_text.lower(): reseau = "instagram"
        elif "beacons.ai" in ocr_text.lower(): reseau = "twitter"
        elif "tiktok" in ocr_text.lower() or any(k in ocr_text.lower() for k in ["followers", "j_aime", "abonnés", "abonné", "fans"]): reseau = "tiktok"
        elif "threads" in ocr_text.lower(): reseau = "threads"
        elif any(x in ocr_text.lower() for x in ["modifier le profil", "suivi(e)s", "publications"]): reseau = "instagram"
        else: reseau = "instagram"; logger.info("Réseau non clairement identifié, par défaut Instagram.")
        logger.info(f"handle_photo: Réseau identifié: {reseau}")

        usernames_found = re.findall(r"@([a-zA-Z0-9_.-]{3,})", ocr_text)
        reseau_handles = KNOWN_HANDLES.get(reseau.lower(), [])
        username = "Non trouvé"
        cleaned_usernames = [re.sub(r"[^a-zA-Z0-9_.-]", "", u).lower() for u in usernames_found]
        for u_cleaned in cleaned_usernames:
            if u_cleaned in [h.lower() for h in reseau_handles]:
                username = next((h_original for h_original in reseau_handles if h_original.lower() == u_cleaned), "Non trouvé")
                if username != "Non trouvé": break
        if username == "Non trouvé":
            for u in usernames_found:
                matches = get_close_matches(u.lower(), reseau_handles, n=1, cutoff=0.7)
                if matches: username = matches[0]; break
        if username == "Non trouvé" and usernames_found: username = usernames_found[0]
        urls = re.findall(r"(getallmylinks\.com|beacons\.ai|linktr\.ee|tiktok\.com)/([a-zA-Z0-9_.-]+)", ocr_text, re.IGNORECASE)
        if username == "Non trouvé" and urls:
            for _, u_from_url in urls:
                match_url = get_close_matches(u_from_url.lower(), reseau_handles, n=1, cutoff=0.7)
                if match_url: username = match_url[0]; break
                if username == "Non trouvé": username = u_from_url
        username = corriger_username(username, reseau)
        logger.info(f"🕵️ Username final : 	'{username}	' (réseau : {reseau})") # Correction ici

        abonnés = None
        if reseau == "tiktok":
            abonnés = extraire_followers_tiktok(ocr_text)
        else:
            match_explicit = re.search(r"(\d[\d.,\s]*[kKmM]?)\s*(?:abonnés|followers|suivies|suivi\(e\)s|abonné)", ocr_text, re.IGNORECASE)
            if match_explicit: abonnés = normaliser_nombre_followers(match_explicit.group(1))
            if not abonnés:
                numbers_extracted_int = []
                raw_numbers = re.findall(r"(\d[\d.,\s]*[kKmM]?)", ocr_text)
                for num_str in raw_numbers:
                    val_norm = normaliser_nombre_followers(num_str)
                    if val_norm: numbers_extracted_int.append(int(val_norm))
                if len(numbers_extracted_int) >= 3: abonnés = str(numbers_extracted_int[1])
                elif len(numbers_extracted_int) == 2 and reseau == "instagram": abonnés = str(numbers_extracted_int[1])
                elif len(numbers_extracted_int) == 1 and reseau == "instagram": abonnés = str(numbers_extracted_int[0])
        logger.info(f"handle_photo: Abonnés extraits ({reseau}): {abonnés}")

        if not username or username == "Non trouvé" or not abonnés:
            logger.warning(f"Données incomplètes: Username (	'{username}	') ou Abonnés (	'{abonnés}	') pour {reseau}.") # Correction ici
            donnees_extraites_ok = False
        else:
            donnees_extraites_ok = True

        if message.message_id in already_processed:
            logger.info(f"⚠️ Message ID {message.message_id} déjà traité, on ignore.")
            return
        already_processed.add(message.message_id)

        if donnees_extraites_ok:
            username_to_sheet = f"@{username}"
            abonnés_to_sheet = str(abonnés)
            row = [today, assistant, reseau, username_to_sheet, abonnés_to_sheet, ""]
            try:
                sheet.append_row(row)
                logger.info("handle_photo: Ligne ajoutée à Google Sheets.")
                message_status_general = f"🤖 {today} - {assistant} - ✅ 1 compte détecté et ajouté ✅"
                msg_topic_assistant = f"📊 {today} - {assistant} - {reseau.capitalize()} @{username} ({abonnés}) ajouté ✅"
                await bot.send_message(chat_id=GROUP_ID, text=msg_topic_assistant, message_thread_id=message.message_thread_id)
                logger.info("Message de confirmation envoyé au topic de l_assistant.")
            except Exception as e_sheet:
                logger.error(f"handle_photo: Erreur lors de l_ajout à Google Sheets: {e_sheet}")
                message_status_general = f"🤖 {today} - {assistant} - ⚠️ Erreur écriture Sheets ⚠️"
                error_message_sheet = f"❌ {today} - Erreur Google Sheets: {str(e_sheet)[:100]}"
                await bot.send_message(chat_id=GROUP_ID, text=error_message_sheet, message_thread_id=message.message_thread_id)

    except Exception as e:
        logger.exception("❌ Erreur globale dans handle_photo")
        assistant_nom = assistant if assistant != "INCONNU" else topic_name.replace("SUIVI ", "").strip().upper() if 'reply' in locals() and reply and hasattr(reply, "forum_topic_created") and reply.forum_topic_created and reply.forum_topic_created.name.startswith("SUIVI ") else "INCONNU"
        message_status_general = f"🤖 {today} - {assistant_nom} - ❌ Analyse OCR impossible ❌"
        try:
            error_message_topic = f"❌ {today} - Erreur analyse: {str(e)[:100]}"
            thread_id_for_error = message.message_thread_id if 'message' in locals() and message and hasattr(message, "is_topic_message") and message.is_topic_message else None
            if thread_id_for_error:
                 await bot.send_message(chat_id=GROUP_ID, text=error_message_topic, message_thread_id=thread_id_for_error)
        except Exception as send_error:
            logger.error(f"Impossible d_envoyer le message d_erreur (globale) au topic: {send_error}")
    finally:
        logger.info(f"Message à envoyer au General: {message_status_general}")
        try:
            await bot.send_message(chat_id=GROUP_ID, text=message_status_general)
            logger.info("Message de statut envoyé au sujet General.")
        except Exception as e_send_general:
            logger.error(f"Impossible d_envoyer le message de statut au sujet General: {e_send_general}")
        logger.info("--- Sortie de handle_photo ---")

from fastapi import FastAPI, Request, HTTPException
import asyncio
import uvicorn

app = FastAPI(lifespan=None)

@app.on_event("startup")
async def startup():
    logger.info("Application startup...")
    mode_polling = os.getenv("MODE_POLLING", "false").lower()
    if mode_polling != "true":
        base_webhook_url = os.getenv("RAILWAY_PUBLIC_URL")
        if base_webhook_url:
            normalized_webhook_url = base_webhook_url.rstrip('/') + "/webhook"
            logger.info(f"Setting webhook to: {normalized_webhook_url}")
            await bot.set_webhook(url=normalized_webhook_url, allowed_updates=Update.ALL_TYPES)
            logger.info("Webhook set.")
        else:
            logger.warning("RAILWAY_PUBLIC_URL not set, webhook not configured.")
    else:
        logger.info("Mode polling activé, pas de configuration de webhook.")

@app.post("/webhook")
async def webhook_handler(request: Request):
    logger.info("--- Entrée dans webhook_handler ---")
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        context = ContextTypes.DEFAULT_TYPE(application=None, chat_id=update.effective_chat.id if update.effective_chat else None, user_id=update.effective_user.id if update.effective_user else None)
        await handle_photo(update, context)
        return {"status": "ok"}
    except json.JSONDecodeError:
        logger.error("Error decoding JSON from webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.exception("Error processing webhook")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        logger.info("--- Sortie de webhook_handler ---")

if __name__ == "__main__":
    mode_polling = os.getenv("MODE_POLLING", "false").lower()
    if mode_polling == "true":
        logger.info("Lancement en mode polling...")
        application = Application.builder().token(TOKEN).build()
        application.add_handler(MessageHandler(filters.PHOTO & (~filters.COMMAND), handle_photo))
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        logger.info("Lancement en mode webhook avec Uvicorn (localement)...")
        port = int(os.getenv("PORT", 8000))
        uvicorn.run(app, host="0.0.0.0", port=port)

