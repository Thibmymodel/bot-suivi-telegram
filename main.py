import json
import io
import re
import datetime
import logging
import os
import traceback 
from difflib import get_close_matches
from typing import Optional, List, Dict, Any, Tuple # Ajout pour typage

from telegram import Update, Bot, PhotoSize # Ajout de PhotoSize
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode # Ajout pour ParseMode
from PIL import Image, ImageOps, UnidentifiedImageError # Ajout de UnidentifiedImageError
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
    logger.error(traceback.format_exc())
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
    logger.error(traceback.format_exc())
    exit()

# Chargement des handles connus et mots-clés (repris de la version fonctionnelle)
KNOWN_HANDLES: Dict[str, List[str]] = {}
try:
    with open("known_handles.json", "r", encoding="utf-8") as f:
        KNOWN_HANDLES = json.load(f)
except FileNotFoundError:
    logger.warning(
        "Le fichier known_handles.json n_a pas été trouvé. L_identification des réseaux sera moins précise."
    )
except json.JSONDecodeError:
    logger.error(
        "Erreur lors du décodage de known_handles.json. Le fichier est peut-être corrompu."
    )

# Définitions des mots-clés et patterns (essentiel pour identifier_reseau_et_username et extraire_followers_spatial)
RESEAUX_SOCIALS_KEYWORDS = {
    "instagram": ["instagram", "followers", "abonnés", "suivi(e)s", "publications", "j_aime", "profil", "reels"],
    "twitter": ["twitter", "x.com", "abonnements", "abonnés", "followers", "following", "tweets", "profil"],
    "tiktok": ["tiktok", "abonnements", "followers", "j_aime", "profil", "amis", "pour toi"],
    # Ajouter d_autres réseaux si nécessaire, comme dans la version fusionnée
}

FOLLOWERS_KEYWORDS_SPECIFIC = {
    "instagram": ["followers", "abonnés", "suivi(e)s"],
    "twitter": ["abonnés", "followers"],
    "tiktok": ["followers", "abonnés"],
}

USERNAME_PATTERNS = {
    "instagram": r"@?([a-zA-Z0-9_.]{1,30})",
    "twitter": r"@?([a-zA-Z0-9_]{1,15})",
    "tiktok": r"@?([a-zA-Z0-9_.-]+)",
}

ASSISTANT_TOPIC_KEYWORDS = ["SUIVI", "Suivi", "suivi"]

ptb_application = Application.builder().token(TOKEN).build() # Garder la structure de la version fusionnée pour la compatibilité future avec webhook

def get_assistant_from_topic_name(topic_name: Optional[str]) -> str:
    if not topic_name:
        return "INCONNU"
    for keyword in ASSISTANT_TOPIC_KEYWORDS:
        if topic_name.startswith(keyword):
            assistant_name = topic_name[len(keyword) :].strip()
            if assistant_name:
                return assistant_name
    return topic_name 

def corriger_username(username: str, reseau: str) -> str:
    if reseau == "instagram" and username.startswith("@"):
        return username[1:]
    return username.strip()

# --- AMÉLIORATIONS OCR INTÉGRÉES CI-DESSOUS ---

