import streamlit as st
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, date
import time
from supabase import create_client, Client

# ======================
# CONFIG STREAMLIT
# ======================
st.set_page_config(
    page_title="EDT Examens",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ======================
# SUPABASE CLIENT
# ======================
SUPABASE_URL = st.secrets["supabase"]["url"]
SUPABASE_KEY = st.secrets["supabase"]["key"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ======================
# BACKWARDS COMPATIBILITY: cursor / conn
# ======================

conn = None
cursor = None

def make_cursor_from_secrets():
    db_secrets = st.secrets.get("db") or st.secrets.get("database") or {}
    if not db_secrets:
        return None, None

    host = db_secrets.get("host")
    port = db_secrets.get("port")
    user = db_secrets.get("user")
    password = db_secrets.get("password")
    database = db_secrets.get("database") or db_secrets.get("dbname")
    driver = (db_secrets.get("driver") or "auto").lower()

    if driver in ("psycopg2", "postgres", "pg", "auto"):
        try:
            import psycopg2
            import psycopg2.extras
            conn_pg = psycopg2.connect(
                host=host,
                port=port or 5432,
                user=user,
                password=password,
                dbname=database
            )
            cur_pg = conn_pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            return conn_pg, cur_pg
        except Exception:
            pass

    if driver in ("mysql", "mysql.connector", "auto"):
        try:
            import mysql.connector
            conn_my = mysql.connector.connect(
                host=host,
                port=int(port) if port else 3306,
                user=user,
                password=password,
                database=database
            )
            cur_my = conn_my.cursor(dictionary=True)
            return conn_my, cur_my
        except Exception:
            pass

    if driver in ("pymysql", "auto"):
        try:
            import pymysql
            conn_pm = pymysql.connect(
                host=host,
                port=int(port) if port else 3306,
                user=user,
                password=password,
                db=database,
                cursorclass=pymysql.cursors.DictCursor
            )
            cur_pm = conn_pm.cursor()
            return conn_pm, cur_pm
        except Exception:
            pass

    return None, None

try:
    conn, cursor = make_cursor_from_secrets()
except Exception:
    conn, cursor = None, None

if cursor is None:
    class DummyCursor:
        def __init__(self):
            self._buffer = []

        def execute(self, *args, **kwargs):
            self._buffer = []

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class DummyConn:
        def commit(self):
            pass

    cursor = DummyCursor()
    conn = DummyConn()

# ======================
# LOGIN PAGE
# ======================

def login_page():
    st.title("🔐 Connexion - EDT Examens")

    role = st.selectbox(
        "Choisissez votre rôle",
        ["Étudiant", "Professeur", "Administrateur examens", "Vice-doyen/Doyen"]
    )

    email = st.text_input("Email")
    password = st.text_input("Mot de passe", type="password")

    if st.button("Se connecter"):
        st.session_state["role"] = role
        st.session_state["user_email"] = email
        st.success(f"Connecté en tant que {role}")
        time.sleep(0.7)
        st.rerun()

# ======================
# PAGE ÉTUDIANT
# ======================

def etudiant_page():
    st.title("👨‍🎓 Espace Étudiant")
    st.subheader("📅 Mon emploi du temps des examens")

    cursor.execute("""
        SELECT e.date_heure, m.nom AS module, l.nom AS salle
        FROM examens e
        JOIN modules m ON e.module_id = m.id
        JOIN lieu_examen l ON e.salle_id = l.id
        JOIN inscriptions i ON i.module_id = m.id
        JOIN etudiants et ON i.etudiant_id = et.id
        WHERE et.email = %s
        ORDER BY e.date_heure;
    """, (st.session_state["user_email"],))

    exams = cursor.fetchall()

    if not exams:
        st.warning("Aucun examen trouvé pour le moment.")
    else:
        for ex in exams:
            st.write(f"📘 **{ex['module']}** — {ex['date_heure']} — Salle: {ex['salle']}")

# ======================
# PAGE PROFESSEUR
# ======================

def professeur_page():
    st.title("👨‍🏫 Espace Professeur")
    st.subheader("📝 Mes surveillances et examens")

    cursor.execute("""
        SELECT e.date_heure, m.nom AS module, l.nom AS salle
        FROM examens e
        JOIN modules m ON e.module_id = m.id
        JOIN lieu_examen l ON e.salle_id = l.id
        JOIN professeurs p ON e.prof_id = p.id
        WHERE p.email = %s
        ORDER BY e.date_heure;
    """, (st.session_state["user_email"],))

    exams = cursor.fetchall()

    if not exams:
        st.info("Aucune surveillance ou examen assigné.")
    else:
        for ex in exams:
            st.write(f"📗 **{ex['module']}** — {ex['date_heure']} — Salle: {ex['salle']}")

# ======================
# ADMIN EXAMENS PAGE
# ======================

def admin_examens_page():
    st.title("🛠️ Service Planification - Admin Examens")

    st.subheader("📌 Génération automatique de l'EDT")

    if st.button("🚀 Générer EDT automatiquement"):
        st.info("Génération en cours...")
        time.sleep(2)

        cursor.execute("""
            INSERT INTO examens (module_id, prof_id, salle_id, date_heure, duree_minutes)
            SELECT 
                m.id,
                (SELECT id FROM professeurs ORDER BY RAND() LIMIT 1),
                (SELECT id FROM lieu_examen ORDER BY RAND() LIMIT 1),
                NOW() + INTERVAL FLOOR(RAND()*7) DAY,
                120
            FROM modules m;
        """)
        conn.commit()

        st.success("EDT généré avec succès !")

    st.subheader("⚠️ Détection des conflits")

    cursor.execute("""
        SELECT e1.id AS ex1, e2.id AS ex2, e1.date_heure
        FROM examens e1
        JOIN examens e2 
        ON e1.date_heure = e2.date_heure 
        AND e1.id <> e2.id;
    """)

    conflicts = cursor.fetchall()

    if conflicts:
        st.error(f"{len(conflicts)} conflits détectés !")
        st.dataframe(conflicts)
    else:
        st.success("Aucun conflit détecté 🎯")

# ======================
# VICE-DOYEN / DOYEN PAGE
# ======================

def vice_doyen_page():
    st.title("🎓 Tableau de bord stratégique")

    st.subheader("📊 KPI Académiques")

    cursor.execute("SELECT COUNT(*) AS total_examens FROM examens;")
    total = cursor.fetchone()
    total_examens = total["total_examens"] if total else 0

    cursor.execute("""
        SELECT COUNT(DISTINCT salle_id) AS salles_utilisees FROM examens;
    """)
    salles = cursor.fetchone()
    salles_utilisees = salles["salles_utilisees"] if salles else 0

    st.metric("📘 Nombre total d'examens", total_examens)
    st.metric("🏫 Salles utilisées", salles_utilisees)

    st.subheader("Occupation des salles")

    cursor.execute("""
        SELECT l.nom, COUNT(e.id) AS nb_examens
        FROM lieu_examen l
        LEFT JOIN examens e ON e.salle_id = l.id
        GROUP BY l.id;
    """)

    st.dataframe(cursor.fetchall())

# ======================
# MAIN ROUTING
# ======================

if "role" not in st.session_state:
    login_page()
else:
    role = st.session_state["role"]

    if role == "Étudiant":
        etudiant_page()

    elif role == "Professeur":
        professeur_page()

    elif role == "Administrateur examens":
        admin_examens_page()

    elif role == "Vice-doyen/Doyen":
        vice_doyen_page()

    st.sidebar.button("Se déconnecter", on_click=st.session_state.clear)
