"""
SmartTech Central - Backend Final
Gestion de Transport Urbain avec IA
ESP/UCAD - Licence 3 GLSi
Migré vers Anthropic Claude
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
import mysql.connector
import os
import logging
import anthropic
from datetime import datetime

# ============================================================
# 1. CONFIGURATION & LOGGING
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SmartTech Central API",
    description="API de gestion de transport urbain avec assistant IA (Claude)",
    version="2.0.0"
)

# Configuration du CORS pour le Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration Database (Aiven / Render)
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 17219)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "defaultdb"),
    "ssl_disabled": False,  # Requis pour Aiven
    "charset": "utf8mb4",
    "autocommit": True
}

# Initialisation Anthropic Claude
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

if client:
    logger.info("✅ Client Anthropic Claude initialisé avec succès")
else:
    logger.warning("⚠️ Clé ANTHROPIC_API_KEY manquante - Chatbot en mode démo")

# ============================================================
# 2. FONCTIONS DE BASE DE DONNÉES
# ============================================================

def get_db():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as e:
        logger.error(f"❌ Erreur Connexion DB: {e}")
        raise HTTPException(status_code=503, detail="Base de données inaccessible")

def execute_query(sql: str, params=None):
    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params or ())
        results = cursor.fetchall()
        return results
    except Exception as e:
        logger.error(f"⚠️ Erreur SQL: {e}")
        return []
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()

# ============================================================
# 3. ROUTES NAVIGATION (SERVIR L'HTML)
# ============================================================

@app.get("/")
async def read_index():
    paths = ["index.html", "frontend/index.html"]
    for path in paths:
        if os.path.exists(path):
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="index.html non trouvé")

# ============================================================
# 4. ROUTES API - DONNÉES & DASHBOARD
# ============================================================

@app.get("/api/dashboard/kpis")
def get_kpis():
    try:
        # Véhicules
        veh = execute_query("SELECT statut, COUNT(*) as count FROM vehicules GROUP BY statut")
        v_stats = {v["statut"]: v["count"] for v in veh}

        # Chauffeurs
        ch_res = execute_query("SELECT COUNT(*) as total, SUM(disponibilite=1) as actifs FROM chauffeurs")
        chauf = ch_res[0] if ch_res else {"total": 0, "actifs": 0}

        # Trajets aujourd'hui
        tr_res = execute_query("""
            SELECT COUNT(*) as total, SUM(statut='termine') as termines,
            COALESCE(SUM(recette),0) as recette_totale
            FROM trajets WHERE DATE(date_heure_depart) = CURDATE()
        """)
        traj = tr_res[0] if tr_res else {"total": 0, "termines": 0, "recette_totale": 0}

        # Incidents
        inc_res = execute_query("SELECT COUNT(*) as total FROM incidents WHERE resolu = 0")
        inc = inc_res[0] if inc_res else {"total": 0}

        return {
            "status": "ok",
            "data": {
                "vehicules": {**v_stats, "total": sum(v_stats.values()) if v_stats else 0},
                "chauffeurs": {"total": chauf["total"] or 0, "actifs": int(chauf["actifs"] or 0)},
                "trajets_aujourd_hui": {k: int(v or 0) for k, v in traj.items()},
                "incidents_ouverts": int(inc["total"] or 0)
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/lignes")
def get_lignes():
    sql = """
        SELECT l.*, COUNT(t.id) as total_trajets, AVG(t.nb_passagers) as moy_passagers
        FROM lignes l LEFT JOIN trajets t ON l.id = t.ligne_id
        GROUP BY l.id ORDER BY l.code
    """
    return {"data": execute_query(sql)}

@app.get("/api/vehicules")
def get_vehicules():
    return {"data": execute_query("SELECT * FROM vehicules ORDER BY created_at DESC")}

@app.get("/api/chauffeurs")
def get_chauffeurs():
    return {"data": execute_query("SELECT * FROM chauffeurs ORDER BY nom ASC")}

@app.get("/api/trajets")
def get_trajets(limit: int = 30):
    sql = """
        SELECT t.*, l.code as ligne_code, v.immatriculation,
        c.nom as chauffeur_nom, c.prenom as chauffeur_prenom
        FROM trajets t
        LEFT JOIN lignes l ON t.ligne_id = l.id
        LEFT JOIN vehicules v ON t.vehicule_id = v.id
        LEFT JOIN chauffeurs c ON t.chauffeur_id = c.id
        ORDER BY t.date_heure_depart DESC LIMIT %s
    """
    return {"data": execute_query(sql, (limit,))}

@app.get("/api/incidents")
def get_incidents(limit: int = 30):
    return {"data": execute_query("SELECT * FROM incidents ORDER BY date_incident DESC LIMIT %s", (limit,))}

# ============================================================
# 5. LOGIQUE DU CHATBOT (ANTHROPIC CLAUDE)
# ============================================================

class ChatMessage(BaseModel):
    message: str
    history: Optional[List[dict]] = []

@app.post("/api/chat")
async def chat(payload: ChatMessage):
    if not client:
        logger.warning("⚠️ Client Anthropic non configuré - réponse démo")
        return {"response": "Assistant en mode démo. Veuillez configurer la variable ANTHROPIC_API_KEY sur Render."}

    try:
        # Construction de l'historique des messages
        # Anthropic attend une liste de messages avec role "user" / "assistant"
        # Note: le message "system" est passé séparément dans l'API Anthropic
        messages = []

        if payload.history:
            # On garde les 5 derniers échanges pour limiter les tokens
            for msg in payload.history[-10:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                # Anthropic n'accepte que "user" et "assistant" (pas "system" dans messages)
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # Ajout du message courant de l'utilisateur
        messages.append({"role": "user", "content": payload.message})

        # Appel à l'API Anthropic Claude
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",   # Modèle rapide et économique
            max_tokens=1024,
            system=(
                "Tu es l'assistant intelligent de SmartTech Central, "
                "une plateforme de gestion de transport urbain au Sénégal. "
                "Tu aides les opérateurs à gérer les véhicules, chauffeurs, lignes, "
                "trajets et incidents. Réponds toujours en français, de manière "
                "claire, utile et professionnelle. Si on te pose une question hors "
                "du domaine transport, redirige poliment vers ton rôle principal."
            ),
            messages=messages
        )

        # Extraction du texte de la réponse
        reply = response.content[0].text
        logger.info(f"✅ Réponse Claude générée ({response.usage.output_tokens} tokens)")

        return {"response": reply}

    except anthropic.AuthenticationError:
        logger.error("❌ Clé API Anthropic invalide")
        return {"response": "Erreur d'authentification : vérifiez votre clé ANTHROPIC_API_KEY."}

    except anthropic.RateLimitError:
        logger.error("❌ Limite de taux Anthropic atteinte")
        return {"response": "Trop de requêtes envoyées. Veuillez patienter quelques secondes."}

    except anthropic.APIStatusError as e:
        logger.error(f"❌ Erreur API Anthropic [{e.status_code}]: {e.message}")
        return {"response": "Une erreur est survenue avec l'assistant IA. Réessayez dans un moment."}

    except Exception as e:
        logger.error(f"❌ Erreur inattendue: {e}")
        return {"response": "Désolé, je ne peux pas répondre pour le moment. Veuillez réessayer."}

# ============================================================
# 6. LANCEMENT & STATIQUES
# ============================================================

# Dossier static (CSS, JS)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
elif os.path.exists("frontend/static"):
    app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

@app.get("/health")
def health():
    return {
        "status": "ok",
        "ai_provider": "Anthropic Claude",
        "ai_model": "claude-haiku-4-5-20251001",
        "ai_ready": client is not None,
        "time": datetime.now().isoformat()
    }
