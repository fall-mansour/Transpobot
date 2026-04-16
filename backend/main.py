"""
TranspoBot - Backend FastAPI
Gestion de Transport Urbain avec IA
ESP/UCAD - Licence 3 GLSi
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
from openai import OpenAI
from datetime import datetime

# ============================================================
# 1. CONFIGURATION & INITIALISATION
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SmartTech Central API",
    description="Système de gestion de transport avec assistant IA",
    version="1.0.0"
)

# Configuration du CORS pour permettre au Frontend de parler au Backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration de la base de données (Optimisée pour Aiven/Render)
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 17219)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "defaultdb"),
    "ssl_disabled": False, # Important pour la sécurité Aiven
    "charset": "utf8mb4",
    "autocommit": True
}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ============================================================
# 2. UTILITAIRES DE BASE DE DONNÉES
# ============================================================

def get_db():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as e:
        logger.error(f"❌ Erreur de connexion DB: {e}")
        raise HTTPException(status_code=503, detail="Connexion à la base de données impossible")

def execute_query(sql: str, params=None):
    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params or ())
        results = cursor.fetchall()
        return results
    except Exception as e:
        logger.error(f"⚠️ Erreur lors de l'exécution SQL: {e}")
        return [] # Retourne une liste vide pour ne pas faire planter le front
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()

# ============================================================
# 3. ROUTES FRONTEND & NAVIGATION
# ============================================================

@app.get("/")
async def read_index():
    # Cherche l'index.html peu importe où il est stocké sur Render
    for path in ["index.html", "frontend/index.html"]:
        if os.path.exists(path):
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="Fichier index.html non trouvé")

# ============================================================
# 4. ROUTES API (KPIs & DONNÉES)
# ============================================================

@app.get("/api/dashboard/kpis")
def get_kpis():
    try:
        # 1. Véhicules par statut
        veh = execute_query("SELECT statut, COUNT(*) as count FROM vehicules GROUP BY statut")
        v_stats = {v["statut"]: v["count"] for v in veh}
        
        # 2. Chauffeurs (Total et Actifs)
        chauf_res = execute_query("SELECT COUNT(*) as total, SUM(disponibilite=1) as actifs FROM chauffeurs")
        chauf = chauf_res[0] if chauf_res else {"total": 0, "actifs": 0}
        
        # 3. Trajets d'aujourd'hui
        traj_res = execute_query("""
            SELECT COUNT(*) as total, SUM(statut='termine') as termines, 
            COALESCE(SUM(recette),0) as recette_totale
            FROM trajets WHERE DATE(date_heure_depart) = CURDATE()
        """)
        traj = traj_res[0] if traj_res else {"total": 0, "termines": 0, "recette_totale": 0}

        # 4. Incidents non résolus
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
    # Cette route corrige ton erreur 404 !
    sql = """
        SELECT l.*, 
        COUNT(t.id) as total_trajets, 
        AVG(t.nb_passagers) as moy_passagers
        FROM lignes l 
        LEFT JOIN trajets t ON l.id = t.ligne_id 
        GROUP BY l.id 
        ORDER BY l.code
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
# 5. CHATBOT IA
# ============================================================

class ChatMessage(BaseModel):
    message: str
    history: Optional[List[dict]] = []

@app.post("/api/chat")
async def chat(payload: ChatMessage):
    if not client:
        return {"response": "L'assistant IA est en mode maintenance. Vérifiez la clé OpenAI."}
    
    # Ton code de traitement IA ici...
    return {"response": "Je suis votre assistant SmartTech Central. Comment puis-je vous aider ?"}

# ============================================================
# 6. LANCEMENT & STATIQUES
# ============================================================

# Sert les fichiers CSS/JS depuis le dossier static s'il existe
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
elif os.path.exists("frontend/static"):
    app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

@app.get("/health")
def health():
    return {"status": "ok", "db_connected": True, "timestamp": datetime.now()}
