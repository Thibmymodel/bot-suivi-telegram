"""
Ce bot Telegram a pour objectif d_extraire des informations de captures d_écran de profils de réseaux sociaux,
notamment le nom d_utilisateur et le nombre de followers, en utilisant Google Vision AI pour l_OCR.
Il enregistre ensuite ces données dans une feuille Google Sheets.

Fonctionnalités principales :
- Réception d_images (captures d_écran) via Telegram.
- Identification de l_assistant (basé sur le nom du topic Telegram si l_image est une réponse à la création du topic).
- Détection du réseau social (Instagram, Twitter, TikTok, etc.) à partir du contenu de l_image.
- Extraction du nom d_utilisateur et du nombre de followers en utilisant Google Vision AI.
- Normalisation des nombres de followers (gestion des "K", "M", espaces).
- Enregistrement des données extraites (Date, Assistant, Réseau, Username, Abonnés) dans Google Sheets.
- Envoi de messages de statut (succès ou échec) sur Telegram dans le topic "General".
- Fonctionnement en mode webhook avec FastAPI/Uvicorn.
"""
import asyncio
import json
import io
import re
import datetime
import logging
import os
import traceback
from typing import Optional, List, Dict, Any, Tuple
from difflib import get_close_matches

from telegram import Update, Bot, PhotoSize
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes,
    TypeHandler,
    CallbackContext,
)
from telegram.constants import ParseMode
from PIL import Image, ImageOps, UnidentifiedImageError
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.cloud import vision
import fastapi
import uvicorn

# Configuration du logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("main") # Nommer le logger "main" pour correspondre aux logs précédents

# Variables d_environnement et constantes
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
RAILWAY_PUBLIC_URL = os.getenv("RAILWAY_PUBLIC_URL")
MODE_POLLING = os.getenv("MODE_POLLING", "false").lower() == "true"

if not TOKEN:
    logger.error("La variable d_environnement TELEGRAM_BOT_TOKEN n_est pas définie.")
    exit()
if not GROUP_ID:
    logger.error("La variable d_environnement TELEGRAM_GROUP_ID n_est pas définie.")
    exit()
if not SPREADSHEET_ID:
    logger.error("La variable d_environnement SPREADSHEET_ID n_est pas définie.")
    exit()

