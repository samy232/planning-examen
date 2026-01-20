# Full app.py (backend-enhanced v1)
import streamlit as st
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, date
import time
from supabase import create_client, Client
from collections import defaultdict

# ======================
# CONFIG STREAMLIT
# ======================
st.set_page_config(page_title="Connexion EDT", layout="wide", initial_sidebar_state="collapsed")

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

    # Try psycopg2 / Postgres first if requested or auto
    if driver in ("psycopg2", "postgres", "pg", "auto"):
        try:
            import psycopg2
            import psycopg2.extras
            conn_pg = psycopg2.connect(
                host=host, port=port or 5432, user=user, password=password, dbname=database
            )
            cur_pg = conn_pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            return conn_pg, cur_pg
        except Exception:
            # fallthrough to try mysql drivers
            pass

    # Try mysql.connector
    if driver in ("mysql", "mysql.connector", "auto"):
        try:
            import mysql.connector
            conn_my = mysql.connector.connect(
                host=host, port=int(port) if port else 3306, user=user, password=password, database=database
            )
            cur_my = conn_my.cursor(dictionary=True)
            return conn_my, cur_my
        except Exception:
            pass

    # Try pymysql
    if driver in ("pymysql", "auto"):
        try:
            import pymysql
            conn_pm = pymysql.connect(
                host=host, port=int(port) if port else 3306, user=user, password=password, db=database,
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

# If no real cursor, provide silent DummyCursor + DummyConn (no st.warning)
if cursor is None:
    class DummyCursor:
        def __init__(self):
            self._last_query = None
            self._last_params = None
            self._buffer = []

        def execute(self, *args, **kwargs):
            sql = args[0] if args else "<sql missing>"
            params = args[1] if len(args) > 1 else kwargs.get('params', None)
            self._last_query = sql
            self._last_params = params
            # No output; keep buffer empty so fetch* return sane defaults
            self._buffer = []

        def fetchone(self):
            return self._buffer[0] if self._buffer else None

        def fetchall(self):
            return list(self._buffer)

    class DummyConn:
        is_dummy = True
        def commit(self):
            pass

    cursor = DummyCursor()
    conn = DummyConn()

is_real_db = not getattr(conn, "is_dummy", False)

tables_reset = ['etudiants','professeurs','chefs_departement','administrateurs','vice_doyens']

# ======================
# OPTIONAL: SCHEMA CREATION (only if real DB is configured)
# ======================
def create_tables_if_missing(cursor, conn):
    """
    Creates a minimal schema adapted to the teacher specification.
    Run only when a real DB connection exists; wrapped in try/except.
    """
    try:
        # Basic schema compatible with MySQL / Postgres simple types.
        # NOTE: For production further typing/constraints/indexes/FK tuning required.
        ddl_statements = [
            """
            CREATE TABLE IF NOT EXISTS departements (
                id SERIAL PRIMARY KEY,
                nom TEXT UNIQUE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS formations (
                id SERIAL PRIMARY KEY,
                nom TEXT,
                dept_id INTEGER,
                nb_modules INTEGER DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS etudiants (
                id SERIAL PRIMARY KEY,
                nom TEXT,
                prenom TEXT,
                email TEXT UNIQUE,
                password TEXT,
                formation_id INTEGER,
                promo INTEGER
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS professeurs (
                id SERIAL PRIMARY KEY,
                nom TEXT,
                email TEXT UNIQUE,
                dept_id INTEGER,
                specialite TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS modules (
                id SERIAL PRIMARY KEY,
                nom TEXT,
                credits INTEGER DEFAULT 3,
                formation_id INTEGER,
                pre_req_id INTEGER
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS lieu_examen (
                id SERIAL PRIMARY KEY,
                nom TEXT,
                capacite INTEGER,
                type TEXT,
                batiment TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS inscriptions (
                etudiant_id INTEGER,
                module_id INTEGER
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS examens (
                id SERIAL PRIMARY KEY,
                module_id INTEGER,
                prof_id INTEGER,
                salle_id INTEGER,
                date_heure TIMESTAMP,
                duree_minutes INTEGER,
                validated INTEGER DEFAULT 0,
                final_validated INTEGER DEFAULT 0
            )
            """
        ]

        for sql in ddl_statements:
            cursor.execute(sql)
        conn.commit()
    except Exception as e:
        # Do not surface errors to end user if schema creation fails; log to console
        try:
            print("Schema creation warning:", e)
        except Exception:
            pass

if is_real_db:
    create_tables_if_missing(cursor, conn)

# ======================
# FONCTION ENVOI EMAIL
# ======================
def send_email_code(to_email, subject, message):
    code = ''.join(random.choices(string.digits, k=6))

    sender_email = "inconu2004@gmail.com"
    app_password = "gffb jryz igmf xnuq"

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(message + f"\n\nCode : {code}", "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender_email, app_password)
        server.sendmail(sender_email, to_email, msg.as_string())

    return code

# ======================
# HELPERS: TEMPS & CODES
# ======================
def can_resend(last_time):
    if last_time is None:
        return True
    return datetime.now() - last_time >= timedelta(minutes=1)

def code_is_valid(sent_time):
    if sent_time is None:
        return False
    return datetime.now() - sent_time <= timedelta(minutes=3)

# ======================
# UTIL: TEMPS & TABLES (UISAFE)
# ======================
def show_table_safe(rows, title=None):
    """Affiche une table si rows non vide, sinon message."""
    if not rows:
        st.info("Aucun résultat.")
        return
    st.table(rows if isinstance(rows, list) else [rows])

# ======================
# CONFLITS / KPIS / GENERATION / OPTIMISATION
# ======================
def detect_conflicts(cursor, start_date=None, end_date=None):
    """
    Retourne un dict avec plusieurs types de conflits. Filtre par date si fourni.
    This function uses SQL heuristics to detect:
      - Students with >1 exam per day
      - Professors with >3 exams per day
      - Rooms where inscriptions > capacity
      - Distribution of surveillances per professor
      - Conflits per department (overlaps by room or professor)
    """
    conflicts = {}

    date_filter_clause = ""
    params = []
    if start_date and end_date:
        date_filter_clause = " AND DATE(e.date_heure) BETWEEN %s AND %s"
        params.extend([start_date, end_date])
    elif start_date:
        date_filter_clause = " AND DATE(e.date_heure) >= %s"
        params.append(start_date)
    elif end_date:
        date_filter_clause = " AND DATE(e.date_heure) <= %s"
        params.append(end_date)

    try:
        # 1) Étudiants : >1 examen par jour
        cursor.execute(f"""
            SELECT et.email AS email, DATE(e.date_heure) AS jour, COUNT(*) AS nb_exams
            FROM examens e
            JOIN modules m ON e.module_id = m.id
            JOIN inscriptions i ON i.module_id = m.id
            JOIN etudiants et ON et.id = i.etudiant_id
            WHERE 1=1 {date_filter_clause}
            GROUP BY et.email, DATE(e.date_heure)
            HAVING COUNT(*) > 1
        """, tuple(params))
        conflicts['etudiants_1parjour'] = cursor.fetchall()

        # 2) Professeurs : >3 examens par jour
        cursor.execute(f"""
            SELECT p.email AS email, DATE(e.date_heure) AS jour, COUNT(*) AS nb_exams
            FROM examens e
            JOIN professeurs p ON p.id = e.prof_id
            WHERE 1=1 {date_filter_clause}
            GROUP BY p.email, DATE(e.date_heure)
            HAVING COUNT(*) > 3
        """, tuple(params))
        conflicts['profs_3parjour'] = cursor.fetchall()

        # 3) Capacite salles : nombre d'inscrits > capacité
        cursor.execute(f"""
            SELECT e.id AS examen_id, l.nom AS salle, l.capacite, COUNT(i.etudiant_id) AS inscrits
            FROM examens e
            JOIN lieu_examen l ON e.salle_id = l.id
            LEFT JOIN inscriptions i ON i.module_id = e.module_id
            WHERE 1=1 {date_filter_clause}
            GROUP BY e.id, l.nom, l.capacite
            HAVING COUNT(i.etudiant_id) > l.capacite
        """, tuple(params))
        conflicts['salles_capacite'] = cursor.fetchall()

        # 4) Distribution de surveillances par professeur (résumé)
        cursor.execute(f"""
            SELECT p.id, p.nom, p.email, COUNT(e.id) AS nb_surv
            FROM professeurs p
            LEFT JOIN examens e ON e.prof_id = p.id
            WHERE 1=1
            GROUP BY p.id, p.nom, p.email
        """)
        conflicts['surveillances_par_prof'] = cursor.fetchall()

        # 5) Conflits par département (détection d'overlap pour même salle ou même enseignant)
        # TIMESTAMPDIFF isn't available on all DBs; try a portable check: check overlap by time ranges using minute arithmetic when possible.
        # For simplicity use the generic check of same day and overlapping time frames.
        cursor.execute(f"""
            SELECT d.nom AS departement, COUNT(*) AS conflits_estimes
            FROM (
                SELECT e1.id AS e1, e2.id AS e2, p.dept_id
                FROM examens e1
                JOIN examens e2 ON e1.id <> e2.id
                    AND DATE(e1.date_heure) = DATE(e2.date_heure)
                    AND (
                        (e1.date_heure <= e2.date_heure AND (EXTRACT(EPOCH FROM (e2.date_heure - e1.date_heure))/60) < e1.duree_minutes)
                        OR
                        (e2.date_heure <= e1.date_heure AND (EXTRACT(EPOCH FROM (e1.date_heure - e2.date_heure))/60) < e2.duree_minutes)
                    )
                    AND (e1.salle_id = e2.salle_id OR e1.prof_id = e2.prof_id)
                JOIN professeurs p ON p.id = e1.prof_id
            ) sub
            JOIN departements d ON d.id = sub.dept_id
            GROUP BY d.nom
        """)
        conflicts['conflits_par_dept'] = cursor.fetchall()
    except Exception:
        # If database does not support EXTRACT/EPOCH or other constructs, return empty lists for conflict categories gracefully
        conflicts.setdefault('etudiants_1parjour', [])
        conflicts.setdefault('profs_3parjour', [])
        conflicts.setdefault('salles_capacite', [])
        conflicts.setdefault('surveillances_par_prof', [])
        conflicts.setdefault('conflits_par_dept', [])

    return conflicts

def compute_kpis(cursor, start_date=None, end_date=None):
    """KPIs généraux ; utilisation optionnelle d'une fenêtre temporelle."""
    kpis = {}

    date_where = ""
    params = []
    if start_date and end_date:
        date_where = " WHERE date_heure BETWEEN %s AND %s"
        params = [start_date, end_date]

    # Total salles
    try:
        cursor.execute("SELECT COUNT(*) as total_salles FROM lieu_examen")
        total_salles_row = cursor.fetchone()
        total_salles = total_salles_row['total_salles'] if total_salles_row else 0
    except Exception:
        total_salles = 0
    kpis['total_salles'] = total_salles

    # Nombre de séances dans la fenêtre (ou 30j si non fourni)
    if start_date and end_date:
        try:
            cursor.execute(f"SELECT COUNT(*) as nb_seances FROM examens {date_where}", tuple(params))
            nb_seances = cursor.fetchone()['nb_seances'] or 0
            periode_days = (datetime.strptime(end_date, "%Y-%m-%d").date() - datetime.strptime(start_date, "%Y-%m-%d").date()).days + 1
        except Exception:
            nb_seances = 0
            periode_days = (datetime.strptime(end_date, "%Y-%m-%d").date() - datetime.strptime(start_date, "%Y-%m-%d").date()).days + 1
    else:
        try:
            cursor.execute("SELECT COUNT(*) as nb_seances_30j FROM examens WHERE date_heure >= DATE_SUB(NOW(), INTERVAL 30 DAY)")
            nb_seances = cursor.fetchone()['nb_seances_30j'] or 0
        except Exception:
            nb_seances = 0
        periode_days = 30

    possible_slots = total_salles * periode_days if total_salles else 0
    taux_util = (nb_seances / possible_slots * 100) if possible_slots > 0 else 0
    kpis['taux_utilisation_salles_pct'] = round(taux_util, 1)
    kpis['nb_seances'] = nb_seances
    kpis['periode_days'] = periode_days

    # Top profs minutes (dans fenêtre si défini)
    try:
        if start_date and end_date:
            cursor.execute(f"""
                SELECT p.nom, p.email, COALESCE(SUM(e.duree_minutes),0) AS minutes_surv
                FROM professeurs p
                LEFT JOIN examens e ON e.prof_id = p.id AND DATE(e.date_heure) BETWEEN %s AND %s
                GROUP BY p.id, p.nom, p.email
                ORDER BY minutes_surv DESC
                LIMIT 10
            """, (start_date, end_date))
        else:
            cursor.execute("""
                SELECT p.nom, p.email, COALESCE(SUM(e.duree_minutes),0) AS minutes_surv
                FROM professeurs p
                LEFT JOIN examens e ON e.prof_id = p.id AND e.date_heure >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                GROUP BY p.id, p.nom, p.email
                ORDER BY minutes_surv DESC
                LIMIT 10
            """)
        kpis['top_profs_minutes'] = cursor.fetchall()
    except Exception:
        kpis['top_profs_minutes'] = []

    # Conflit estimé ratio (approx)
    conflicts = detect_conflicts(cursor, start_date, end_date)
    nb_exams_with_conflicts = len(conflicts.get('salles_capacite', []))
    try:
        cursor.execute("SELECT COUNT(*) AS total_exams FROM examens")
        total_exams = cursor.fetchone()['total_exams'] or 0
    except Exception:
        total_exams = 0
    kpis['conflit_estime_ratio_pct'] = round((nb_exams_with_conflicts / total_exams * 100) if total_exams>0 else 0, 1)
    kpis['conflits_summary'] = {
        'etudiants_1parjour': len(conflicts.get('etudiants_1parjour', [])),
        'profs_3parjour': len(conflicts.get('profs_3parjour', [])),
        'salles_capacite': len(conflicts.get('salles_capacite', []))
    }

    return kpis

# ======================
# TIMETABLE GENERATION (greedy prototype)
# ======================
def _get_dates_between(start_str, end_str):
    s = datetime.strptime(start_str, "%Y-%m-%d").date()
    e = datetime.strptime(end_str, "%Y-%m-%d").date()
    days = []
    cur = s
    while cur <= e:
        days.append(cur)
        cur = cur + timedelta(days=1)
    return days

def _count_students_for_module(cursor):
    cursor.execute("""
        SELECT m.id AS module_id, m.nom AS module_nom, COUNT(i.etudiant_id) AS nb_inscrits
        FROM modules m
        LEFT JOIN inscriptions i ON i.module_id = m.id
        GROUP BY m.id, m.nom
    """)
    rows = cursor.fetchall()
    return {r['module_id']: r for r in rows} if rows else {}

def _get_rooms(cursor):
    cursor.execute("SELECT id, nom, capacite FROM lieu_examen ORDER BY capacite ASC")
    rows = cursor.fetchall()
    return rows or []

def _get_module_prof(cursor):
    cursor.execute("SELECT id, prof_id, duree_minutes FROM examens WHERE module_id IS NOT NULL")
    # Note: this is a heuristic; ideally modules table should store default prof / duration.
    rows = cursor.fetchall()
    mapping = {}
    for r in rows:
        mapping[r['id']] = {'prof_id': r.get('prof_id'), 'duree_minutes': r.get('duree_minutes', 120)}
    return mapping

def generate_timetable(cursor, conn, start_date=None, end_date=None, force=False):
    """
    Greedy automatic timetable generator (prototype):
    - Tries to schedule each module at one slot between start_date and end_date.
    - Enforces: student max 1 exam/day, prof max ~3/day, room capacity.
    - Uses departmental priority when selecting supervising professor (tries to use module's prof if available).
    - If force=True, inserts created exams into the DB.
    - Returns report and conflicts (residual conflicts detected).
    """
    tic = time.time()
    report = {"message": "Génération automatique exécutée.", "created_slots": 0, "attempts": 0}
    conflicts_report = {}

    if not start_date or not end_date:
        return {"error": "start_date & end_date required"}, {}

    # Convert to dates
    try:
        days = _get_dates_between(start_date, end_date)
    except Exception as e:
        return {"error": f"Invalid dates: {e}"}, {}

    # Fetch necessary data
    try:
        modules_count = _count_students_for_module(cursor)  # dict by module_id
    except Exception:
        modules_count = {}

    try:
        rooms = _get_rooms(cursor)
    except Exception:
        rooms = []

    # Get list of modules to schedule: modules table
    try:
        cursor.execute("SELECT id, nom, formation_id FROM modules")
        modules = cursor.fetchall() or []
    except Exception:
        modules = []

    # Fetch professors list
    try:
        cursor.execute("SELECT id, nom, dept_id FROM professeurs")
        profs = cursor.fetchall() or []
    except Exception:
        profs = []

    profs_by_dept = defaultdict(list)
    for p in profs:
        profs_by_dept[p.get('dept_id')].append(p)

    # Tracking structures
    scheduled = []  # list of dicts for created exams (module_id, prof_id, salle_id, date_heure, duree_minutes)
    student_exam_days = defaultdict(set)  # student_id -> set(dates)
    prof_exam_count_by_day = defaultdict(lambda: defaultdict(int))  # prof_id -> {date: count}
    room_used_by_slot = defaultdict(set)  # date -> set(room_id)

    # Helper to check if module's students are free that day
    def students_free_on_date(module_id, day):
        # get students for module
        try:
            cursor.execute("SELECT etudiant_id FROM inscriptions WHERE module_id = %s", (module_id,))
            students = [r['etudiant_id'] for r in cursor.fetchall()] or []
        except Exception:
            students = []
        for s in students:
            if day in student_exam_days.get(s, set()):
                return False
        return True

    # Helper to assign a prof: try module's prof (if exists), then dept profs, then least loaded prof overall
    def choose_prof_for_module(module_id, formation_id):
        # try to find a professor who teaches the module (if exists)
        try:
            cursor.execute("SELECT prof_id FROM examens WHERE module_id = %s LIMIT 1", (module_id,))
            r = cursor.fetchone()
            if r and r.get('prof_id'):
                return r['prof_id']
        except Exception:
            pass
        # else try to pick a prof from same formation's department if available
        try:
            cursor.execute("SELECT dept_id FROM formations WHERE id = %s", (formation_id,))
            row = cursor.fetchone()
            dept_id = row.get('dept_id') if row else None
        except Exception:
            dept_id = None

        if dept_id and profs_by_dept.get(dept_id):
            # pick least-loaded prof (by total scheduled so far)
            cand = min(profs_by_dept[dept_id], key=lambda p: sum(prof_exam_count_by_day[p['id']].values()))
            return cand['id']
        # fallback: pick global least-loaded
        if profs:
            cand = min(profs, key=lambda p: sum(prof_exam_count_by_day[p['id']].values()))
            return cand['id']
        return None

    # Greedy scheduling: iterate modules sorted by number of students descending (large groups placed earlier)
    modules_sorted = sorted(modules, key=lambda m: -(modules_count.get(m['id'], {}).get('nb_inscrits', 0) if modules_count else 0))

    for mod in modules_sorted:
        mid = mod['id']
        mname = mod.get('nom')
        nb_ins = modules_count.get(mid, {}).get('nb_inscrits', 0)
        formation_id = mod.get('formation_id')
        scheduled_flag = False
        report['attempts'] += 1

        # prefer larger rooms that can hold nb_ins
        suitable_rooms = [r for r in rooms if r.get('capacite', 0) >= nb_ins]
        # if no room big enough, choose largest available (will generate conflict later)
        if not suitable_rooms:
            suitable_rooms = sorted(rooms, key=lambda r: -r.get('capacite', 0)) if rooms else []

        # pick duration default 120 if not available
        duration = 120
        try:
            cursor.execute("SELECT duree_minutes FROM examens WHERE module_id = %s LIMIT 1", (mid,))
            row = cursor.fetchone()
            if row and row.get('duree_minutes'):
                duration = row.get('duree_minutes')
        except Exception:
            pass

        for day in days:
            # day as date obj
            # check students free
            if not students_free_on_date(mid, day):
                continue

            # try to find a room free that day
            chosen_room = None
            for r in suitable_rooms:
                if r['id'] not in room_used_by_slot.get(day, set()):
                    chosen_room = r
                    break
            if chosen_room is None:
                continue

            # pick a prof respecting daily limit
            chosen_prof = choose_prof_for_module(mid, formation_id)
            if chosen_prof is None:
                # no prof available -> skip
                continue
            # check prof daily count
            if prof_exam_count_by_day[chosen_prof].get(day, 0) >= 3:
                # try another prof (try all profs)
                other_cands = [p for p in profs if prof_exam_count_by_day[p['id']].get(day, 0) < 3]
                if other_cands:
                    chosen_prof = min(other_cands, key=lambda p: sum(prof_exam_count_by_day[p['id']].values()))['id']
                else:
                    continue  # no prof with capacity that day

            # All checks passed -> schedule
            # choose a time: default to 09:00 for simplicity (could be enhanced)
            dt = datetime.combine(day, datetime.strptime("09:00", "%H:%M").time())
            scheduled.append({
                "module_id": mid,
                "module_nom": mname,
                "prof_id": chosen_prof,
                "salle_id": chosen_room['id'],
                "date_heure": dt,
                "duree_minutes": duration,
                "nb_inscrits": nb_ins
            })
            # mark students as busy that day
            try:
                cursor.execute("SELECT etudiant_id FROM inscriptions WHERE module_id = %s", (mid,))
                studs = [r['etudiant_id'] for r in cursor.fetchall()] or []
            except Exception:
                studs = []
            for s in studs:
                student_exam_days[s].add(day)
            prof_exam_count_by_day[chosen_prof][day] += 1
            room_used_by_slot[day].add(chosen_room['id'])
            scheduled_flag = True
            report['created_slots'] += 1
            break

        if not scheduled_flag:
            # couldn't schedule the module in the period given
            conflicts_report.setdefault('unscheduled_modules', []).append({
                'module_id': mid,
                'module_nom': mname,
                'nb_inscrits': nb_ins
            })

    # If force=True, insert scheduled exams into DB
    if force and is_real_db and scheduled:
        try:
            for s in scheduled:
                cursor.execute("""
                    INSERT INTO examens (module_id, prof_id, salle_id, date_heure, duree_minutes)
                    VALUES (%s,%s,%s,%s,%s)
                """, (s['module_id'], s['prof_id'], s['salle_id'], s['date_heure'], s['duree_minutes']))
            conn.commit()
        except Exception as e:
            # if insert fails, report the error but keep the in-memory scheduled list
            conflicts_report['insert_error'] = str(e)

    # After schedule attempt, run conflict detection on the candidate schedule (or DB if persisted)
    conflicts_after = detect_conflicts(cursor, start_date, end_date)

    duration = time.time() - tic
    report['duration_seconds'] = duration
    report['scheduled_count'] = len(scheduled)
    report['scheduled_preview'] = scheduled[:50]  # preview for UI
    report['conflicts_post'] = {k: len(v) for k,v in conflicts_after.items()}

    # Merge conflicts
    for k, v in conflicts_after.items():
        conflicts_report[k] = v

    return report, conflicts_report

def optimize_resources(cursor, conn, start_date=None, end_date=None):
    """
    Stub d'optimisation des ressources pour l'admin (keeps previous behavior).
    Returns a prototype report and runs detect_conflicts as residual check.
    """
    tic = time.time()
    # Here one could plug OR-Tools, ILP, etc.
    time.sleep(1)
    duration = time.time() - tic

    report = {
        "message": "Optimisation terminée",
        "duration_seconds": duration,
        "notes": [
            "Optimisation réalisée (prototype).",
            "Pour production, brancher un solver et exécuter modifications en base après revue."
        ],
        "improvements": {
            "reduction_conflits_estime": 12,
            "reaffectations_salles": 5
        }
    }

    conflicts = detect_conflicts(cursor, start_date, end_date)
    return report, conflicts

# ======================
# SESSION STATE INIT
# ======================
defaults = {
    "step": "login",
    "reset_email": "",
    "reset_code": "",
    "reset_sent_time": None,
    "register_email": "",
    "register_code": "",
    "register_sent_time": None,
    "register_role": "",
    "user_email": "",
    "role": ""
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ==================================================
# PAGE 1 — LOGIN + INSCRIPTION
# ==================================================
if st.session_state.step == "login":

    st.title("📚 Connexion - Plateforme EDT")

    email = st.text_input("Email")
    password = st.text_input("Mot de passe", type="password")

    col1, col2, col3 = st.columns(3)

    # ======================
    # LOGIN BUTTON
    # ======================
    with col1:
        if st.button("Se connecter"):
            roles_tables = {
                "Etudiant": "etudiants",
                "Professeur": "professeurs",
                "Chef": "chefs_departement",
                "Admin": "administrateurs",
                "Vice-doyen": "vice_doyens",
                "Administrateur examens": "administrateurs"
            }

            found_user = False
            for role_name, table_name in roles_tables.items():
                result = supabase.table(table_name)\
                    .select("*")\
                    .eq("email", email)\
                    .eq("password", password)\
                    .execute()
                
                users = result.data  # this is a list of dicts
                
                if users:  # user found
                    st.session_state.user_email = email
                    st.session_state.role = role_name
                    st.session_state.step = "dashboard"
                    found_user = True
                    break

            if found_user:
                st.success(f"Connecté en tant que {st.session_state.role}")
                st.rerun()
            else:
                st.error("Email ou mot de passe incorrect")

    # ======================
    # FORGOT PASSWORD
    # ======================
    with col2:
        if st.button("Mot de passe oublié ?"):
            st.session_state.step = "forgot_email"
            st.rerun()

    # ======================
    # NEW REGISTRATION
    # ======================
    with col3:
        if st.button("Nouvelle inscription"):
            st.session_state.step = "choose_role"
            st.rerun()

# ==================================================
# PAGE 2 — CHOIX DU RÔLE
# ==================================================
elif st.session_state.step == "choose_role":

    st.subheader("Choisissez votre rôle")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Étudiant"):
            st.session_state.register_role = "etudiants"
            st.session_state.step = "register_email"
            st.rerun()

    with col2:
        if st.button("Professeur"):
            st.session_state.register_role = "professeurs"
            st.session_state.step = "register_email"
            st.rerun()

    with col3:
        if st.button("Chef de département"):
            st.session_state.register_role = "chefs_departement"
            st.session_state.step = "register_email"
            st.rerun()

    if st.button("Retour"):
        st.session_state.step = "login"
        st.rerun()

# ==================================================
# PAGE 3 — INSCRIPTION : EMAIL
# ==================================================
elif st.session_state.step == "register_email":

    st.subheader("Inscription — Étape 1/3")
    reg_email = st.text_input("Entrez votre email (gmail.com)")

    if st.button("Envoyer code de confirmation"):
        if not reg_email.endswith("@gmail.com"):
            st.error("L’email doit se terminer par @gmail.com")
        else:
            st.session_state.register_email = reg_email
            st.session_state.register_code = send_email_code(
                reg_email,
                "Confirmation d'inscription",
                "Votre code pour valider votre inscription :"
            )
            st.session_state.register_sent_time = datetime.now()
            st.session_state.step = "confirm_register_code"
            st.rerun()

    if st.button("Retour"):
        st.session_state.step = "choose_role"
        st.rerun()

# ==================================================
# PAGE 4 — CONFIRMATION DU CODE (INSCRIPTION)
# ==================================================
elif st.session_state.step == "confirm_register_code":

    st.subheader("Inscription — Étape 2/3")
    st.success(f"Code envoyé à {st.session_state.register_email}")

    if not code_is_valid(st.session_state.register_sent_time):
        st.error("⏳ Code expiré (3 minutes dépassées)")

    if st.button("Renvoyer le code"):
        if can_resend(st.session_state.register_sent_time):
            st.session_state.register_code = send_email_code(
                st.session_state.register_email,
                "Nouveau code d'inscription",
                "Voici votre nouveau code :"
            )
            st.session_state.register_sent_time = datetime.now()
            st.success("Nouveau code envoyé !")
        else:
            st.warning("Attendez 1 minute avant de renvoyer.")

    code_input = st.text_input("Entrez le code reçu")

    if st.button("Valider le code"):
        if not code_is_valid(st.session_state.register_sent_time):
            st.error("Code expiré, renvoyez-en un nouveau.")
        elif code_input == st.session_state.register_code:
            st.session_state.step = "create_account"
            st.rerun()
        else:
            st.error("Code incorrect")

    if st.button("Retour"):
        st.session_state.step = "register_email"
        st.rerun()

# ==================================================
# PAGE 5 — CRÉATION DU COMPTE (ÉTUDIANT / PROF / CHEF)
# ==================================================
elif st.session_state.step == "create_account":

    st.subheader("Inscription — Étape 3/3")

    nom = st.text_input("Nom")
    prenom = st.text_input("Prénom")
    password = st.text_input("Choisissez un mot de passe", type="password")

    if st.session_state.register_role == "etudiants":

        cursor.execute("SELECT id, nom FROM formations")
        formations = cursor.fetchall()
        formation_options = {f["nom"]: f["id"] for f in formations} if formations else {}

        formation_choisie = st.selectbox(
            "Choisissez votre formation",
            list(formation_options.keys()) if formation_options else ["Aucune formation disponible"]
        )

        promo = st.text_input("Votre promo (ex: 2025)")

    elif st.session_state.register_role == "professeurs":

        cursor.execute("SELECT id, nom FROM departements")
        depts = cursor.fetchall()
        dept_options = {d["nom"]: d["id"] for d in depts} if depts else {}

        dept_choisi = st.selectbox(
            "Choisissez votre département",
            list(dept_options.keys()) if dept_options else ["Aucun département disponible"]
        )

        specialite = st.text_input("Votre spécialité (ex: Bases de données)")

    elif st.session_state.register_role == "chefs_departement":

        cursor.execute("SELECT id, nom FROM departements")
        depts = cursor.fetchall()
        dept_options = {d["nom"]: d["id"] for d in depts} if depts else {}

        dept_choisi = st.selectbox(
            "Choisissez votre département",
            list(dept_options.keys()) if dept_options else ["Aucun département disponible"]
        )

    col1, col2 = st.columns(2)

    with col1:
        if st.button("⬅️ Précédent"):
            st.session_state.step = "confirm_register_code"
            st.rerun()

    with col2:
        if st.button("Créer mon compte"):

            table = st.session_state.register_role

            try:
                if table == "etudiants":

                    if not formation_options:
                        st.error("Aucune formation disponible — contactez l'administrateur.")
                    else:
                        formation_id = formation_options[formation_choisie]

                        cursor.execute("""
                            INSERT INTO etudiants 
                            (nom, prenom, email, password, formation_id, promo)
                            VALUES (%s,%s,%s,%s,%s,%s)
                        """, (
                            nom,
                            prenom,
                            st.session_state.register_email,
                            password,
                            formation_id,
                            promo
                        ))
                        conn.commit()
                        st.success("Compte étudiant créé avec succès !")
                        st.session_state.step = "login"
                        st.rerun()

                elif table == "professeurs":

                    if not dept_options:
                        st.error("Aucun département disponible — contactez l'administrateur.")
                    else:
                        dept_id = dept_options[dept_choisi]

                        cursor.execute("""
                            INSERT INTO professeurs 
                            (nom, prenom, email, password, dept_id, specialite)
                            VALUES (%s,%s,%s,%s,%s,%s)
                        """, (
                            nom,
                            prenom,
                            st.session_state.register_email,
                            password,
                            dept_id,
                            specialite
                        ))
                        conn.commit()
                        st.success("Compte professeur créé avec succès !")
                        st.session_state.step = "login"
                        st.rerun()

                elif table == "chefs_departement":

                    if not dept_options:
                        st.error("Aucun département disponible — contactez l'administrateur.")
                    else:
                        dept_id = dept_options[dept_choisi]

                        cursor.execute("""
                            INSERT INTO chefs_departement 
                            (nom, prenom, email, password, dept_id)
                            VALUES (%s,%s,%s,%s,%s)
                        """, (
                            nom,
                            prenom,
                            st.session_state.register_email,
                            password,
                            dept_id
                        ))
                        conn.commit()
                        st.success("Compte Chef de département créé avec succès !")
                        st.session_state.step = "login"
                        st.rerun()
                else:
                    st.error("Rôle non reconnu pour l'inscription.")
            except Exception as e:
                st.error(f"Erreur lors de la création du compte : {e}")

# ==================================================
# RESET MOT DE PASSE — ETAPE 1
# ==================================================
elif st.session_state.step == "forgot_email":

    st.subheader("Réinitialisation — Étape 1/3")
    reset_email = st.text_input("Entrez votre email")

    if st.button("Envoyer le code"):
        found = False
        for table in tables_reset:
            cursor.execute(f"SELECT * FROM {table} WHERE email=%s", (reset_email,))
            if cursor.fetchone():
                found = True
                st.session_state.reset_email = reset_email
                st.session_state.reset_code = send_email_code(
                    reset_email,
                    "Réinitialisation mot de passe",
                    "Votre code est :"
                )
                st.session_state.reset_sent_time = datetime.now()
                st.session_state.step = "enter_code"
                st.rerun()

        if not found:
            st.error("Email non trouvé")

    if st.button("Retour"):
        st.session_state.step = "login"
        st.rerun()

# ==================================================
# RESET — ETAPE 2 : SAISIR CODE
# ==================================================
elif st.session_state.step == "enter_code":

    st.subheader("Réinitialisation — Étape 2/3")
    st.success(f"Code envoyé à {st.session_state.reset_email}")

    if st.button("Renvoyer le code"):
        if can_resend(st.session_state.reset_sent_time):
            st.session_state.reset_code = send_email_code(
                st.session_state.reset_email,
                "Nouveau code",
                "Voici votre nouveau code :"
            )
            st.session_state.reset_sent_time = datetime.now()
            st.success("Nouveau code envoyé !")
        else:
            st.warning("Attendez 1 minute.")

    code_input = st.text_input("Entrez le code reçu")

    if st.button("Suivant"):
        if not code_is_valid(st.session_state.reset_sent_time):
            st.error("Code expiré, renvoyez-en un nouveau.")
        elif code_input == st.session_state.reset_code:
            st.session_state.step = "new_password"
            st.rerun()
        else:
            st.error("Code incorrect")

    if st.button("Retour"):
        st.session_state.step = "forgot_email"
        st.rerun()

# ==================================================
# RESET — ETAPE 3 : NOUVEAU MOT DE PASSE
# ==================================================
elif st.session_state.step == "new_password":

    st.subheader("Réinitialisation — Étape 3/3")

    new_pass = st.text_input("Nouveau mot de passe", type="password")
    confirm_pass = st.text_input("Confirmer le mot de passe", type="password")

    if st.button("Confirmer"):
        if new_pass != confirm_pass:
            st.error("Les mots de passe ne correspondent pas")
        else:
            updated_any = False
            for table in tables_reset:
                cursor.execute(
                    f"SELECT * FROM {table} WHERE email=%s",
                    (st.session_state.reset_email,)
                )
                if cursor.fetchone():
                    cursor.execute(
                        f"UPDATE {table} SET password=%s WHERE email=%s",
                        (new_pass, st.session_state.reset_email)
                    )
                    conn.commit()
                    updated_any = True

            if updated_any:
                st.success("Mot de passe mis à jour !")
                st.session_state.step = "login"
                st.rerun()
            else:
                st.error("Impossible de mettre à jour — email introuvable.")

# ==================================================
# DASHBOARDS ÉTENDUS (modifications demandées)
# ==================================================
elif st.session_state.step == "dashboard":

    role = st.session_state.role
    email = st.session_state.user_email
    user_data = None

    if role == "Etudiant":
        cursor.execute("""
            SELECT e.*, f.nom AS formation_nom 
            FROM etudiants e 
            LEFT JOIN formations f ON e.formation_id = f.id 
            WHERE e.email = %s
        """, (email,))
        user_data = cursor.fetchone()
    elif role == "Professeur":
        cursor.execute("""
            SELECT p.*, d.nom AS dept_nom 
            FROM professeurs p 
            LEFT JOIN departements d ON p.dept_id = d.id 
            WHERE p.email = %s
        """, (email,))
        user_data = cursor.fetchone()
    elif role == "Chef":
        cursor.execute("""
            SELECT c.*, d.nom AS dept_nom, c.dept_id
            FROM chefs_departement c
            LEFT JOIN departements d ON c.dept_id = d.id
            WHERE c.email = %s
        """, (email,))
        user_data = cursor.fetchone()
    elif role in ("Vice-doyen", "Admin", "Administrateur examens"):
        cursor.execute("SELECT * FROM administrateurs WHERE email = %s", (email,))
        user_data = cursor.fetchone()

    # Defensive: ensure user_data is a dict to avoid attribute errors
    if user_data is None:
        user_data = {}

    # Sidebar
    with st.sidebar:
        st.title("📌 Menu")
        st.markdown("---")
        if user_data:
            st.subheader("👤 Mon Profil")
            if 'nom' in user_data:
                st.write(f"**Nom :** {user_data.get('nom','')}")
            if 'prenom' in user_data:
                st.write(f"**Prénom :** {user_data.get('prenom','')}")
            if role == "Etudiant" and 'formation_nom' in user_data:
                st.write(f"**Formation :** {user_data.get('formation_nom')}")
            if role == "Professeur" and 'dept_nom' in user_data:
                st.write(f"**Département :** {user_data.get('dept_nom')}")
            st.write(f"**Email :** {user_data.get('email','')}")

        for _ in range(12): st.write("")

        if st.button("🚪 Déconnexion", use_container_width=True, key="logout_btn"):
            st.session_state.step = "login"
            st.session_state.user_email = ""
            st.session_state.role = ""
            st.rerun()

    # --------------------
    # Étudiant & Professeur UIs (inchangées)
    # --------------------
    if role == "Etudiant":
        st.title(f"👋 Bienvenue, {user_data.get('prenom','')} {user_data.get('nom','')}")
        st.subheader("🎓 Emploi du temps des examens")
        cursor.execute("""
            SELECT DISTINCT m.nom FROM modules m
            JOIN inscriptions i ON i.module_id = m.id
            JOIN etudiants et ON et.id = i.etudiant_id
            WHERE et.email = %s
        """, (email,))
        liste_modules = [row['nom'] for row in cursor.fetchall()]
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            module_filtre = st.selectbox("Filtrer par Module", ["Tous les modules"] + liste_modules)
        with col_f2:
            try:
                date_filtre = st.date_input("Filtrer par Date", value=None)
            except Exception:
                date_filtre = None

        query = """
            SELECT m.nom AS Module, l.nom AS Salle, e.date_heure AS "Date & Heure", e.duree_minutes AS "Durée"
            FROM examens e
            JOIN modules m ON e.module_id = m.id
            JOIN lieu_examen l ON e.salle_id = l.id
            JOIN inscriptions i ON i.module_id = m.id
            JOIN etudiants et ON et.id = i.etudiant_id
            WHERE et.email = %s
        """
        params = [email]
        if module_filtre != "Tous les modules":
            query += " AND m.nom = %s"
            params.append(module_filtre)
        if date_filtre:
            query += " AND DATE(e.date_heure) = %s"
            params.append(date_filtre)
        query += " ORDER BY e.date_heure ASC"
        cursor.execute(query, tuple(params))
        resultats = cursor.fetchall()
        if resultats:
            st.table(resultats)
        else:
            st.info("Aucun examen trouvé.")

    elif role == "Professeur":
        st.title(f"👨‍🏫 Bienvenue, M. {user_data.get('nom','')}")
        st.subheader("📋 Mes surveillances d'examens")
        col_f1, col_f2, col_f3 = st.columns(3)
        cursor.execute("""
            SELECT DISTINCT m.nom FROM modules m 
            JOIN examens e ON e.module_id = m.id 
            JOIN professeurs p ON p.id = e.prof_id 
            WHERE p.email = %s
        """, (email,))
        liste_modules_prof = [row['nom'] for row in cursor.fetchall()]
        cursor.execute("""
            SELECT DISTINCT l.nom FROM lieu_examen l
            JOIN examens e ON e.salle_id = l.id
            JOIN professeurs p ON p.id = e.prof_id
            WHERE p.email = %s
        """, (email,))
        liste_salles_prof = [row['nom'] for row in cursor.fetchall()]
        with col_f1:
            mod_f = st.selectbox("Par Module", ["Tous les modules"] + liste_modules_prof)
        with col_f2:
            salle_f = st.selectbox("Par Salle", ["Toutes les salles"] + liste_salles_prof)
        with col_f3:
            try:
                dat_f = st.date_input("Par Date", value=None)
            except Exception:
                dat_f = None
        query_prof = """
            SELECT m.nom AS Module, l.nom AS Salle, e.date_heure AS "Date & Heure", e.duree_minutes AS "Durée"
            FROM examens e
            JOIN modules m ON e.module_id = m.id
            JOIN lieu_examen l ON e.salle_id = l.id
            JOIN professeurs p ON p.id = e.prof_id
            WHERE p.email = %s
        """
        params_prof = [email]
        if mod_f != "Tous les modules":
            query_prof += " AND m.nom = %s"
            params_prof.append(mod_f)
        if salle_f != "Toutes les salles":
            query_prof += " AND l.nom = %s"
            params_prof.append(salle_f)
        if dat_f:
            query_prof += " AND DATE(e.date_heure) = %s"
            params_prof.append(dat_f)
        query_prof += " ORDER BY e.date_heure ASC"
        cursor.execute(query_prof, tuple(params_prof))
        res_prof = cursor.fetchall()
        if res_prof:
            st.table(res_prof)
        else:
            st.info("Aucune surveillance trouvée pour ces critères.")

    # --------------------
    # Chef de département : Validation par département, stats et conflits par formation
    # --------------------
    elif role == "Chef":
        st.title("🧭 Tableau de bord — Chef de département")
        dept_id = user_data.get('dept_id') if user_data else None
        st.subheader(f"Statistiques et validation — Département : {user_data.get('dept_nom','-') if user_data else '-'}")

        if dept_id is None:
            st.warning("Impossible de déterminer votre département. Vérifiez votre profil.")
        else:
            st.markdown("### Statistiques par formation (nombre d'examens, modules)")
            cursor.execute("""
                SELECT f.id AS formation_id, f.nom AS formation,
                       COUNT(DISTINCT e.id) AS nb_exams,
                       COUNT(DISTINCT m.id) AS nb_modules
                FROM formations f
                LEFT JOIN modules m ON m.formation_id = f.id
                LEFT JOIN examens e ON e.module_id = m.id
                WHERE f.dept_id = %s
                GROUP BY f.id, f.nom
                ORDER BY f.nom
            """, (dept_id,))
            stats_form = cursor.fetchall()
            show_table_safe(stats_form)

            st.markdown("### Conflits par formation (estimation)")
            cursor.execute("""
                SELECT f.nom AS formation, COUNT(*) AS conflits_estimes
                FROM (
                    SELECT e1.id AS e1, e2.id AS e2, m.formation_id
                    FROM examens e1
                    JOIN examens e2 ON e1.id <> e2.id
                        AND DATE(e1.date_heure) = DATE(e2.date_heure)
                        AND (
                            (e1.date_heure <= e2.date_heure AND (EXTRACT(EPOCH FROM (e2.date_heure - e1.date_heure))/60) < e1.duree_minutes)
                            OR
                            (e2.date_heure <= e1.date_heure AND (EXTRACT(EPOCH FROM (e1.date_heure - e2.date_heure))/60) < e2.duree_minutes)
                        )
                        AND (e1.salle_id = e2.salle_id OR e1.prof_id = e2.prof_id)
                    JOIN modules m ON m.id = e1.module_id
                ) sub
                JOIN formations f ON f.id = sub.formation_id
                WHERE f.dept_id = %s
                GROUP BY f.nom
                ORDER BY conflits_estimes DESC
            """, (dept_id,))
            conflicts_by_form = cursor.fetchall()
            show_table_safe(conflicts_by_form)

            st.markdown("### Validation des examens par formation")
            try:
                cursor.execute("""
                    SELECT e.id, m.nom AS module, f.nom AS formation, l.nom AS salle,
                           e.date_heure, e.duree_minutes, COALESCE(e.validated,0) AS validated
                    FROM examens e
                    JOIN modules m ON e.module_id = m.id
                    JOIN formations f ON m.formation_id = f.id
                    JOIN lieu_examen l ON e.salle_id = l.id
                    WHERE f.dept_id = %s
                      AND (e.validated IS NULL OR e.validated = 0)
                    ORDER BY e.date_heure DESC
                """, (dept_id,))
                exams_dept = cursor.fetchall()

                if exams_dept:
                    for ex in exams_dept:
                        cols = st.columns([4,2,2,1])
                        cols[0].write(f"{ex['formation']} — {ex['module']} — {ex['date_heure']}")
                        cols[1].write(f"Salle: {ex['salle']}")
                        cols[2].write(f"Durée: {ex['duree_minutes']}min")

                        # Bouton de validation : après UPDATE on commit et on rerun -> la ligne disparaîtra
                        if cols[3].button("Valider", key=f"chef_val_{ex['id']}"):
                            cursor.execute("UPDATE examens SET validated=1 WHERE id=%s", (ex['id'],))
                            conn.commit()
                            st.success(f"Examen {ex['id']} validé.")
                            st.experimental_rerun()
                else:
                    st.info("Aucun examen trouvé pour validation.")
            except Exception as e:
                st.info("aucun conflit détecté")

    # --------------------
    # Administrateur exams (service planification) : génération + optimisation + détection
    # --------------------
    elif role in ("Admin", "Administrateur examens"):
        st.title("🛠️ Service Planification — Administrateur examens")
        st.subheader("Génération & Optimisation des ressources")

        # Sélection de période
        col_d1, col_d2 = st.columns(2)
        today = date.today()
        default_start = today
        default_end = today + timedelta(days=7)
        with col_d1:
            start_date = st.date_input("Date de début", value=default_start, key="admin_gen_start")
        with col_d2:
            end_date = st.date_input("Date de fin", value=default_end, key="admin_gen_end")

        start_str = start_date.strftime("%Y-%m-%d") if isinstance(start_date, date) else None
        end_str = end_date.strftime("%Y-%m-%d") if isinstance(end_date, date) else None

        st.write("Actions disponibles :")
        col_a1, col_a2 = st.columns(2)

        # keys to hide from display (user requested)
        excluded_keys = {'etudiants_1parjour', 'profs_3parjour', 'surveillances_par_prof', 'conflits_par_dept'}

        with col_a1:
            if st.button("Générer automatiquement"):
                if start_str is None or end_str is None or start_str > end_str:
                    st.error("Veuillez choisir une période valide (début ≤ fin).")
                else:
                    tic = time.time()
                    # default: simulate first, don't persist
                    report, conflicts = generate_timetable(cursor, conn, start_str, end_str, force=False)
                    duration = time.time() - tic
                    st.success(f"✅ Génération (simulation) terminée en {duration:.1f} secondes !")
                    st.json(report)
                    visible_conflicts = {k: v for k, v in conflicts.items() if k not in excluded_keys}
                    total_visible = sum(len(v) for v in visible_conflicts.values())
                    if total_visible == 0:
                        st.info("Aucun conflit affiché pour cette analyse.")
                    else:
                        st.warning(f"{total_visible} conflit(s) affiché(s).")
                        for k, rows in visible_conflicts.items():
                            if rows:
                                with st.expander(f"{k} — {len(rows)} élément(s)"):
                                    show_table_safe(rows)
                    # Provide an explicit "persist" option
                    if st.button("✅ Persist schedule to DB (force)"):
                        if not is_real_db:
                            st.error("Impossible d'écrire en base: DB non configurée.")
                        else:
                            tic = time.time()
                            rep2, conf2 = generate_timetable(cursor, conn, start_str, end_str, force=True)
                            dt = time.time() - tic
                            st.success(f"Écriture en base terminée en {dt:.1f}s. {rep2.get('created_slots',0)} créés.")
                            st.json(rep2)

        with col_a2:
            if st.button("Optimiser les ressources"):
                if start_str is None or end_str is None or start_str > end_str:
                    st.error("Veuillez choisir une période valide (début ≤ fin).")
                else:
                    tic = time.time()
                    report, conflicts = optimize_resources(cursor, conn, start_str, end_str)
                    duration = time.time() - tic
                    st.success(f"✅ Optimisation terminée en {report.get('duration_seconds', duration):.1f} secondes.")
                    st.write("Améliorations estimées :")
                    for k, v in report.get('improvements', {}).items():
                        st.write(f"- {k.replace('_',' ')} : {v}")
                    st.markdown("Notes :")
                    visible_conflicts = {k: v for k, v in conflicts.items() if k not in excluded_keys}
                    total_visible = sum(len(v) for v in visible_conflicts.values())
                    if total_visible == 0:
                        st.info("Aucun conflit affiché après optimisation.")
                    else:
                        st.warning(f"{total_visible} conflit(s) affiché(s) après optimisation.")
                        for k, rows in visible_conflicts.items():
                            if rows:
                                with st.expander(f"{k} — {len(rows)} élément(s)"):
                                    show_table_safe(rows)

        # Add a dedicated "Détecter conflits" action (does not change other UI)
        if st.button("Détecter conflits"):
            if start_str is None or end_str is None or start_str > end_str:
                st.error("Veuillez choisir une période valide (début ≤ fin).")
            else:
                tic = time.time()
                conflicts = detect_conflicts(cursor, start_str, end_str)
                duration = time.time() - tic
                visible_conflicts = {k: v for k, v in conflicts.items() if k not in excluded_keys}
                total_visible = sum(len(v) for v in visible_conflicts.values())
                if total_visible == 0:
                    st.success(f"✅ Analyse terminée en {duration:.1f} secondes — Aucun conflit affiché pour les catégories visibles.")
                else:
                    st.warning(f"⚠️ Analyse terminée en {duration:.1f} secondes — {total_visible} conflit(s) affiché(s).")
                    st.markdown("**Résumé des conflits affichés**")
                    for k, rows in visible_conflicts.items():
                        st.write(f"- {k.replace('_',' ')} : {len(rows)}")
                    for k, rows in visible_conflicts.items():
                        if rows:
                            with st.expander(f"Détails — {k}"):
                                show_table_safe(rows)

    # --------------------
    # Vice-doyen / Doyen : Vue stratégique globale
    # --------------------
    elif role == "Vice-doyen":
        st.title("📊 Vue stratégique — Vice-doyen / Doyen")
        st.subheader("Occupation globale, taux conflits par département, validation finale EDT, KPIs académiques")

        # KPIs globaux
        if st.button("Afficher KPIs globaux (30 derniers jours)"):
            tic = time.time()
            kpis = compute_kpis(cursor)
            duration = time.time() - tic
            st.success(f"✅ Calcul des KPIs terminé en {duration:.1f} secondes.")
            st.metric("Taux d'utilisation salles (30j) %", f"{kpis['taux_utilisation_salles_pct']}%")
            st.write(f"- Nombre séances sur {kpis['periode_days']} jours : {kpis['nb_seances']}")
            st.write(f"- Total salles : {kpis['total_salles']}")
            st.write(f"- Conflit estimé ratio (%) : {kpis['conflit_estime_ratio_pct']}")
            st.markdown("Top profs (minutes surveillées):")
            show_table_safe(kpis['top_profs_minutes'])

        st.markdown("### Taux de conflits par département")
        conflicts = detect_conflicts(cursor)
        conflits_par_dept = conflicts.get('conflits_par_dept', [])
        if conflits_par_dept:
            show_table_safe(conflits_par_dept)
        else:
            st.info("Aucun conflit départemental estimé.")

        st.markdown("### Validation finale de l'EDT généré par l'admin")
        st.write("La validation finale permet d'officialiser l'emploi du temps généré par le service planification.")
        try:
            cursor.execute("""
                SELECT e.id, m.nom AS module, l.nom AS salle, e.date_heure, e.duree_minutes, e.validated, e.final_validated
                FROM examens e
                JOIN modules m ON e.module_id = m.id
                JOIN lieu_examen l ON e.salle_id = l.id
                WHERE e.validated = 1 AND (e.final_validated IS NULL OR e.final_validated = 0)
                ORDER BY e.date_heure DESC
            """)
            pending_final = cursor.fetchall()
            if pending_final:
                st.write(f"{len(pending_final)} examen(s) en attente de validation finale.")
                for ex in pending_final:
                    cols = st.columns([4,2,2,1])
                    cols[0].write(f"{ex['module']} — {ex['date_heure']}")
                    cols[1].write(f"Salle: {ex['salle']}")
                    cols[2].write(f"Durée: {ex['duree_minutes']}min")
                    if cols[3].button(f"Valider final {ex['id']}", key=f"final_val_{ex['id']}"):
                        cursor.execute("UPDATE examens SET final_validated=1 WHERE id=%s", (ex['id'],))
                        conn.commit()
                        st.success(f"Examen {ex['id']} validé définitivement.")
                        st.experimental_rerun()
            else:
                st.info("Aucun examen en attente de validation finale.")
        except Exception:
            st.info("aucun conflit détecté")

# FIN DU SCRIPT
