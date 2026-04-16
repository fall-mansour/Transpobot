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
# CONFIGURATION & INITIALISATION
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TranspoBot API",
    description="API de gestion de transport urbain avec assistant IA",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration Database
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 17219)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "defaultdb"),
    "ssl_disabled": False, 
    "charset": "utf8mb4",
    "autocommit": True
}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ============================================================
# DATABASE UTILS
# ============================================================

def get_db():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as e:
        logger.error(f"DB connection error: {e}")
        raise HTTPException(status_code=503, detail=f"Connexion DB impossible")

def execute_query(sql: str, params=None):
    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params or ())
        results = cursor.fetchall()
        return results
    except Exception as e:
        logger.error(f"Query error: {e}")
        return [] # On retourne une liste vide pour éviter de faire planter le front
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()

# ============================================================
# ROUTES NAVIGATION & API
# ============================================================

@app.get("/")
async def read_index():
    paths = ["index.html", "frontend/index.html"]
    for path in paths:
        if os.path.exists(path): return FileResponse(path)
    raise HTTPException(status_code=404, detail="index.html non trouvé")

@app.get("/api/dashboard/kpis")
def get_kpis():
    try:
        # KPIs Véhicules
        veh = execute_query("SELECT statut, COUNT(*) as count FROM vehicules GROUP BY statut")
        v_stats = {v["statut"]: v["count"] for v in veh}
        
        # KPIs Chauffeurs
        chauf = execute_query("SELECT COUNT(*) as total, SUM(disponibilite=1) as actifs FROM chauffeurs")[0]
        
        # KPIs Trajets Aujourd'hui
        traj = execute_query("""
            SELECT COUNT(*) as total, SUM(statut='termine') as termines, 
            COALESCE(SUM(recette),0) as recette_totale
            FROM trajets WHERE DATE(date_heure_depart) = CURDATE()
        """)[0]

        # KPIs Incidents
        inc = execute_query("SELECT COUNT(*) as total FROM incidents WHERE resolu = 0")[0]

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

@app.get("/api/vehicules")
def get_vehicules():
    return {"data": execute_query("SELECT * FROM vehicules ORDER BY created_at DESC")}

@app.get("/api/chauffeurs")
def get_chauffeurs():
    return {"data": execute_query("SELECT * FROM chauffeurs ORDER BY nom ASC")}

@app.get("/api/trajets")
def get_trajets(limit: int = 20):
    sql = """
        SELECT t.*, v.immatriculation, c.nom as chauffeur_nom, c.prenom as chauffeur_prenom
        FROM trajets t
        LEFT JOIN vehicules v ON t.vehicule_id = v.id
        LEFT JOIN chauffeurs c ON t.chauffeur_id = c.id
        ORDER BY t.date_heure_depart DESC LIMIT %s
    """
    return {"data": execute_query(sql, (limit,))}

@app.get("/api/incidents")
def get_incidents(limit: int = 20):
    return {"data": execute_query("SELECT * FROM incidents ORDER BY date_incident DESC LIMIT %s", (limit,))}

# ============================================================
# CHATBOT LOGIC
# ============================================================

class ChatMessage(BaseModel):
    message: str
    history: Optional[List[dict]] = []

@app.post("/api/chat")
async def chat(payload: ChatMessage):
    if not client:
        return {"response": "Assistant désactivé (Clé API manquante)."}
    
    # Ici, tu peux remettre ta logique OpenAI complète
    return {"response": "Assistant opérationnel pour SmartTech Central."}

# ============================================================
# LANCEMENT & STATIQUES
# ============================================================

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
elif os.path.exists("frontend/static"):
    app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now()}
