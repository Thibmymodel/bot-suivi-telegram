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

def extraire_followers_tiktok(text_annotations) -> str | None:
    logger.info(f"extraire_followers_tiktok: Début de l_extraction TikTok.")
    followers_keyword_annotations = []
    number_annotations = []

    if not text_annotations:
        logger.warning("extraire_followers_tiktok: Aucune annotation de texte fournie.")
        return None

    # Séparer les mots-clés et les nombres potentiels avec leurs positions
    for i, annotation in enumerate(text_annotations[1:]): # Ignorer la première annotation (texte complet)
        text = annotation.description.lower()
        vertices = annotation.bounding_poly.vertices
        # Calculer le centre Y approximatif du mot-clé ou du nombre
        avg_y = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4
        avg_x = (vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x) / 4

        if any(keyword in text for keyword in ["followers", "abonnés", "abonné", "fans", "abos"]):
            followers_keyword_annotations.append({"text": text, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
            logger.info(f"extraire_followers_tiktok: Mot-clé trouvé: 	'{text}	' à y={avg_y}, x={avg_x}")
        
        # Essayer de normaliser pour voir si c_est un nombre
        nombre_normalise_test = normaliser_nombre_followers(text)
        if nombre_normalise_test:
            # Vérifier que ce n_est pas un format heure comme XX:XX
            if not re.fullmatch(r"\d{1,2}:\d{2}", text):
                number_annotations.append({"text": text, "normalized": nombre_normalise_test, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                logger.info(f"extraire_followers_tiktok: Nombre potentiel trouvé: 	'{text}	' (normalisé: {nombre_normalise_test}) à y={avg_y}, x={avg_x}")
            else:
                logger.info(f"extraire_followers_tiktok: Nombre ignoré (format heure): 	'{text}	'")
        elif text.replace(".", "").replace(",", "").isdigit(): # Pour les nombres sans k/M mais avec points/virgules
             nombre_simple = text.replace(".", "").replace(",", "")
             if not re.fullmatch(r"\d{1,2}:\d{2}", text):
                number_annotations.append({"text": text, "normalized": nombre_simple, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                logger.info(f"extraire_followers_tiktok: Nombre simple potentiel trouvé: 	'{text}	' (normalisé: {nombre_simple}) à y={avg_y}, x={avg_x}")
             else:
                logger.info(f"extraire_followers_tiktok: Nombre simple ignoré (format heure): 	'{text}	'")

    if not followers_keyword_annotations:
        logger.warning("extraire_followers_tiktok: Aucun mot-clé de followers trouvé.")
        # Fallback: si on a 3 nombres groupés comme Suivis / Followers / J_aime
        if len(number_annotations) >= 3:
            # Trier par position X pour obtenir l_ordre Suivis, Followers, J_aime
            number_annotations.sort(key=lambda ann: ann["avg_x"])
            # Vérifier s_ils sont à peu près sur la même ligne (Y)
            if abs(number_annotations[0]["avg_y"] - number_annotations[1]["avg_y"]) < 20 and \ 
               abs(number_annotations[1]["avg_y"] - number_annotations[2]["avg_y"]) < 20: 
                logger.info(f"extraire_followers_tiktok (Fallback mots-clés absents): 3 nombres alignés trouvés. Sélection du 2ème: {number_annotations[1][	"normalized	"]}")
                return number_annotations[1]["normalized"]
        logger.warning("extraire_followers_tiktok (Fallback mots-clés absents): Conditions non remplies pour le fallback des 3 nombres.")
        return None

    # Chercher le nombre le plus proche (généralement au-dessus) du mot-clé "followers"
    best_candidate = None
    min_distance = float(	"inf")

    for kw_ann in followers_keyword_annotations:
        for num_ann in number_annotations:
            # Le nombre doit être au-dessus ou très légèrement en dessous du mot-clé, et proche horizontalement
            y_diff = kw_ann["avg_y"] - num_ann["avg_y"] # Positif si le nombre est au-dessus
            x_diff = abs(kw_ann["avg_x"] - num_ann["avg_x"])
            
            logger.debug(f"extraire_followers_tiktok: Comparaison: kw=	'{kw_ann["text"]}	' (y={kw_ann["avg_y"]}) avec num=	'{num_ann["text"]}	' (y={num_ann["avg_y"]}). y_diff={y_diff}, x_diff={x_diff}")

            # Critères: nombre au-dessus (y_diff > -5, tolérance pour légère superposition) et pas trop loin horizontalement
            # et le nombre ne doit pas être trop petit (ex: ignorer "22" Suivis si on cherche des milliers de followers)
            # On s_attend à ce que le nombre de followers soit plus grand que le nombre de suivis.
            if y_diff > -15 and x_diff < 100: # Le nombre est au-dessus ou très proche, et aligné horizontalement
                # Simple distance euclidienne pour départager si plusieurs candidats proches
                distance = (y_diff**2 + x_diff**2)**0.5
                if distance < min_distance:
                    # Éviter de prendre un nombre comme "20" (de "20 | Ecole de graphisme") si le mot-clé est "followers"
                    # et qu_un autre nombre plus plausible existe.
                    # Pour l_image test, le "1326" est au-dessus de "Followers"
                    if kw_ann["text"] == "followers" and int(num_ann["normalized"]) > 100: # Heuristique simple
                        min_distance = distance
                        best_candidate = num_ann["normalized"]
                        logger.info(f"extraire_followers_tiktok: Nouveau meilleur candidat: {best_candidate} (distance: {min_distance} de 	'{kw_ann["text"]}	')")
                    elif kw_ann["text"] != "followers": # Pour les autres mots-clés, être moins strict
                        min_distance = distance
                        best_candidate = num_ann["normalized"]
                        logger.info(f"extraire_followers_tiktok: Nouveau meilleur candidat (autre mot-clé): {best_candidate} (distance: {min_distance} de 	'{kw_ann["text"]}	')")
    
    if best_candidate:
        logger.info(f"extraire_followers_tiktok: Nombre de followers final extrait: {best_candidate}")
        return best_candidate
    else:
        logger.warning("extraire_followers_tiktok: Aucun candidat de followers n_a pu être sélectionné après analyse spatiale.")
        return None

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("--- Entrée dans handle_photo ---")
    assistant = "INCONNU"
    today = datetime.datetime.now().strftime("%d/%m/%Y")
    message_status_general = f"🤖 {today} - {assistant} - ❌ Analyse OCR impossible ❌"
    donnees_extraites_ok = False
    reply_message_exists_for_error_handling = False # Pour savoir si on peut récupérer le topic_name en cas d_erreur précoce
    topic_name_for_error_handling = ""

    try:
        message = update.message
        if not message or not message.photo:
            logger.info("handle_photo: Message None ou sans photo, sortie.")
            return # Le message d_échec sera envoyé dans le finally

        reply = message.reply_to_message
        if not reply or not hasattr(reply, "forum_topic_created") or not reply.forum_topic_created:
            logger.info("handle_photo: Pas une réponse à un topic valide, sortie.")
            return # Le message d_échec sera envoyé dans le finally
        
        reply_message_exists_for_error_handling = True
        topic_name = reply.forum_topic_created.name
        topic_name_for_error_handling = topic_name # Sauvegarder pour le bloc except

        if not topic_name.startswith("SUIVI "):
            logger.info(f"handle_photo: Nom du topic 	'{topic_name}	' non conforme, sortie.")
            return # Le message d_échec sera envoyé dans le finally
        
        assistant = topic_name.replace("SUIVI ", "").strip().upper()
        logger.info(f"handle_photo: Assistant extrait: 	'{assistant}	'")
        message_status_general = f"🤖 {today} - {assistant} - ❌ Analyse OCR impossible ❌" # Mettre à jour avec l_assistant correct

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        img_bytes_io = io.BytesIO()
        await file.download_to_memory(img_bytes_io)
        img_bytes_io.seek(0)
        img_content = img_bytes_io.read()

        image = Image.open(io.BytesIO(img_content))
        width, height = image.size
        # Le recadrage à 40% est conservé, car il semble cibler la zone d_intérêt
        cropped_image = image.crop((0, 0, width, int(height * 0.4)))
        enhanced_image = ImageOps.autocontrast(cropped_image)
        byte_arr = io.BytesIO()
        enhanced_image.save(byte_arr, format=	'PNG	')
        content_vision = byte_arr.getvalue()

        image_vision = vision.Image(content=content_vision)
        response = vision_client.text_detection(image=image_vision)
        texts_annotations_vision = response.text_annotations # Conserver toutes les annotations pour l_analyse spatiale

        if response.error.message:
            logger.error(f"handle_photo: Erreur API Google Vision: {response.error.message}")
            raise Exception(f"Erreur Google Vision: {response.error.message}")

        ocr_text_full = texts_annotations_vision[0].description if texts_annotations_vision else ""
        logger.info(f"🔍 OCR Google Vision brut (premiers 500 caractères):\n{ocr_text_full[:500]}")

        if not ocr_text_full:
            logger.warning("handle_photo: OCR n_a retourné aucun texte.")
            return # Le message d_échec sera envoyé dans le finally

        # Identification du réseau (basée sur le texte complet)
        if "getallmylinks.com" in ocr_text_full.lower(): reseau = "instagram"
        elif "beacons.ai" in ocr_text_full.lower(): reseau = "twitter"
        # Pour TikTok, on se fie plus à la présence de mots-clés spécifiques à TikTok dans l_ensemble du texte
        elif "tiktok" in ocr_text_full.lower() or any(k in ocr_text_full.lower() for k in ["followers", "j_aime", "abonnés", "abonné", "fans", "suivis"]):
            reseau = "tiktok"
        elif "threads" in ocr_text_full.lower(): reseau = "threads"
        elif any(x in ocr_text_full.lower() for x in ["modifier le profil", "suivi(e)s", "publications"]):
            reseau = "instagram"
        else: reseau = "instagram"; logger.info("Réseau non clairement identifié, par défaut Instagram.")
        logger.info(f"handle_photo: Réseau identifié: {reseau}")

        # Extraction Username (basée sur le texte complet)
        usernames_found = re.findall(r"@([a-zA-Z0-9_.-]{3,})", ocr_text_full)
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
        urls = re.findall(r"(getallmylinks\.com|beacons\.ai|linktr\.ee|tiktok\.com)/([a-zA-Z0-9_.-]+)", ocr_text_full, re.IGNORECASE)
        if username == "Non trouvé" and urls:
            for _, u_from_url in urls:
                match_url = get_close_matches(u_from_url.lower(), reseau_handles, n=1, cutoff=0.7)
                if match_url: username = match_url[0]; break
                if username == "Non trouvé": username = u_from_url
        username = corriger_username(username, reseau)
        logger.info(f"🕵️ Username final : 	'{username}	' (réseau : {reseau})")

        # Extraction Abonnés
        abonnés = None
        if reseau == "tiktok":
            abonnés = extraire_followers_tiktok(texts_annotations_vision) # Passer toutes les annotations
        else: # Pour Instagram, Threads, etc.
            match_explicit = re.search(r"(\d[\d.,\s]*[kKmM]?)\s*(?:abonnés|followers|suivies|suivi\(e\)s|abonné)", ocr_text_full, re.IGNORECASE)
            if match_explicit: abonnés = normaliser_nombre_followers(match_explicit.group(1))
            if not abonnés:
                numbers_extracted_int = []
                raw_numbers = re.findall(r"(\d[\d.,\s]*[kKmM]?)", ocr_text_full)
                for num_str in raw_numbers:
                    val_norm = normaliser_nombre_followers(num_str)
                    if val_norm: numbers_extracted_int.append(int(val_norm))
                if len(numbers_extracted_int) >= 3: abonnés = str(numbers_extracted_int[1])
                elif len(numbers_extracted_int) == 2 and reseau == "instagram": abonnés = str(numbers_extracted_int[1])
                elif len(numbers_extracted_int) == 1 and reseau == "instagram": abonnés = str(numbers_extracted_int[0])
        logger.info(f"handle_photo: Abonnés extraits ({reseau}): {abonnés}")

        if not username or username == "Non trouvé" or not abonnés:
            logger.warning(f"Données incomplètes: Username (	'{username}	') ou Abonnés (	'{abonnés}	') pour {reseau}.")
            donnees_extraites_ok = False
        else:
            donnees_extraites_ok = True

        if message.message_id in already_processed:
            logger.info(f"⚠️ Message ID {message.message_id} déjà traité, on ignore.")
            return # Ne pas envoyer de message au General si déjà traité
        already_processed.add(message.message_id)

        if donnees_extraites_ok:
            username_to_sheet = f"@{username}"
            abonnés_to_sheet = str(abonnés)
            row = [today, assistant, reseau, username_to_sheet, abonnés_to_sheet, ""]
            try:
                sheet.append_row(row)
                logger.info("handle_photo: Ligne ajoutée à Google Sheets.")
                message_status_general = f"🤖 {today} - {assistant} - ✅ 1 compte détecté et ajouté ✅"
                # NE PLUS ENVOYER DE MESSAGE AU TOPIC DE L_ASSISTANT
                # msg_topic_assistant = f"📊 {today} - {assistant} - {reseau.capitalize()} @{username} ({abonnés}) ajouté ✅"
                # await bot.send_message(chat_id=GROUP_ID, text=msg_topic_assistant, message_thread_id=message.message_thread_id)
                # logger.info("Message de confirmation envoyé au topic de l_assistant.") 
            except Exception as e_sheet:
                logger.error(f"handle_photo: Erreur lors de l_ajout à Google Sheets: {e_sheet}")
                message_status_general = f"🤖 {today} - {assistant} - ⚠️ Erreur écriture Sheets ⚠️"
                # Si l_écriture Sheets échoue, on n_envoie pas non plus au topic de l_assistant (déjà commenté)
        # Si donnees_extraites_ok est False, message_status_general est déjà "Analyse OCR impossible" ou similaire

    except Exception as e:
        logger.exception("❌ Erreur globale dans handle_photo")
        # Essayer de récupérer le nom de l_assistant même en cas d_erreur précoce
        assistant_nom_erreur = assistant
        if assistant == "INCONNU" and reply_message_exists_for_error_handling and topic_name_for_error_handling.startswith("SUIVI "):
            assistant_nom_erreur = topic_name_for_error_handling.replace("SUIVI ", "").strip().upper()
        message_status_general = f"🤖 {today} - {assistant_nom_erreur} - ❌ Analyse OCR impossible ❌"
        # Pas d_envoi au topic de l_assistant en cas d_erreur globale non plus

    finally:
        logger.info(f"Message à envoyer au General: {message_status_general}")
        try:
            await bot.send_message(chat_id=GROUP_ID, text=message_status_general) # Pas de message_thread_id pour envoyer au General
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
            normalized_webhook_url = base_webhook_url.rstrip(	"/	") + "/webhook"
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

