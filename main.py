#!/usr/bin/env python3
import json
import io
import re
import datetime
import logging
import os
import traceback # Assurez-vous que traceback est importé
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
    # exit() # Commenté pour permettre l_exécution dans des environnements sans cette variable pour tests unitaires
try:
    creds_gspread_dict = json.loads(google_creds_gspread_json_str)
    gspread_creds = ServiceAccountCredentials.from_service_account_info(creds_gspread_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(gspread_creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
    logger.info("Connexion à Google Sheets réussie.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Sheets: {e}")
    logger.error(traceback.format_exc())
    # exit() # Commenté pour permettre l_exécution

# Initialisation Google Vision AI
google_creds_vision_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if not google_creds_vision_json_str:
    logger.error("La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS (pour Vision) n_est pas définie.")
    # exit()
try:
    creds_vision_dict = json.loads(google_creds_vision_json_str)
    vision_creds = ServiceAccountCredentials.from_service_account_info(creds_vision_dict)
    vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)
    logger.info("Client Google Vision AI initialisé avec succès.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Vision AI: {e}")
    logger.error(traceback.format_exc())
    # exit()

bot = Bot(TOKEN)
already_processed = set()

with open("known_handles.json", "r", encoding="utf-8") as f:
    KNOWN_HANDLES = json.load(f)

def corriger_username(username: str, reseau: str) -> str:
    if reseau == "instagram" and username.startswith("@"):
        return username[1:]
    return username

def normaliser_nombre_followers(nombre_str: str) -> str | None:
    if not isinstance(nombre_str, str):
        return None
    nombre_str_test = nombre_str.strip()
    if not re.match(r"^[\d.,\s]*[kKm]?$", nombre_str_test, re.IGNORECASE):
        logger.debug(f"normaliser_nombre_followers: 	'{nombre_str_test}' ne correspond pas au format attendu.")
        return None

    nombre_str_cleaned_spaces = nombre_str_test.replace(" ", "")
    nombre_str_clean = nombre_str_cleaned_spaces.lower().replace(".", "").replace(",", "")
    valeur = None
    try:
        if "k" in nombre_str_clean:
            nombre_str_clean = re.sub(r"(\d)\s+k", r"\1k", nombre_str_clean)
            if not re.match(r"^\d+k$", nombre_str_clean):
                logger.debug(f"normaliser_nombre_followers: Format 'k' invalide pour 	'{nombre_str_clean}'")
                return None
            valeur = str(int(float(nombre_str_clean.replace("k", "")) * 1000))
        elif "m" in nombre_str_clean:
            nombre_str_clean = re.sub(r"(\d)\s+m", r"\1m", nombre_str_clean)
            if not re.match(r"^\d+m$", nombre_str_clean):
                logger.debug(f"normaliser_nombre_followers: Format 'm' invalide pour 	'{nombre_str_clean}'")
                return None
            valeur = str(int(float(nombre_str_clean.replace("m", "")) * 1000000))
        else:
            if not nombre_str_clean.isdigit():
                logger.debug(f"normaliser_nombre_followers: 	'{nombre_str_clean}' n'est pas un digit après nettoyage.")
                return None
            valeur = str(int(nombre_str_clean))
    except ValueError:
        logger.warning(f"normaliser_nombre_followers: ValueError lors de la conversion de 	'{nombre_str_clean}'")
        return None
    return valeur


def fusionner_nombres_adjacents(number_annotations_list_input, reseau_nom_log="inconnu"):
    if not number_annotations_list_input or len(number_annotations_list_input) < 2:
        return number_annotations_list_input

    logger.info(f"fusionner_nombres_adjacents ({reseau_nom_log}): Début de la fusion. Nombres entrants: {len(number_annotations_list_input)}")
    sorted_numbers = sorted(number_annotations_list_input, key=lambda ann: (ann['avg_y'], ann['avg_x']))
    
    merged_numbers_final = []
    processed_indices = set()
    
    i = 0
    while i < len(sorted_numbers):
        if i in processed_indices:
            i += 1
            continue

        current_ann_data = sorted_numbers[i]
        current_text_original = current_ann_data['annotation'].description
        logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}): Traitement du bloc {i}: '{current_text_original}' à y={current_ann_data['avg_y']:.2f}, x={current_ann_data['avg_x']:.2f}")

        if i + 1 < len(sorted_numbers) and (i + 1) not in processed_indices:
            next_ann_data = sorted_numbers[i+1]
            next_text_original = next_ann_data['annotation'].description
            logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):  Vérification avec bloc suivant {i+1}: '{next_text_original}' à y={next_ann_data['avg_y']:.2f}, x={next_ann_data['avg_x']:.2f}")

            y_diff_merge = abs(current_ann_data['avg_y'] - next_ann_data['avg_y'])
            current_vertices = current_ann_data['annotation'].bounding_poly.vertices
            next_vertices = next_ann_data['annotation'].bounding_poly.vertices
            current_end_x = max(v.x for v in current_vertices)
            next_start_x = min(v.x for v in next_vertices)
            x_gap = next_start_x - current_end_x
            
            Y_THRESHOLD_MERGE = 25
            X_GAP_THRESHOLD_MERGE = 45
            X_OVERLAP_THRESHOLD_MERGE = -10

            logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    y_diff_merge={y_diff_merge:.2f} (Seuil Y: {Y_THRESHOLD_MERGE})")
            logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    x_gap={x_gap:.2f} (Seuil X: {X_OVERLAP_THRESHOLD_MERGE} <= gap < {X_GAP_THRESHOLD_MERGE})")

            is_current_simple_num_text = bool(re.fullmatch(r"\d+", current_text_original.strip()))
            is_next_simple_num_text = bool(re.fullmatch(r"\d+", next_text_original.strip()))
            logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    '{current_text_original}' est num simple: {is_current_simple_num_text}, '{next_text_original}' est num simple: {is_next_simple_num_text}")

            if (is_current_simple_num_text and is_next_simple_num_text and
                y_diff_merge < Y_THRESHOLD_MERGE and 
                X_OVERLAP_THRESHOLD_MERGE <= x_gap < X_GAP_THRESHOLD_MERGE):
                
                combined_text_original = f"{current_text_original} {next_text_original}"
                combined_normalized = normaliser_nombre_followers(combined_text_original)
                
                if combined_normalized:
                    logger.info(f"fusionner_nombres_adjacents ({reseau_nom_log}): FUSION RÉUSSIE de '{current_text_original}' et '{next_text_original}' -> '{combined_text_original}' (normalisé: {combined_normalized})")
                    merged_ann_entry = {
                        "text": combined_text_original.lower().strip(), 
                        "normalized": combined_normalized,
                        "avg_y": current_ann_data['avg_y'], 
                        "avg_x": current_ann_data['avg_x'], 
                        "annotation": current_ann_data['annotation'] 
                    }
                    merged_numbers_final.append(merged_ann_entry)
                    processed_indices.add(i)
                    processed_indices.add(i+1)
                    i += 2 
                    continue 
                else:
                    logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    Texte combiné '{combined_text_original}' non normalisable.")
            else:
                logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    Critères de fusion non remplis.")
        
        merged_numbers_final.append(current_ann_data)
        processed_indices.add(i)
        i += 1
            
    logger.info(f"fusionner_nombres_adjacents ({reseau_nom_log}): Fin de la fusion. Nombres sortants: {len(merged_numbers_final)}")
    return merged_numbers_final