def normaliser_nombre_followers(nombre_str: Optional[str]) -> Optional[str]:
    if not nombre_str or not isinstance(nombre_str, str):
        return None

    nombre_str_test = nombre_str.strip()
    # Regex pour accepter chiffres, points, virgules, espaces, et k/K/m/M à la fin.
    if not re.match(r"^[\d.,\s]*[kKm]?$", nombre_str_test, re.IGNORECASE):
        logger.debug(f"normaliser_nombre_followers: 	Ó{nombre_str_test}	 ne correspond pas au format attendu.")
        return None

    nombre_str_clean = nombre_str_test.lower()
    valeur = None

    try:
        if "k" in nombre_str_clean or "m" in nombre_str_clean:
            # Gérer les espaces avant k/m et les virgules comme séparateurs décimaux
            nombre_str_clean = nombre_str_clean.replace(" ", "") # Enlever tous les espaces
            nombre_part_str = nombre_str_clean[:-1].replace(",", ".") # Remplacer virgule par point pour float
            
            if not re.match(r"^\d+(\.\d+)?[km]$", nombre_part_str + nombre_str_clean[-1]):
                logger.debug(f"normaliser_nombre_followers: Format 	Ók	 ou 	Óm	 invalide pour 	Ó{nombre_str_clean}	 (partie numérique: 	Ó{nombre_part_str}	)")
                return None
            
            multiplicateur = 1000 if "k" in nombre_str_clean else 1000000
            valeur = str(int(float(nombre_part_str) * multiplicateur))
        else:
            # Enlever tout ce qui n_est pas un chiffre (points, virgules, espaces)
            nombre_str_clean = re.sub(r"\D", "", nombre_str_test) 
            if not nombre_str_clean.isdigit():
                logger.debug(f"normaliser_nombre_followers: 	Ó{nombre_str_clean}	 (après sub non-digit) n_est pas un digit.")
                return None
            valeur = str(int(nombre_str_clean))
    except ValueError:
        logger.warning(f"normaliser_nombre_followers: ValueError lors de la conversion de 	Ó{nombre_str_clean}	 (original: 	Ó{nombre_str_test}	)")
        return None
    return valeur

def fusionner_nombres_adjacents(
    text_annotations: List[vision.entity_annotation.EntityAnnotation],
    max_pixel_gap: int = 30, 
    assistant_nom: str = "", # Ajouté pour cohérence des logs
) -> List[vision.entity_annotation.EntityAnnotation]:
    if not text_annotations or len(text_annotations) == 0:
        return text_annotations

    logger.info(f"fusionner_nombres_adjacents ({assistant_nom}): Début. Annotations: {len(text_annotations)}")

    potential_number_annotations = []
    for ann in text_annotations: 
        if re.search(r"\d", ann.description) and not re.fullmatch(r"\d{1,2}:\d{2}", ann.description):
            potential_number_annotations.append(ann)

    if not potential_number_annotations:
        logger.info(f"fusionner_nombres_adjacents ({assistant_nom}): Aucune annotation numérique pour fusion.")
        return text_annotations 

    potential_number_annotations.sort(key=lambda ann: (ann.bounding_poly.vertices[0].y + ann.bounding_poly.vertices[3].y) / 2)
    potential_number_annotations.sort(key=lambda ann: (ann.bounding_poly.vertices[0].x + ann.bounding_poly.vertices[1].x) / 2)

    merged_number_texts: List[vision.entity_annotation.EntityAnnotation] = []
    processed_indices = [False] * len(potential_number_annotations)

    for i in range(len(potential_number_annotations)):
        if processed_indices[i]:
            continue

        current_ann = potential_number_annotations[i]
        current_desc = current_ann.description
        current_vertices = list(current_ann.bounding_poly.vertices)

        for j in range(i + 1, len(potential_number_annotations)):
            if processed_indices[j]:
                continue
            next_ann = potential_number_annotations[j]
            current_mid_y = (current_vertices[0].y + current_vertices[3].y) / 2
            next_mid_y = (next_ann.bounding_poly.vertices[0].y + next_ann.bounding_poly.vertices[3].y) / 2
            current_right_x = max(v.x for v in current_vertices)
            next_left_x = min(v.x for v in next_ann.bounding_poly.vertices)
            approx_char_height = abs(current_vertices[3].y - current_vertices[0].y)
            y_diff = abs(current_mid_y - next_mid_y)
            gap_x = next_left_x - current_right_x

            if (y_diff < approx_char_height * 0.75 and 0 <= gap_x < max_pixel_gap and re.search(r"\d", next_ann.description)):
                log_current_desc = current_desc
                log_next_ann_desc = next_ann.description
                logger.info(f"fusionner_nombres_adjacents ({assistant_nom}): Fusion de 	Ó{log_current_desc}	 avec 	Ó{log_next_ann_desc}	 (gap_x: {gap_x:.0f})")
                current_desc += " " + next_ann.description
                new_vertices = [
                    vision.Vertex(x=min(current_vertices[0].x, next_ann.bounding_poly.vertices[0].x), y=min(current_vertices[0].y, next_ann.bounding_poly.vertices[0].y)),
                    vision.Vertex(x=max(current_vertices[1].x, next_ann.bounding_poly.vertices[1].x), y=min(current_vertices[1].y, next_ann.bounding_poly.vertices[1].y)),
                    vision.Vertex(x=max(current_vertices[2].x, next_ann.bounding_poly.vertices[2].x), y=max(current_vertices[2].y, next_ann.bounding_poly.vertices[2].y)),
                    vision.Vertex(x=min(current_vertices[3].x, next_ann.bounding_poly.vertices[3].x), y=max(current_vertices[3].y, next_ann.bounding_poly.vertices[3].y)),
                ]
                current_vertices = new_vertices
                processed_indices[j] = True
            else:
                break
        merged_ann = vision.entity_annotation.EntityAnnotation(description=current_desc, bounding_poly=vision.BoundingPoly(vertices=current_vertices))
        merged_number_texts.append(merged_ann)
        processed_indices[i] = True
    
    final_annotations = []
    original_non_numbers = [ann for ann in text_annotations if not (re.search(r"\d", ann.description) and not re.fullmatch(r"\d{1,2}:\d{2}", ann.description))]
    final_annotations.extend(original_non_numbers)
    final_annotations.extend(merged_number_texts)

    logger.info(f"fusionner_nombres_adjacents ({assistant_nom}): Fin. Nombres traités: {len(merged_number_texts)}. Total final: {len(final_annotations)}")
    return final_annotations