# Initialisation Google Sheets
gspread_creds: Optional[ServiceAccountCredentials] = None
gc: Optional[gspread.Client] = None
sheet: Optional[gspread.Worksheet] = None
try:
    google_creds_gspread_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_GSPREAD")
    if not google_creds_gspread_json_str:
        logger.error(
            "La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS_GSPREAD n_est pas définie."
        )
        exit()
    creds_gspread_dict = json.loads(google_creds_gspread_json_str)
    gspread_creds = ServiceAccountCredentials.from_service_account_info(
        creds_gspread_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(gspread_creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
    logger.info("Connexion à Google Sheets réussie.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Sheets: {e}")
    logger.error(traceback.format_exc())
    exit()

# Initialisation Google Vision AI
vision_client: Optional[vision.ImageAnnotatorClient] = None
try:
    google_creds_vision_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not google_creds_vision_json_str:
        logger.error(
            "La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS (pour Vision) n_est pas définie."
        )
        exit()
    creds_vision_dict = json.loads(google_creds_vision_json_str)
    vision_creds = ServiceAccountCredentials.from_service_account_info(creds_vision_dict)
    vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)
    logger.info("Client Google Vision AI initialisé avec succès.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Vision AI: {e}")
    logger.error(traceback.format_exc())
    exit()

# Chargement des handles connus
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

# Configuration des réseaux sociaux et mots-clés
RESEAUX_SOCIALS_KEYWORDS = {
    "instagram": [
        "instagram",
        "followers",
        "abonnés",
        "suivi(e)s",
        "publications",
        "j_aime",
        "profil",
        "reels",
    ],
    "twitter": [
        "twitter",
        "x.com",
        "abonnements",
        "abonnés",
        "followers",
        "following",
        "tweets",
        "profil",
    ],
    "tiktok": ["tiktok", "abonnements", "followers", "j_aime", "profil", "amis", "pour toi"],
    "threads": ["threads", "followers", "abonnés", "profil"],
    "facebook": ["facebook", "amis", "followers", "j_aime", "publications", "profil"],
    "linkedin": ["linkedin", "relations", "abonné(e)s", "profil", "posts"],
    "youtube": ["youtube", "abonnés", "subscribers", "vidéos", "chaîne", "channel"],
    "twitch": ["twitch", "followers", "suivis", "chaîne", "stream"],
    "snapchat": ["snapchat", "amis", "score", "profil"],
    "pinterest": ["pinterest", "abonnés", "épingles", "tableaux", "profil"],
}

FOLLOWERS_KEYWORDS_SPECIFIC = {
    "instagram": ["followers", "abonnés", "suivi(e)s"],
    "twitter": ["abonnés", "followers"],
    "tiktok": ["followers", "abonnés"],
    "threads": ["followers", "abonnés"],
    "facebook": ["followers", "amis"],  # Amis peut être un indicateur sur FB
    "linkedin": ["abonné(e)s", "relations"],
    "youtube": ["abonnés", "subscribers"],
    "twitch": ["followers"],
    "snapchat": [],  # Snapchat ne montre pas publiquement les followers de la même manière
    "pinterest": ["abonnés"],
}

USERNAME_PATTERNS = {
    "instagram": r"@?([a-zA-Z0-9_.]{1,30})",
    "twitter": r"@?([a-zA-Z0-9_]{1,15})",
    "tiktok": r"@?([a-zA-Z0-9_.-]+)",
    "threads": r"@?([a-zA-Z0-9_.]{1,30})",  # Similaire à Instagram
}

ASSISTANT_TOPIC_KEYWORDS = ["SUIVI", "Suivi", "suivi"]

ptb_application: Application = Application.builder().token(TOKEN).build()
fastapi_app = fastapi.FastAPI()

def get_assistant_from_topic_name(topic_name: Optional[str]) -> str:
    if not topic_name:
        return "INCONNU"
    for keyword in ASSISTANT_TOPIC_KEYWORDS:
        if topic_name.startswith(keyword):
            assistant_name = topic_name[len(keyword) :].strip()
            if assistant_name:
                return assistant_name
    return topic_name  # Si aucun mot-clé, le nom du topic est peut-être le nom de l_assistant

def corriger_username(username: str, reseau: str) -> str:
    if reseau == "instagram" and username.startswith("@"):
        return username[1:]
    return username.strip()

def normaliser_nombre_followers(nombre_str: Optional[str]) -> Optional[str]:
    if not nombre_str or not isinstance(nombre_str, str):
        return None

    nombre_str_test = nombre_str.strip()
    if not re.match(r"^[\d.,\s]*[kKm]?$", nombre_str_test, re.IGNORECASE):
        logger.debug(
            f"normaliser_nombre_followers: 	Ó{nombre_str_test}	 ne correspond pas au format attendu."
        )
        return None

    nombre_str_clean = nombre_str_test.lower()
    valeur = None

    try:
        if "k" in nombre_str_clean or "m" in nombre_str_clean:
            nombre_str_clean = nombre_str_clean.replace(" ", "")
            nombre_part = nombre_str_clean[:-1].replace(",", ".")
            multiplicateur = 1000 if "k" in nombre_str_clean else 1000000

            if not re.match(r"^\d+(\.\d+)?[km]$", nombre_str_clean.replace(",", ".")):
                logger.debug(
                    f"normaliser_nombre_followers: Format 	Ók	 ou 	Óm	 invalide pour 	Ó{nombre_str_clean}	"
                )
                return None
            valeur = str(int(float(nombre_part) * multiplicateur))
        else:
            nombre_str_clean = re.sub(r"\D", "", nombre_str_test)
            if not nombre_str_clean.isdigit():
                logger.debug(
                    f"normaliser_nombre_followers: 	Ó{nombre_str_clean}	 (après sub non-digit) n_est pas un digit."
                )
                return None
            valeur = str(int(nombre_str_clean))
    except ValueError:
        logger.warning(
            f"normaliser_nombre_followers: ValueError lors de la conversion de 	Ó{nombre_str_clean}	 (original: 	Ó{nombre_str_test}	)"
        )
        return None
    return valeur

def fusionner_nombres_adjacents(
    text_annotations: List[vision.entity_annotation.EntityAnnotation],
    max_pixel_gap: int = 30, 
    assistant_nom: str = "",
) -> List[vision.entity_annotation.EntityAnnotation]:
    if not text_annotations or len(text_annotations) == 0: 
        return text_annotations

    logger.info(
        f"fusionner_nombres_adjacents ({assistant_nom}): Début de la fusion. Nombre d_annotations: {len(text_annotations)}"
    )

    potential_number_annotations = []
    for ann in text_annotations: 
        if re.search(r"\d", ann.description) and not re.fullmatch(
            r"\d{1,2}:\d{2}", ann.description
        ):
            potential_number_annotations.append(ann)

    if not potential_number_annotations:
        logger.info(
            f"fusionner_nombres_adjacents ({assistant_nom}): Aucune annotation numérique potentielle trouvée pour la fusion."
        )
        return text_annotations 

    potential_number_annotations.sort(
        key=lambda ann: (
            ann.bounding_poly.vertices[0].y + ann.bounding_poly.vertices[3].y
        )
        / 2
    )
    potential_number_annotations.sort(
        key=lambda ann: (
            ann.bounding_poly.vertices[0].x + ann.bounding_poly.vertices[1].x
        )
        / 2
    )

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
            next_mid_y = (
                next_ann.bounding_poly.vertices[0].y
                + next_ann.bounding_poly.vertices[3].y
            ) / 2
            current_right_x = max(v.x for v in current_vertices)
            next_left_x = min(v.x for v in next_ann.bounding_poly.vertices)
            approx_char_height = abs(current_vertices[3].y - current_vertices[0].y)
            y_diff = abs(current_mid_y - next_mid_y)
            gap_x = next_left_x - current_right_x

            if (
                y_diff < approx_char_height * 0.75
                and 0 <= gap_x < max_pixel_gap
                and re.search(r"\d", next_ann.description)
            ):
                log_current_desc = current_desc
                log_next_ann_desc = next_ann.description
                logger.info(
                    f"fusionner_nombres_adjacents ({assistant_nom}): Fusion de 	Ó{log_current_desc}	 avec 	Ó{log_next_ann_desc}	 (gap_x: {gap_x:.0f}) "
                )
                current_desc += " " + next_ann.description
                new_vertices = [
                    vision.Vertex(
                        x=min(
                            current_vertices[0].x,
                            next_ann.bounding_poly.vertices[0].x,
                        ),
                        y=min(
                            current_vertices[0].y,
                            next_ann.bounding_poly.vertices[0].y,
                        ),
                    ),
                    vision.Vertex(
                        x=max(
                            current_vertices[1].x,
                            next_ann.bounding_poly.vertices[1].x,
                        ),
                        y=min(
                            current_vertices[1].y,
                            next_ann.bounding_poly.vertices[1].y,
                        ),
                    ),
                    vision.Vertex(
                        x=max(
                            current_vertices[2].x,
                            next_ann.bounding_poly.vertices[2].x,
                        ),
                        y=max(
                            current_vertices[2].y,
                            next_ann.bounding_poly.vertices[2].y,
                        ),
                    ),
                    vision.Vertex(
                        x=min(
                            current_vertices[3].x,
                            next_ann.bounding_poly.vertices[3].x,
                        ),
                        y=max(
                            current_vertices[3].y,
                            next_ann.bounding_poly.vertices[3].y,
                        ),
                    ),
                ]
                current_vertices = new_vertices
                processed_indices[j] = True
            else:
                break

        merged_ann = vision.EntityAnnotation(
            description=current_desc,
            bounding_poly=vision.BoundingPoly(vertices=current_vertices),
        )
        merged_number_texts.append(merged_ann)
        processed_indices[i] = True
    
    final_annotations = []
    original_non_numbers = [ann for ann in text_annotations if not re.search(r"\d", ann.description) or re.fullmatch(r"\d{1,2}:\d{2}", ann.description)]
    final_annotations.extend(original_non_numbers)
    final_annotations.extend(merged_number_texts)

    logger.info(
        f"fusionner_nombres_adjacents ({assistant_nom}): Fin de la fusion. Nombre d_annotations numériques traitées: {len(merged_number_texts)}. Total final: {len(final_annotations)}"
    )
    return final_annotations

def extraire_followers_spatial(
    text_annotations: List[vision.entity_annotation.EntityAnnotation],
    mots_cles_followers: List[str],
    reseau_nom: str = "inconnu",
    assistant_nom: str = "",
) -> Optional[str]:
    try:
        logger.info(
            f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): --- Début de l_extraction spatiale ---"
        )

        if not text_annotations or len(text_annotations) <= 1:
            logger.warning(
                f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Aucune annotation de texte (ou seulement le texte complet) fournie."
            )
            return None

        annotations_details = text_annotations[1:]
        annotations_post_fusion = fusionner_nombres_adjacents(
            annotations_details, assistant_nom=assistant_nom
        )

        processed_text_annotations = [text_annotations[0]] + annotations_post_fusion

        logger.info(
            f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Nombre total d_annotations pour analyse: {len(processed_text_annotations)}"
        )
        if len(processed_text_annotations) > 1:
            logger.info(
                f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Premières annotations après fusion (description et position Y moyenne):"
            )
            for i, annotation in enumerate(processed_text_annotations[1:11]):
                try:
                    if (
                        hasattr(annotation, "description")
                        and hasattr(annotation, "bounding_poly")
                        and hasattr(annotation.bounding_poly, "vertices")
                        and len(annotation.bounding_poly.vertices) >= 4
                    ):
                        vertices = annotation.bounding_poly.vertices
                        avg_y_log = (
                            vertices[0].y
                            + vertices[1].y
                            + vertices[2].y
                            + vertices[3].y
                        ) / 4
                        ann_desc = annotation.description
                        logger.info(
                            f"  - Ann {i+1} (post-fusion): 	Ó{ann_desc}	 (avg_y: {avg_y_log:.0f})"
                        )
                    else:
                        logger.warning(
                            f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Annotation post-fusion {i+1} malformée: {annotation}"
                        )
                except Exception as e_log_ann:
                    logger.error(
                        f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Erreur lors du logging de l_annotation post-fusion {i+1}: {e_log_ann}. Annotation: {annotation}"
                    )

        keyword_annotations_list = []
        number_annotations_list = []

        for i, annotation in enumerate(processed_text_annotations[1:]):
            try:
                if not hasattr(annotation, "description") or not hasattr(
                    annotation, "bounding_poly"
                ):
                    continue

                text = annotation.description.lower().strip()
                if not hasattr(annotation.bounding_poly, "vertices") or len(
                    annotation.bounding_poly.vertices
                ) < 4:
                    continue

                vertices = annotation.bounding_poly.vertices
                avg_y = (
                    vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y
                ) / 4
                avg_x = (
                    vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x
                ) / 4

                if any(keyword.lower() in text for keyword in mots_cles_followers):
                    keyword_annotations_list.append(
                        {"text": text, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation}
                    )
                    logger.info(
                        f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): MOT-CLÉ TROUVÉ: 	Ó{text}	 à y={avg_y:.0f}, x={avg_x:.0f}"
                    )

                nombre_normalise_test = normaliser_nombre_followers(
                    annotation.description
                )
                if nombre_normalise_test:
                    if not re.fullmatch(r"\d{1,2}:\d{2}", annotation.description):
                        number_annotations_list.append(
                            {
                                "text": annotation.description,
                                "normalized": nombre_normalise_test,
                                "avg_y": avg_y,
                                "avg_x": avg_x,
                                "annotation": annotation,
                            }
                        )
                        ann_desc_norm = annotation.description
                        logger.info(
                            f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): NOMBRE POTENTIEL TROUVÉ: 	Ó{ann_desc_norm}	 (normalisé: {nombre_normalise_test}) à y={avg_y:.0f}, x={avg_x:.0f}"
                        )
                    else:
                        ann_desc_heure = annotation.description
                        logger.info(
                            f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Nombre 	Ó{ann_desc_heure}	 ignoré (format heure)."
                        )

            except Exception as e_loop_ann:
                logger.error(
                    f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): ERREUR INATTENDUE lors du traitement de l_annotation {i}: {e_loop_ann}"
                )
                logger.error(traceback.format_exc())
                continue

        logger.info(
            f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Fin de l_analyse des annotations. Mots-clés: {len(keyword_annotations_list)}, Nombres: {len(number_annotations_list)}"
        )
        for idx, na in enumerate(number_annotations_list):
            na_text = na["text"]
            na_normalized = na["normalized"]
            na_avg_y = na["avg_y"]
            logger.info(f"  - Nombre {idx}: {na_text} (normalisé: {na_normalized}) à y={na_avg_y:.0f}")

        if not keyword_annotations_list:
            logger.warning(
                f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Aucun mot-clé de followers trouvé. Tentative de fallback."
            )
            if number_annotations_list:
                number_annotations_list.sort(
                    key=lambda x: int(x.get("normalized", "0")),
                    reverse=True,
                )
                sel_num_norm = number_annotations_list[0]["normalized"]
                logger.info(
                    f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}) (Fallback sans mot-clé): Sélection du plus grand nombre: {sel_num_norm}"
                )
                return number_annotations_list[0]["normalized"]
            logger.warning(
                f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Aucun mot-clé et aucun nombre trouvé. Abandon."
            )
            return None

        best_candidate = None
        min_distance = float("inf")

        logger.info(
            f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Recherche du meilleur candidat basé sur la proximité du mot-clé."
        )
        for kw_ann in keyword_annotations_list:
            kw_text = kw_ann["text"]
            kw_avg_y = kw_ann["avg_y"]
            logger.info(f"  - Analyse pour mot-clé: 	Ó{kw_text}	 à y={kw_avg_y:.0f}")
            for num_ann in number_annotations_list:
                y_diff = num_ann["avg_y"] - kw_ann["avg_y"]
                x_diff = abs(kw_ann["avg_x"] - num_ann["avg_x"])

                if y_diff > -35 and y_diff < 100 and x_diff < 200:
                    distance = (y_diff**2 + x_diff**2) ** 0.5
                    if distance < min_distance:
                        min_distance = distance
                        best_candidate = num_ann["normalized"]
                        kw_text_cand = kw_ann["text"]
                        logger.info(
                            f"      NOUVEAU MEILLEUR CANDIDAT (pour 	Ó{kw_text_cand}	): {best_candidate} (distance: {min_distance:.2f})"
                        )

        if best_candidate:
            logger.info(
                f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Nombre de followers final extrait: {best_candidate}"
            )
            return best_candidate
        else:
            logger.warning(
                f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): Aucun candidat de followers n_a pu être sélectionné après analyse spatiale."
            )
            if number_annotations_list:
                number_annotations_list.sort(
                    key=lambda x: int(x.get("normalized", "0")),
                    reverse=True,
                )
                if number_annotations_list and number_annotations_list[0]["normalized"]:
                    sel_num_norm_fin = number_annotations_list[0]["normalized"]
                    logger.warning(
                        f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}) (Fallback final): Sélection du plus grand nombre: {sel_num_norm_fin}"
                    )
                    return number_annotations_list[0]["normalized"]
            logger.warning(
                f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}) (Fallback final): Aucun nombre à retourner."
            )
            return None

    except Exception as e_global_spatial:
        logger.error(
            f"extraire_followers_spatial ({reseau_nom} - {assistant_nom}): ERREUR GLOBALE INATTENDUE DANS LA FONCTION: {e_global_spatial}"
        )
        logger.error(traceback.format_exc())
        return None