def extraire_followers_spatial(text_annotations, mots_cles_specifiques, reseau_nom="inconnu") -> str | None:
    try:
        logger.info(f"extraire_followers_spatial ({reseau_nom}): --- Début de l_extraction spatiale ---")
        keyword_annotations_list = []
        number_annotations_list = []

        if not text_annotations:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucune annotation de texte fournie.")
            return None
        
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre total d_annotations reçues: {len(text_annotations)}")
        if "instagram" in reseau_nom.lower(): # Log spécifique pour Instagram
            logger.info(f"extraire_followers_spatial ({reseau_nom}): DÉTAIL ANNOTATIONS BRUTES (pour débogage fusion Instagram):")
            for idx, ann_detail in enumerate(text_annotations[1:]):
                desc = ann_detail.description
                poly = [(v.x, v.y) for v in ann_detail.bounding_poly.vertices]
                logger.info(f"  Ann.Brute {idx}: '{desc}' | Poly: {poly}")

        if len(text_annotations) > 1:
            logger.info(f"extraire_followers_spatial ({reseau_nom}): Premières annotations (description et position Y moyenne):")
            for i_log, annotation_log in enumerate(text_annotations[1:6]): 
                try:
                    if hasattr(annotation_log, 'description') and hasattr(annotation_log, 'bounding_poly') and hasattr(annotation_log.bounding_poly, 'vertices') and len(annotation_log.bounding_poly.vertices) >=4:
                        vertices_log = annotation_log.bounding_poly.vertices
                        avg_y_log = (vertices_log[0].y + vertices_log[1].y + vertices_log[2].y + vertices_log[3].y) / 4
                        logger.info(f"  - Ann {i_log+1}: 	'{annotation_log.description}' (avg_y: {avg_y_log})")
                    else:
                        logger.warning(f"extraire_followers_spatial ({reseau_nom}): Annotation initiale {i_log+1} malformée: {annotation_log}")
                except Exception as e_log_ann:
                    logger.error(f"extraire_followers_spatial ({reseau_nom}): Erreur lors du logging de l_annotation initiale {i_log+1}: {e_log_ann}. Annotation: {annotation_log}")

        for i, annotation in enumerate(text_annotations[1:]):
            try:
                if not hasattr(annotation, 'description') or not hasattr(annotation, 'bounding_poly'):
                    logger.warning(f"extraire_followers_spatial ({reseau_nom}): Annotation {i} n_a pas les attributs requis (description/bounding_poly), ignorée. Contenu: {annotation}")
                    continue

                text = annotation.description.lower().strip()
                
                if not hasattr(annotation.bounding_poly, 'vertices') or len(annotation.bounding_poly.vertices) < 4:
                    logger.warning(f"extraire_followers_spatial ({reseau_nom}): Annotation {i} ('{text}') n_a pas de bounding_poly.vertices valides, ignorée.")
                    continue
                    
                vertices = annotation.bounding_poly.vertices
                avg_y = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4
                avg_x = (vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x) / 4
                logger.debug(f"extraire_followers_spatial ({reseau_nom}): Traitement annotation {i}: 	'{text}' (avg_y={avg_y}, avg_x={avg_x})")

                if any(keyword.lower() in text for keyword in mots_cles_specifiques):
                    keyword_annotations_list.append({"text": text, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                    logger.info(f"extraire_followers_spatial ({reseau_nom}): MOT-CLÉ TROUVÉ: 	'{text}' à y={avg_y}, x={avg_x}")
                
                if re.search(r"\d", text) and re.match(r"^[\d.,\s]*[kKm]?$", text, re.IGNORECASE):
                    nombre_normalise_test = normaliser_nombre_followers(annotation.description) 
                    if nombre_normalise_test:
                        if not re.fullmatch(r"\d{1,2}:\d{2}", text): 
                            number_annotations_list.append({"text": text, "normalized": nombre_normalise_test, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                            logger.info(f"extraire_followers_spatial ({reseau_nom}): NOMBRE POTENTIEL TROUVÉ: 	'{text}' (original: '{annotation.description}', normalisé: {nombre_normalise_test}) à y={avg_y}, x={avg_x}")
                        else:
                            logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre 	'{text}' ignoré (format heure).")
                    else:
                        logger.debug(f"extraire_followers_spatial ({reseau_nom}): 	'{text}' (original: '{annotation.description}') non normalisable en nombre.")
                else:
                    logger.debug(f"extraire_followers_spatial ({reseau_nom}): Annotation 	'{text}' ne semble pas être un nombre (basé sur regex), ignorée pour la normalisation.")
            except Exception as e_loop_ann:
                logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR INATTENDUE lors du traitement de l_annotation {i}: {e_loop_ann}")
                logger.error(f"extraire_followers_spatial ({reseau_nom}): Annotation problématique: {annotation}")
                logger.error(traceback.format_exc())
                continue 

        logger.info(f"extraire_followers_spatial ({reseau_nom}): Fin de la boucle d_analyse des annotations.")
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de mots-clés trouvés: {len(keyword_annotations_list)}")
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de nombres potentiels AVANT fusion: {len(number_annotations_list)}")
        for idx, na in enumerate(number_annotations_list):
            logger.info(f"  - Nombre AVANT fusion {idx}: '{na['annotation'].description}' (normalisé: {na['normalized']}) à y={na['avg_y']:.2f}, x={na['avg_x']:.2f}")

        if number_annotations_list:
            number_annotations_list = fusionner_nombres_adjacents(number_annotations_list, reseau_nom)
            logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de nombres potentiels APRÈS fusion: {len(number_annotations_list)}")
            for idx, na in enumerate(number_annotations_list):
                 logger.info(f"  - Nombre APRÈS fusion {idx}: '{na['text']}' (normalisé: {na['normalized']}) à y={na['avg_y']:.2f}, x={na['avg_x']:.2f}")
        
        if not keyword_annotations_list:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucun mot-clé de followers trouvé. Tentative de fallback basée sur la position des nombres.")
            if len(number_annotations_list) >= 3:
                number_annotations_list.sort(key=lambda ann: ann['avg_x'])
                logger.info(f"extraire_followers_spatial ({reseau_nom}) (Fallback): Nombres triés par X: {[na['text'] for na in number_annotations_list]}")
                if (abs(number_annotations_list[0]['avg_y'] - number_annotations_list[1]['avg_y']) < 30 and 
                    abs(number_annotations_list[1]['avg_y'] - number_annotations_list[2]['avg_y']) < 30):
                    logger.info(f"extraire_followers_spatial ({reseau_nom}) (Fallback): 3 nombres alignés trouvés. Sélection du 2ème: {number_annotations_list[1]['normalized']}")
                    return number_annotations_list[1]['normalized']
                else:
                    logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback): Les 3 premiers nombres ne sont pas alignés en Y.")
            else:
                logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback): Pas assez de nombres ({len(number_annotations_list)}) pour le fallback des 3 nombres.")
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Conditions de fallback non remplies.")
            if number_annotations_list:
                number_annotations_list.sort(key=lambda x: int(x.get("normalized", "0")), reverse=True)
                if number_annotations_list and number_annotations_list[0]['normalized']:
                    logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback - Aucun mot-clé): Sélection du plus grand nombre: {number_annotations_list[0]['normalized']}")
                    return number_annotations_list[0]['normalized']
            return None

        best_candidate = None
        min_distance = float('inf')

        logger.info(f"extraire_followers_spatial ({reseau_nom}): Recherche du meilleur candidat basé sur la proximité du mot-clé.")
        for kw_ann in keyword_annotations_list:
            logger.info(f"  - Analyse pour mot-clé: 	'{kw_ann['text']}' à y={kw_ann['avg_y']}")
            for num_ann in number_annotations_list:
                y_diff = num_ann['avg_y'] - kw_ann['avg_y']
                x_diff = abs(kw_ann['avg_x'] - num_ann['avg_x'])
                
                logger.debug(f"    - Comparaison avec nombre: 	'{num_ann['text']}' (norm: {num_ann['normalized']}) à y={num_ann['avg_y']}. y_diff={y_diff:.2f}, x_diff={x_diff:.2f}")

                if y_diff > -25 and y_diff < 100 and x_diff < 150: 
                    distance = (y_diff**2 + x_diff**2)**0.5
                    logger.debug(f"      Candidat potentiel. Distance: {distance:.2f}")
                    if distance < min_distance:
                        try:
                            min_distance = distance
                            best_candidate = num_ann['normalized']
                            logger.info(f"      NOUVEAU MEILLEUR CANDIDAT (pour '{kw_ann['text']}'): {best_candidate} (distance: {min_distance:.2f})")
                        except ValueError:
                            logger.warning(f"      Impossible de convertir 	'{num_ann['normalized']}' en entier pour la comparaison.")
                    else:
                        logger.debug(f"      Distance {distance:.2f} non inférieure à min_distance {min_distance:.2f}.")
                else:
                    logger.debug(f"      Critères de position (y_diff > -25 ET y_diff < 100 ET x_diff < 150) non remplis.")
        
        if best_candidate:
            logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de followers final extrait: {best_candidate}")
            return best_candidate
        else:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucun candidat de followers n_a pu être sélectionné après analyse spatiale.")
            if number_annotations_list:
                number_annotations_list.sort(key=lambda x: int(x.get("normalized", "0")), reverse=True)
                logger.info(f"extraire_followers_spatial ({reseau_nom}) (Fallback final): Nombres triés par valeur: {[na['text'] for na in number_annotations_list]}")
                if number_annotations_list and number_annotations_list[0]['normalized']:
                     logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback final): Sélection du plus grand nombre: {number_annotations_list[0]['normalized']}")
                     return number_annotations_list[0]['normalized']
            logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback final): Aucun nombre à retourner.")
            return None

    except Exception as e_global_spatial:
        logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR GLOBALE INATTENDUE DANS LA FONCTION: {e_global_spatial}")
        logger.error(traceback.format_exc())
        return None

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("--- Entrée dans handle_photo ---")
    assistant = "INCONNU"
    today = datetime.datetime.now().strftime("%d/%m/%Y")
    message_status_general = None 
    donnees_extraites_ok = False
    reply_message_exists_for_error_handling = False
    topic_name_for_error_handling = ""
    username = "Non trouvé"
    reseau = "instagram" 
    abonnés = None
    action_tentee = False

    try:
        message = update.message
        if not message or not message.photo:
            logger.info("handle_photo: Message None ou sans photo. Aucune action.")
            return

        reply = message.reply_to_message
        if not reply or not hasattr(reply, "forum_topic_created") or not reply.forum_topic_created:
            logger.info("handle_photo: Le message n_est pas une réponse à un message de création de sujet de forum. Aucune action.")
            return
        
        topic_name_for_error_handling = reply.forum_topic_created.name
        reply_message_exists_for_error_handling = True
        assistant = topic_name_for_error_handling
        logger.info(f"handle_photo: Traitement pour l_assistant: {assistant}")

        file_id = message.photo[-1].file_id
        if file_id in already_processed:
            logger.info(f"handle_photo: Image {file_id} déjà traitée. Ignorée.")
            return
        already_processed.add(file_id)

        new_file = await context.bot.get_file(file_id)
        img_bytes = await new_file.download_as_bytearray()
        img_content = bytes(img_bytes)

        image = Image.open(io.BytesIO(img_content))
        width, height = image.size
        
        # MODIFICATION: Ajustement du recadrage pour Twitter
        crop_height_ratio = 0.4 
        # Déterminer le réseau avant le crop pour ajuster le ratio si c_est Twitter
        # Pour cela, on fait une première passe OCR sur une plus grande partie de l_image
        # ou on se base sur le nom de l_assistant si possible.
        # Pour l_instant, on va faire une détection de réseau basique sur le nom de l_assistant
        # et on affinera si besoin avec une pré-OCR.
        
        # Tentative de détection du réseau à partir du nom de l_assistant (nom du topic)
        # Cela suppose que le nom du topic contient le nom du réseau.
        # Si ce n_est pas fiable, il faudra une pré-analyse OCR.
        temp_reseau_detect = "instagram" # Par défaut
        if assistant:
            assistant_lower = assistant.lower()
            if "twitter" in assistant_lower or " x " in assistant_lower: # " x " pour éviter confusion avec d_autres mots
                temp_reseau_detect = "twitter"
            elif "tiktok" in assistant_lower:
                temp_reseau_detect = "tiktok"
            # Instagram est souvent le cas par défaut ou moins marqué

        if temp_reseau_detect == "twitter":
            crop_height_ratio = 0.65 # Ratio spécifique pour Twitter
            logger.info(f"handle_photo: Ratio de crop ajusté à {crop_height_ratio} pour Twitter (détecté via nom assistant).")
        
        cropped_image = image.crop((0, 0, width, int(height * crop_height_ratio)))
        enhanced_image = ImageOps.autocontrast(cropped_image)
        byte_arr = io.BytesIO()
        enhanced_image.save(byte_arr, format='PNG')
        content_vision = byte_arr.getvalue()

        image_vision = vision.Image(content=content_vision)
        response = vision_client.text_detection(image=image_vision)
        texts_annotations_vision = response.text_annotations

        if response.error.message:
            logger.error(f"handle_photo: Erreur API Google Vision: {response.error.message}")
            await bot.send_message(chat_id=GROUP_ID, text=f"Erreur OCR Google Vision pour {assistant}: {response.error.message}", message_thread_id=message.message_thread_id)
            return

        ocr_text_full = texts_annotations_vision[0].description if texts_annotations_vision and len(texts_annotations_vision) > 0 else ""
        logger.info(f"🔍 OCR Google Vision brut (premiers 500 caractères) pour {assistant}:\n{ocr_text_full[:500]}")

        if not ocr_text_full:
            logger.warning(f"handle_photo: OCR n_a retourné aucun texte pour {assistant}.")
            await bot.send_message(chat_id=GROUP_ID, text=f"L_OCR n_a retourné aucun texte pour l_image de {assistant}.", message_thread_id=message.message_thread_id)
            return

        ocr_lower = ocr_text_full.lower()
        if "tiktok" in ocr_lower or "j_aime" in ocr_lower or "j’aime" in ocr_lower:
            reseau = "tiktok"
        elif "twitter" in ocr_lower or "tweets" in ocr_lower or "reposts" in ocr_lower or "abonnement" in ocr_lower or "abonnés" in ocr_lower:
            reseau = "twitter"
        elif "instagram" in ocr_lower or "publications" in ocr_lower or "getallmylinks.com" in ocr_lower or "modifier le profil" in ocr_lower:
            reseau = "instagram"
        elif "threads" in ocr_lower:
            reseau = "threads"
        elif "beacons.ai" in ocr_lower:
            if "twitter" in ocr_lower: reseau = "twitter"
            else: reseau = "instagram"
        else: 
            reseau = temp_reseau_detect # Utiliser le réseau détecté via nom d_assistant si rien d_autre
            logger.info(f"Réseau non clairement identifié par OCR, déduit de/mis par défaut à: {reseau}")
        logger.info(f"handle_photo: Réseau identifié pour {assistant}: {reseau}")

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
        logger.info(f"🕵️ Username final pour {assistant}: 	'{username}' (réseau : {reseau})")

        abonnés = None
        if reseau == "tiktok":
            mots_cles_tiktok = ["followers", "abonnés", "abonné", "fans", "abos"]
            abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_tiktok, f"tiktok ({assistant})")
        elif reseau == "instagram":
            mots_cles_instagram = ["followers", "abonnés", "abonné", "suivi(e)s", "suivis"]
            abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_instagram, f"instagram ({assistant})")
        elif reseau == "twitter":
            mots_cles_twitter = ["abonnés", "abonné", "followers", "suivies", "suivis", "abonnements"]
            abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_twitter, f"twitter ({assistant})")
        elif reseau == "threads":
             mots_cles_threads = ["followers", "abonnés", "abonné"]
             abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_threads, f"threads ({assistant})")
        else:
            mots_cles_generiques = ["followers", "abonnés", "abonné", "fans", "suivi(e)s", "suivis"]
            abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_generiques, f"générique ({reseau}, {assistant})")

        logger.info(f"handle_photo: Abonnés extraits pour {assistant} ({reseau}): {abonnés}")

        if username != "Non trouvé" and abonnés is not None:
            donnees_extraites_ok = True
            action_tentee = True
            try:
                sheet.append_row([today, assistant, reseau, username, abonnés, ""])
                logger.info(f"Données ajoutées à Google Sheets pour {assistant}: {today}, {reseau}, {username}, {abonnés}")
                message_status_general = f"OK ✅ {username} ({reseau}) -> {abonnés} followers."
            except Exception as e_gsheet:
                logger.error(f"Erreur lors de l_écriture sur Google Sheets pour {assistant}: {e_gsheet}")
                logger.error(traceback.format_exc())
                message_status_general = f"⚠️ Erreur Sheets pour {assistant} ({username}, {reseau}, {abonnés}). Détails dans les logs."
        else:
            action_tentee = True
            logger.warning(f"handle_photo: Données incomplètes pour {assistant}. Username: {username}, Abonnés: {abonnés}")
            message_status_general = f"❓ Données incomplètes pour {assistant}. Username: '{username}', Abonnés: '{abonnés}'. Réseau: {reseau}. OCR brut: {ocr_text_full[:150]}..."

    except Exception as e:
        logger.error(f"❌ Erreur globale dans handle_photo pour l_assistant {assistant if assistant != 'INCONNU' else 'non identifié'}:")
        logger.error(traceback.format_exc())
        message_status_general = f"🆘 Erreur critique bot pour {assistant if assistant != 'INCONNU' else 'image non identifiée'}. Détails dans les logs."
    
    finally:
        if message_status_general and GROUP_ID:
            try:
                target_thread_id = message.message_thread_id if hasattr(message, 'message_thread_id') else None
                if not target_thread_id and reply_message_exists_for_error_handling:
                    # Fallback si on n_a pas pu obtenir le thread_id du message entrant mais qu_on a celui du reply
                    # Cela peut arriver si le message n_est pas directement dans un topic mais y répond
                    # Cependant, le bot est censé répondre au topic où la commande a été initiée.
                    # Le message.message_thread_id devrait être le bon.
                    pass 
                
                await bot.send_message(chat_id=GROUP_ID, text=message_status_general, message_thread_id=target_thread_id)
                logger.info(f"Message de statut envoyé au groupe pour {assistant}: {message_status_general}")
            except Exception as e_send_status:
                logger.error(f"Erreur lors de l_envoi du message de statut au groupe pour {assistant}: {e_send_status}")
                logger.error(traceback.format_exc())
        elif not message_status_general and action_tentee:
             logger.warning(f"handle_photo: Un message de statut aurait dû être généré pour {assistant} mais ne l_a pas été.")
        elif not action_tentee:
            logger.info("handle_photo: Aucune action de traitement OCR n_a été tentée (probablement image déjà traitée ou non pertinente).")