def extraire_followers_spatial(
    text_annotations: List[vision.entity_annotation.EntityAnnotation], 
    mots_cles_specifiques: List[str], 
    reseau_nom: str = "inconnu", 
    assistant_nom: str = "" # Ajouté pour les logs
) -> Optional[str]:
    try:
        logger.info(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): --- Début --- ") 
        if not text_annotations or len(text_annotations) <= 1: # Le premier est le texte complet
            logger.warning(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Pas assez d_annotations.")
            return None

        # Passer les annotations DÉTAILLÉES (sans le texte complet) à la fusion
        annotations_details = text_annotations[1:]
        annotations_post_fusion = fusionner_nombres_adjacents(annotations_details, assistant_nom=assistant_nom)
        
        # Reconstruire une liste d_annotations pour la recherche: texte complet + détails fusionnés/non-numériques
        # Cette approche peut être simplifiée si fusionner_nombres_adjacents retourne déjà tout ce qu_il faut.
        # Pour l_instant, on utilise les annotations post-fusion directement pour la recherche de mots-clés et nombres.
        # Le premier élément de text_annotations (full_text) n_est pas utilisé directement ici pour la logique spatiale.
        
        keyword_annotations_list = []
        number_annotations_list = []

        logger.info(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Annotations post-fusion ({len(annotations_post_fusion)}):")
        for i, annotation in enumerate(annotations_post_fusion):
            try:
                if not hasattr(annotation, "description") or not hasattr(annotation, "bounding_poly") or not hasattr(annotation.bounding_poly, "vertices") or len(annotation.bounding_poly.vertices) < 4:
                    continue
                
                text = annotation.description.lower().strip()
                vertices = annotation.bounding_poly.vertices
                avg_y = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4
                avg_x = (vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x) / 4
                # logger.debug(f"  Ann {i}: 	Ó{text}	 (avg_y={avg_y:.0f}, avg_x={avg_x:.0f})") # Log verbeux

                if any(keyword.lower() in text for keyword in mots_cles_specifiques):
                    keyword_annotations_list.append({"text": text, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                    logger.info(f"  MOT-CLÉ TROUVÉ: 	Ó{text}	 à y={avg_y:.0f}, x={avg_x:.0f}")
                
                nombre_normalise_test = normaliser_nombre_followers(annotation.description) # Utiliser la description originale pour normaliser
                if nombre_normalise_test:
                    if not re.fullmatch(r"\d{1,2}:\d{2}", annotation.description): 
                        number_annotations_list.append({"text": annotation.description, "normalized": nombre_normalise_test, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                        logger.info(f"  NOMBRE POTENTIEL: 	Ó{annotation.description}	 (norm: {nombre_normalise_test}) à y={avg_y:.0f}, x={avg_x:.0f}")
                    # else: logger.info(f"  Nombre 	Ó{annotation.description}	 ignoré (format heure).")
            except Exception as e_loop_ann:
                logger.error(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Erreur boucle annotation {i}: {e_loop_ann}")
                continue

        logger.info(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Mots-clés: {len(keyword_annotations_list)}, Nombres: {len(number_annotations_list)}")

        if not keyword_annotations_list:
            logger.warning(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Aucun mot-clé. Fallback sur le plus grand nombre si disponible.")
            if number_annotations_list:
                number_annotations_list.sort(key=lambda x: int(x.get("normalized", "0")), reverse=True)
                if number_annotations_list[0]["normalized"]:
                    selected_num = number_annotations_list[0]["normalized"]
                    logger.info(f"  Fallback: Sélection du plus grand nombre: {selected_num}")
                    return selected_num
            logger.warning(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Aucun mot-clé ni nombre pour fallback.")
            return None

        best_candidate = None
        min_distance = float("inf")

        for kw_ann in keyword_annotations_list:
            kw_text = kw_ann["text"]
            kw_avg_y = kw_ann["avg_y"]
            logger.info(f"  Analyse pour mot-clé: 	Ó{kw_text}	 à y={kw_avg_y:.0f}")
            for num_ann in number_annotations_list:
                y_diff = num_ann["avg_y"] - kw_ann["avg_y"]
                x_diff = abs(kw_ann["avg_x"] - num_ann["avg_x"])
                
                # Critères ajustés: y_diff > -35 (tolérance au-dessus), y_diff < 100 (pas trop loin en dessous)
                # x_diff < 200 (assez aligné horizontalement)
                if y_diff > -35 and y_diff < 100 and x_diff < 200: 
                    distance = (y_diff**2 + x_diff**2)**0.5 
                    if distance < min_distance:
                        min_distance = distance
                        best_candidate = num_ann["normalized"]
                        logger.info(f"    NOUVEAU MEILLEUR CANDIDAT (pour 	Ó{kw_text}	): {best_candidate} (distance: {min_distance:.2f})")
        
        if best_candidate:
            logger.info(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Followers final: {best_candidate}")
            return best_candidate
        else:
            logger.warning(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Aucun candidat après analyse spatiale. Fallback sur le plus grand nombre si disponible.")
            if number_annotations_list:
                number_annotations_list.sort(key=lambda x: int(x.get("normalized", "0")), reverse=True)
                if number_annotations_list[0]["normalized"]:
                    selected_num_fallback = number_annotations_list[0]["normalized"]
                    logger.info(f"  Fallback final: Sélection du plus grand nombre: {selected_num_fallback}")
                    return selected_num_fallback
            logger.warning(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Aucun nombre pour fallback final.")
            return None

    except Exception as e_global_spatial:
        logger.error(f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): ERREUR GLOBALE: {e_global_spatial}")
        logger.error(traceback.format_exc())
        return None

# --- FIN DES AMÉLIORATIONS OCR ---

# Fonction identifier_reseau_et_username (reprise de la version fonctionnelle, légèrement adaptée)
def identifier_reseau_et_username(
    text_annotations: List[vision.entity_annotation.EntityAnnotation],
    assistant_nom: str = "", # Ajouté pour les logs
) -> Tuple[str, Optional[str]]:
    if not text_annotations or not text_annotations[0].description:
        logger.warning(f"identifier_reseau_et_username ({assistant_nom}): Pas d_annotations.")
        return "inconnu", None

    full_text = text_annotations[0].description.lower()
    log_full_text_preview = full_text[:200].replace("\n", " ")
    logger.info(f"identifier_reseau_et_username ({assistant_nom}): Texte complet (aperçu): {log_full_text_preview}...")

    reseau_scores = {name: 0 for name in RESEAUX_SOCIALS_KEYWORDS}
    for name, keywords in RESEAUX_SOCIALS_KEYWORDS.items():
        for keyword in keywords:
            if keyword in full_text:
                reseau_scores[name] += 1
        if name in KNOWN_HANDLES:
            for handle in KNOWN_HANDLES[name]:
                if handle.lower() in full_text:
                    reseau_scores[name] += 2 

    identified_reseau = "inconnu"
    if any(reseau_scores.values()): # Vérifier si un score est non nul
        identified_reseau = max(reseau_scores, key=reseau_scores.get)
        if reseau_scores[identified_reseau] == 0: # Double vérification, devrait être couvert par any()
            identified_reseau = "inconnu"
    
    logger.info(f"identifier_reseau_et_username ({assistant_nom}): Réseau identifié: {identified_reseau} (Scores: {reseau_scores})")

    username = None
    if identified_reseau != "inconnu" and identified_reseau in USERNAME_PATTERNS:
        pattern = USERNAME_PATTERNS[identified_reseau]
        matches = re.findall(pattern, text_annotations[0].description) # Utiliser la casse originale pour regex username
        if matches:
            potential_usernames = []
            for match_group in matches:
                m = match_group if isinstance(match_group, str) else next((s for s in match_group if s), None)
                if m and len(m) > 2 and not m.lower() in ["profil", "modifier", "accueil"]:
                    potential_usernames.append(m)
            if potential_usernames:
                username = max(potential_usernames, key=len) # Souvent le plus long est le bon
                logger.info(f"identifier_reseau_et_username ({assistant_nom}): Username (regex): {username}")

    if not username and identified_reseau != "inconnu": # Fallback si regex échoue
        for ann in text_annotations[1:]:
            desc = ann.description
            if desc.startswith("@") and len(desc) > 1:
                username = desc
                logger.info(f"identifier_reseau_et_username ({assistant_nom}): Username (fallback @): {username}")
                break
            if identified_reseau in KNOWN_HANDLES:
                closest_match = get_close_matches(desc, KNOWN_HANDLES[identified_reseau], n=1, cutoff=0.8)
                if closest_match:
                    username = closest_match[0]
                    logger.info(f"identifier_reseau_et_username ({assistant_nom}): Username (known_handles): {username}")
                    break
    
    if username:
        username = corriger_username(username, identified_reseau)
        logger.info(f"identifier_reseau_et_username ({assistant_nom}): Username final: {username}")
    else:
        logger.warning(f"identifier_reseau_et_username ({assistant_nom}): Aucun username extrait.")

    return identified_reseau, username

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("--- Entrée dans handle_photo ---")
    assistant = "INCONNU"
    today = datetime.datetime.now().strftime("%d/%m/%Y")
    status_message = ""
    # topic_name_for_error_handling = "" # Non utilisé dans le code original, retiré
    reseau_identifie = "inconnu"
    username_extrait = "Non trouvé"
    abonnés_extraits = "Non trouvé"

    message = update.message
    if not message or not message.photo:
        logger.info("handle_photo: Message None ou sans photo.")
        return

    # Détermination de l_assistant (logique du code original)
    # Le code original ne récupérait pas le nom du topic si l_image était postée directement.
    # Il se basait sur le reply_to_message pour le nom du topic.
    # Si l_image est postée directement dans un topic, l_assistant restera "INCONNU" ou basé sur le chat_id/thread_id
    # ce qui est le comportement du code original.
    if message.reply_to_message and hasattr(message.reply_to_message, "forum_topic_created") and message.reply_to_message.forum_topic_created:
        topic_name = message.reply_to_message.forum_topic_created.name
        assistant = get_assistant_from_topic_name(topic_name)
        logger.info(f"handle_photo: Assistant 	Ó{assistant}	 identifié via réponse à création topic 	Ó{topic_name}	.")
    elif message.is_topic_message and message.message_thread_id:
        # Le code original ne tentait pas de résoudre le nom du topic ici.
        # Il envoyait au topic "General" (message_thread_id=None implicitement ou explicitement)
        # L_assistant restait "INCONNU" ou un identifiant numérique.
        # Pour correspondre au code original, on ne cherche pas le nom du topic ici.
        assistant = f"TopicID:{message.message_thread_id}" # Ou laisser INCONNU
        logger.info(f"handle_photo: Image dans topic {assistant}. Assistant non déterminé par nom.")
        status_message += f"Image reçue dans le sujet {assistant}. L_identification de l_assistant par nom nécessite de répondre au message de création du sujet.\n"
    else:
        logger.info("handle_photo: Image hors topic ou non en réponse à création. Assistant INCONNU.")
        status_message += "L_image n_a pas été envoyée de manière à identifier l_assistant par nom.\n"

    logger.info(f"handle_photo: Traitement pour assistant: {assistant}")

    photo_file = None
    try:
        photo: PhotoSize = message.photo[-1]
        photo_file = await photo.get_file()
    except Exception as e:
        logger.error(f"handle_photo ({assistant}): Erreur téléchargement photo: {e}")
        status_message += "Erreur téléchargement photo Telegram."
        # Le code original envoyait au chat_id du message original, pas GROUP_ID/General
        # Pour rester fidèle, on envoie au chat d_origine et au thread_id d_origine si c_est un message de topic
        # Si on veut toujours envoyer à General, il faut changer context.bot.send_message ici.
        # Le code original faisait : await message.reply_text(message_status_general, message_thread_id=message.message_thread_id if message.is_topic_message else None)
        # Ce qui est complexe à reproduire sans message_status_general. On va simplifier pour l_instant et envoyer à General.
        await context.bot.send_message(
            chat_id=GROUP_ID, 
            text=status_message, 
            message_thread_id=None, # Forcer vers General pour les erreurs initiales
            parse_mode=ParseMode.HTML
        )
        return

    image_bytes = io.BytesIO()
    await photo_file.download_to_memory(image_bytes)
    image_bytes.seek(0)

    try:
        pil_image = Image.open(image_bytes)
        pil_image = ImageOps.grayscale(pil_image)
        img_byte_arr = io.BytesIO()
        pil_image.save(img_byte_arr, format="PNG")
        content = img_byte_arr.getvalue()
        vision_image = vision.Image(content=content)

        if not vision_client:
            raise Exception("Client Vision AI non initialisé.")
        
        response = vision_client.text_detection(image=vision_image)
        texts = response.text_annotations

        if response.error.message:
            raise Exception(f"Erreur API Vision: {response.error.message}")

        if texts:
            ocr_text_preview = texts[0].description[:200].replace("\n", " ")
            logger.info(f"handle_photo ({assistant}): OCR (début): {ocr_text_preview}")
            
            reseau_identifie, username_extrait = identifier_reseau_et_username(texts, assistant_nom=assistant)
            logger.info(f"handle_photo ({assistant}): Réseau: {reseau_identifie}, Username: {username_extrait}")

            mots_cles_fol = FOLLOWERS_KEYWORDS_SPECIFIC.get(reseau_identifie, [])
            if not mots_cles_fol and reseau_identifie != "inconnu":
                mots_cles_fol = ["followers", "abonnés", "suivi(e)s"] # Fallback générique
            
            abonnés_extraits = extraire_followers_spatial(texts, mots_cles_fol, reseau_identifie, assistant_nom=assistant)
            logger.info(f"handle_photo ({assistant}): Followers: {abonnés_extraits}")

            if reseau_identifie != "inconnu" and username_extrait and abonnés_extraits:
                status_message += (
                    f"<b>Assistant {assistant}</b>:\n"
                    f"Réseau: {reseau_identifie}\n"
                    f"Username: {username_extrait}\n"
                    f"Abonnés: {abonnés_extraits}"
                )
                if sheet:
                    try:
                        sheet.append_row([today, assistant, reseau_identifie, username_extrait, abonnés_extraits])
                        logger.info(f"Données ajoutées à GSheets pour {assistant}")
                    except Exception as e_gsheet:
                        logger.error(f"Erreur ajout GSheets: {e_gsheet}")
                        status_message += "\n(Erreur sauvegarde GSheets)"
                else:
                    status_message += "\n(Sauvegarde GSheets impossible: non initialisé)"
            else:
                status_message += (
                    f"<b>Assistant {assistant}</b>: Infos incomplètes.\n"
                    f"Réseau: {reseau_identifie if reseau_identifie != \"inconnu\" else \"Non identifié\"}\n"
                    f"Username: {username_extrait if username_extrait else \"Non trouvé\"}\n"
                    f"Abonnés: {abonnés_extraits if abonnés_extraits else \"Non trouvé\"}"
                )
        else:
            logger.warning(f"handle_photo ({assistant}): Aucun texte détecté.")
            status_message += f"<b>Assistant {assistant}</b>: Aucun texte détecté sur l_image."

    except UnidentifiedImageError:
        logger.error(f"handle_photo ({assistant}): Format image non reconnu.")
        status_message += f"<b>Assistant {assistant}</b>: Format image non reconnu/corrompue."
    except Exception as e:
        logger.error(f"handle_photo ({assistant}): Erreur traitement OCR/analyse: {e}")
        logger.error(traceback.format_exc())
        status_message += f"<b>Assistant {assistant}</b>: Erreur analyse image: {str(e)[:100]}"

    # Envoi du message de statut dans le topic "General" (comme dans le code original)
    # Le code original utilisait `bot.send_message` avec `chat_id=GROUP_ID` et `message_thread_id=None` (implicite pour General)
    try:
        # S_assurer que GROUP_ID est bien un entier si nécessaire pour la lib
        final_group_id = int(GROUP_ID) if GROUP_ID and GROUP_ID.lstrip("-").isdigit() else GROUP_ID

        await context.bot.send_message(
            chat_id=final_group_id,
            text=status_message if status_message else f"<b>Assistant {assistant}</b>: Traitement terminé.",
            message_thread_id=None, # None pour envoyer au topic "General"
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"Message statut envoyé à General pour {assistant}.")
    except Exception as e_send:
        logger.error(f"Erreur envoi message statut à General: {e_send}")
        logger.error(traceback.format_exc())

# Le code original n_avait pas de webhook, donc on garde la structure de polling simple.
# Si le webhook est nécessaire, il faudra ajouter FastAPI/Uvicorn comme dans la version fusionnée.
def main():
    # Utiliser ptb_application défini globalement
    ptb_application.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.SUPERGROUP, handle_photo))
    
    logger.info("Démarrage du bot en mode polling...")
    ptb_application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    # Vérifier les variables d_environnement essentielles au démarrage
    if not TOKEN or not GROUP_ID or not SPREADSHEET_ID or not google_creds_gspread_json_str or not google_creds_vision_json_str:
        logger.critical("Variables d_environnement critiques manquantes. Le bot ne peut pas démarrer.")
    else:
        main()