def identifier_reseau_et_username(
    text_annotations: List[vision.entity_annotation.EntityAnnotation],
    assistant_nom: str = "",
) -> Tuple[str, Optional[str]]:
    if not text_annotations or not text_annotations[0].description:
        logger.warning(
            f"identifier_reseau_et_username ({assistant_nom}): Pas d_annotations ou de description complète."
        )
        return "inconnu", None

    full_text = text_annotations[0].description.lower()
    # Utilisation d_une variable temporaire pour le texte formaté pour le log
    log_full_text_preview = full_text[:500].replace("\n", " ")
    logger.info(
        f"identifier_reseau_et_username ({assistant_nom}): Texte complet pour identification: \n{log_full_text_preview}..."
    )

    reseau_scores = {name: 0 for name in RESEAUX_SOCIALS_KEYWORDS}
    for name, keywords in RESEAUX_SOCIALS_KEYWORDS.items():
        for keyword in keywords:
            if keyword in full_text:
                reseau_scores[name] += 1
        if name in KNOWN_HANDLES:
            for handle in KNOWN_HANDLES[name]:
                if handle.lower() in full_text:
                    reseau_scores[name] += 2

    identified_reseau = max(reseau_scores, key=reseau_scores.get)
    if reseau_scores[identified_reseau] == 0:
        identified_reseau = "inconnu"
    logger.info(
        f"identifier_reseau_et_username ({assistant_nom}): Réseau identifié: {identified_reseau} (Scores: {reseau_scores})"
    )

    username = None
    if identified_reseau != "inconnu" and identified_reseau in USERNAME_PATTERNS:
        pattern = USERNAME_PATTERNS[identified_reseau]
        # Utiliser text_annotations[0].description (original case) pour la regex, pas full_text (lower case)
        matches = re.findall(pattern, text_annotations[0].description)
        if matches:
            potential_usernames = []
            for match_group in matches:
                m = (
                    match_group
                    if isinstance(match_group, str)
                    else next((s for s in match_group if s), None)
                )
                if m and len(m) > 2 and not m.lower() in ["profil", "modifier", "accueil"]:
                    potential_usernames.append(m)
            if potential_usernames:
                # Prendre le username le plus long, souvent plus pertinent
                username = max(potential_usernames, key=len) 
                logger.info(
                    f"identifier_reseau_et_username ({assistant_nom}): Username potentiel trouvé par regex: {username}"
                )

    if not username and identified_reseau != "inconnu":
        for ann in text_annotations[1:]:
            desc = ann.description
            if desc.startswith("@") and len(desc) > 1:
                username = desc
                logger.info(
                    f"identifier_reseau_et_username ({assistant_nom}): Username trouvé par fallback (@): {username}"
                )
                break
            if identified_reseau in KNOWN_HANDLES:
                closest_match = get_close_matches(
                    desc, KNOWN_HANDLES[identified_reseau], n=1, cutoff=0.8
                )
                if closest_match:
                    username = closest_match[0]
                    logger.info(
                        f"identifier_reseau_et_username ({assistant_nom}): Username trouvé par KNOWN_HANDLES: {username}"
                    )
                    break

    if username:
        username = corriger_username(username, identified_reseau)
        logger.info(f"identifier_reseau_et_username ({assistant_nom}): Username final: {username}")
    else:
        logger.warning(f"identifier_reseau_et_username ({assistant_nom}): Aucun username n_a pu être extrait.")

    return identified_reseau, username

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("--- Entrée dans handle_photo ---")
    assistant = "INCONNU"
    today = datetime.datetime.now().strftime("%d/%m/%Y")
    status_message = ""
    donnees_extraites_ok = False
    reseau_identifie = "inconnu"
    username_extrait = "Non trouvé"
    abonnés_extraits = "Non trouvé"

    message = update.message
    if not message or not message.photo:
        logger.info("handle_photo: Message None ou sans photo. Aucune action.")
        return

    if (
        message.reply_to_message
        and hasattr(message.reply_to_message, "forum_topic_created")
        and message.reply_to_message.forum_topic_created
    ):
        topic_name = message.reply_to_message.forum_topic_created.name
        assistant = get_assistant_from_topic_name(topic_name)
        logger.info(
            f"handle_photo: Assistant 	Ó{assistant}	 identifié via réponse à la création du topic 	Ó{topic_name}	."
        )
    elif message.is_topic_message and message.message_thread_id:
        # Pour récupérer le nom du topic, il faudrait un appel à get_forum_topic_by_id ou une logique plus complexe.
        # Pour l_instant, on ne peut pas récupérer le nom du topic directement depuis l_objet message.
        # On va donc utiliser le TopicID pour l_instant.
        # Si le nom du topic est crucial, il faudra investiguer comment le récupérer.
        # Le code initial de l_utilisateur ne semblait pas non plus le récupérer directement dans ce cas.
        assistant = f"INCONNU (TopicID: {message.message_thread_id})" 
        logger.warning(
            f"handle_photo: Image postée directement dans le topic ID {message.message_thread_id}. "
            f"L_assistant n_a pas pu être déterminé à partir du nom du topic. "
            f"Pour une identification basée sur le nom, répondez au message de création du topic."
        )
        status_message += (
            f"Impossible de déterminer l_assistant à partir du nom du topic pour l_image postée directement (TopicID: {message.message_thread_id}). "
            f"Pour une identification correcte par nom, veuillez répondre au message de création du sujet avec l_image.\n"
        )
    else:
        logger.info(
            f"handle_photo: Image non envoyée en réponse à la création d_un topic, ou pas dans un topic. Assistant reste 	Ó{assistant}	."
        )
        status_message += "L_image n_a pas été envoyée de manière à identifier l_assistant (répondre au message de création du topic). \n"

    logger.info(f"handle_photo: Traitement pour l_assistant: {assistant}")

    photo_file = None
    try:
        photo: PhotoSize = message.photo[-1]  
        photo_file = await photo.get_file()
    except Exception as e:
        logger.error(f"handle_photo ({assistant}): Erreur lors du téléchargement de la photo: {e}")
        status_message += "Erreur lors du téléchargement de la photo depuis Telegram."
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=status_message,
            message_thread_id=None,  
            parse_mode=ParseMode.HTML,
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
            raise Exception(f"Erreur de l_API Vision: {response.error.message}")

        if texts:
            # Correction de la f-string pour l_affichage du log (SyntaxError)
            ocr_text_preview = texts[0].description[:200].replace("\n", " ")
            logger.info(f"handle_photo ({assistant}): Texte extrait par OCR (début): {ocr_text_preview}")
            
            reseau_identifie, username_extrait = identifier_reseau_et_username(
                texts, assistant_nom=assistant
            )
            logger.info(
                f"handle_photo ({assistant}): Réseau identifié: {reseau_identifie}, Username: {username_extrait}"
            )

            mots_cles_fol = FOLLOWERS_KEYWORDS_SPECIFIC.get(reseau_identifie, [])
            if not mots_cles_fol and reseau_identifie != "inconnu": 
                mots_cles_fol = ["followers", "abonnés", "suivi(e)s"]
            
            abonnés_extraits = extraire_followers_spatial(
                texts, mots_cles_fol, reseau_identifie, assistant_nom=assistant
            )
            logger.info(f"handle_photo ({assistant}): Followers extraits: {abonnés_extraits}")

            if reseau_identifie != "inconnu" and username_extrait and abonnés_extraits:
                donnees_extraites_ok = True
                status_message += (
                    f"<b>Assistant {assistant}</b>:\n"
                    f"Réseau: {reseau_identifie}\n"
                    f"Username: {username_extrait}\n"
                    f"Abonnés: {abonnés_extraits}"
                )
                if sheet:
                    try:
                        sheet.append_row([
                            today,
                            assistant,
                            reseau_identifie,
                            username_extrait,
                            abonnés_extraits,
                        ])
                        logger.info(f"Données ajoutées à Google Sheets pour {assistant}")
                    except Exception as e_gsheet:
                        logger.error(f"Erreur lors de l_ajout à Google Sheets: {e_gsheet}")
                        status_message += "\n(Erreur sauvegarde GSheets)"
                else:
                    logger.error("Google Sheet non initialisé, sauvegarde impossible.")
                    status_message += "\n(Sauvegarde GSheets impossible: non initialisé)"
            else:
                status_message += (
                    f"<b>Assistant {assistant}</b>:\n"
                    f"Traitement OCR terminé, mais informations incomplètes.\n"
                    f"Réseau: {reseau_identifie if reseau_identifie != 'inconnu' else 'Non identifié'}\n"
                    f"Username: {username_extrait if username_extrait else 'Non trouvé'}\n"
                    f"Abonnés: {abonnés_extraits if abonnés_extraits else 'Non trouvé'}"
                )
        else:
            logger.warning(f"handle_photo ({assistant}): Aucun texte détecté par l_OCR.")
            status_message += f"<b>Assistant {assistant}</b>: Aucun texte n_a pu être détecté sur l_image."

    except UnidentifiedImageError:
        logger.error(f"handle_photo ({assistant}): Format d_image non reconnu ou image corrompue.")
        status_message += f"<b>Assistant {assistant}</b>: Le format de l_image n_est pas reconnu ou l_image est corrompue."
    except Exception as e:
        logger.error(f"handle_photo ({assistant}): Erreur lors du traitement OCR ou de l_analyse: {e}")
        logger.error(traceback.format_exc())
        status_message += f"<b>Assistant {assistant}</b>: Une erreur est survenue lors de l_analyse de l_image: {str(e)[:100]}"

    try:
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=status_message if status_message else f"<b>Assistant {assistant}</b>: Traitement terminé (pas de message spécifique).",
            message_thread_id=None,  
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"Message de statut envoyé à General pour l_assistant {assistant}.")
    except Exception as e_send:
        logger.error(f"Erreur lors de l_envoi du message de statut à General: {e_send}")