async def main_bot():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_photo))
    
    mode_polling_str = os.getenv("MODE_POLLING", "true").lower()
    if mode_polling_str == "true":
        logger.info("Démarrage du bot en mode polling...")
        await application.run_polling()
    else:
        # Configuration pour webhook (simplifié, nécessite un serveur web comme FastAPI/Uvicorn)
        # Le Dockerfile est configuré pour lancer uvicorn main:app --host 0.0.0.0 --port $PORT
        # Il faudrait une vraie app FastAPI ici.
        # Pour l_instant, si MODE_POLLING n_est pas true, on ne fait rien dans ce script standalone.
        logger.info("Mode Webhook configuré (nécessite un serveur d_application externe comme FastAPI/Uvicorn).")
        logger.info("Ce script ne démarrera pas de serveur webhook lui-même.")
        logger.info("Assurez-vous que votre infrastructure (ex: Railway) lance ce bot via une app ASGI (comme avec uvicorn main:app).")
        # Exemple de ce à quoi ressemblerait l_intégration avec FastAPI:
        # from fastapi import FastAPI
        # app = FastAPI()
        # @app.post("/webhook") async def webhook(update_data: dict):
        #     update = Update.de_json(update_data, bot)
        #     await application.process_update(update)
        #     return {"status": "ok"}
        # Pour le déploiement, le point d_entrée serait `uvicorn main:app` et non `python main.py`
        # Et TELEGRAM_BOT_TOKEN, RAILWAY_PUBLIC_URL etc. seraient utilisés pour set_webhook.
        pass # En mode non-polling, ce script python direct ne fait rien de plus.

if __name__ == "__main__":
    import asyncio
    asyncio.run(main_bot())