async def webhook_handler_post(request: fastapi.Request, ptb_context: ContextTypes.DEFAULT_TYPE):
    logger.info("--- Entrée dans webhook_handler_post ---")
    try:
        update_data = await request.json()
        # Ne pas logger toutes les données du webhook en production si elles sont volumineuses ou sensibles
        # logger.debug(f"Webhook received data: {json.dumps(update_data, indent=2)}") 
        update = Update.de_json(data=update_data, bot=ptb_context.bot)
        await ptb_application.process_update(update)
        return fastapi.Response(content="OK", status_code=200)
    except json.JSONDecodeError:
        logger.error("webhook_handler_post: Erreur de décodage JSON.")
        return fastapi.Response(content="Error: Invalid JSON", status_code=400)
    except Exception as e:
        logger.error(f"webhook_handler_post: Erreur lors du traitement de l_update: {e}")
        logger.error(traceback.format_exc())
        return fastapi.Response(content="Error: Internal Server Error", status_code=500)

async def startup_event():
    logger.info("Application startup...")
    if not vision_client or not gc or not sheet or not TOKEN:
        logger.critical("Dépendances critiques non initialisées. Arrêt.")
        return

    await ptb_application.initialize() 

    if not MODE_POLLING:
        if not RAILWAY_PUBLIC_URL:
            logger.error(
                "MODE_POLLING est false mais RAILWAY_PUBLIC_URL n_est pas définie. Webhook non configuré."
            )
            return
        webhook_url = f"{RAILWAY_PUBLIC_URL.rstrip("/")}/"
        try:
            await ptb_application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            logger.info(f"Webhook configuré sur: {webhook_url}")
        except Exception as e:
            logger.error(f"Erreur lors de la configuration du webhook: {e}")
            logger.error(traceback.format_exc())
    else:
        logger.info("Mode Polling activé. Le Webhook n_est pas configuré.")

async def shutdown_event():
    logger.info("Application shutdown...")
    if not MODE_POLLING:
        try:
            await ptb_application.bot.delete_webhook(drop_pending_updates=True)
            logger.info("Webhook supprimé.")
        except Exception as e:
            logger.error(f"Erreur lors de la suppression du webhook: {e}")
    await ptb_application.shutdown()

def main_bot():
    ptb_application.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.SUPERGROUP, handle_photo
        ) 
    )

    if MODE_POLLING:
        logger.info("Démarrage du bot en mode polling...")
        ptb_application.run_polling(drop_pending_updates=True)
    else:
        logger.info(
            "Mode Webhook configuré (nécessite un serveur d_application externe comme FastAPI/Uvicorn)."
        )
        fastapi_app.add_event_handler("startup", startup_event)
        fastapi_app.add_event_handler("shutdown", shutdown_event)

        @fastapi_app.post("/")
        async def webhook_route(request: fastapi.Request):
            return await webhook_handler_post(request, ptb_application) 

if __name__ == "__main__":
    main_bot()
    if not MODE_POLLING:
        port = int(os.getenv("PORT", "8000"))
        logger.info(f"Démarrage du serveur Uvicorn sur le port {port} pour le mode webhook (test local)...")
        uvicorn.run(fastapi_app, host="0.0.0.0", port=port, log_level="info")

