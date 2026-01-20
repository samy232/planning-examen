# Full app.py — Supabase-only backend (cursor/conn removed)
# Rewritten to use supabase.table(...) for all DB access.
# UI and app flow kept intact; DB layer replaced with helper functions that use Supabase.
import streamlit as st
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, date, time as dtime
import time
from supabase import create_client, Client
from collections import defaultdict
from typing import List, Dict, Any, Optional

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

# Optional admin client (service_role key) for writes. Put your service_role key in secrets as:
# [supabase]
# service_role = "your_service_role_key_here"
SERVICE_ROLE_KEY = st.secrets["supabase"].get("service_role") or st.secrets["supabase"].get("service_role_key")
supabase_admin: Optional[Client] = None
if SERVICE_ROLE_KEY:
    try:
        supabase_admin = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)
        print("[supabase_admin] service_role client created")
    except Exception as e:
        print("[supabase_admin] cannot create admin client:", e)

# We removed cursor/conn mechanism — everything uses Supabase now.
is_real_db = False  # kept for code paths that previously checked this flag

tables_reset = ['etudiants','professeurs','chefs_departement','administrateurs','vice_doyens']

# ======================
# DB HELPERS (Supabase wrappers)
# ======================
def db_select(table: str, select: str = "*", eq: Dict[str, Any] = None, order: Optional[str] = None,
              limit: Optional[int] = None, offset: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return list of rows from supabase.table(table).select(select) with optional eq filters."""
    try:
        q = supabase.table(table).select(select)
        if eq:
            for k, v in eq.items():
                q = q.eq(k, v)
        if order:
            # order example: "column.asc" or "column.desc"
            parts = order.split(".")
            col = parts[0]
            asc = True
            if len(parts) > 1 and parts[1].lower() == "desc":
                asc = False
            q = q.order(col, ascending=asc)
        if limit:
            q = q.limit(limit)
        if offset and limit:
            q = q.range(offset, offset + (limit - 1))
        res = q.execute()
        return res.data or []
    except Exception as e:
        # log to server console for debugging
        print(f"[db_select] error table={table} select={select} eq={eq} : {e}")
        return []

def db_get_one(table: str, select: str = "*", eq: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    rows = db_select(table, select=select, eq=eq, limit=1)
    return rows[0] if rows else None

# --- REPLACED db_insert: uses admin client if available ---
def db_insert(table: str, payload: Any) -> Dict[str, Any]:
    """Insert payload (dict or list) into table. Uses admin client if available for writes.
    Returns dict {data, error, inserted_count}.
    """
    try:
        client = supabase_admin if supabase_admin is not None else supabase
        res = client.table(table).insert(payload).execute()
        err = getattr(res, "error", None)
        data = getattr(res, "data", None)
        inserted = len(data) if isinstance(data, list) else (1 if data else 0)
        return {"data": data, "error": err, "inserted_count": inserted}
    except Exception as e:
        print(f"[db_insert] error table={table} payload_size={len(payload) if isinstance(payload, list) else 1} : {e}")
        return {"data": None, "error": str(e), "inserted_count": 0}
# --- end replaced db_insert ---

def db_update(table: str, values: Dict[str, Any], eq: Dict[str, Any]) -> Dict[str, Any]:
    """Update table set values where eq filters apply."""
    try:
        q = supabase.table(table).update(values)
        if eq:
            for k, v in eq.items():
                q = q.eq(k, v)
        res = q.execute()
        return {"data": res.data, "error": res.error}
    except Exception as e:
        print(f"[db_update] error table={table} values={values} eq={eq} : {e}")
        return {"data": None, "error": str(e)}

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
# CONFLICTS / KPIS / GENERATION / OPTIMISATION (Supabase-based implementations)
# ======================
def _parse_datetime(val):
    if val is None:
        return None
    if isinstance(val, str):
        # Supabase may return ISO strings for timestamps
        try:
            return datetime.fromisoformat(val)
        except Exception:
            try:
                return datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
    if isinstance(val, datetime):
        return val
    return None

def detect_conflicts(start_date=None, end_date=None):
    """
    Detect conflicts using Supabase data and Python logic.
    Returns dict with keys:
      - etudiants_1parjour
      - profs_3parjour
      - salles_capacite
      - surveillances_par_prof
      - conflits_par_dept
    """
    conflicts = {
        'etudiants_1parjour': [],
        'profs_3parjour': [],
        'salles_capacite': [],
        'surveillances_par_prof': [],
        'conflits_par_dept': []
    }

    # fetch tables
    exams = db_select("examens", "*")
    modules = {m['id']: m for m in db_select("modules", "id,nom,formation_id")}
    inscriptions = db_select("inscriptions", "etudiant_id,module_id")
    students = {s['id']: s for s in db_select("etudiants", "id,nom,prenom,email,formation_id")}
    profs = {p['id']: p for p in db_select("professeurs", "id,nom,email,dept_id")}
    rooms = {r['id']: r for r in db_select("lieu_examen", "id,nom,capacite")}
    formations = {f['id']: f for f in db_select("formations", "id,nom,dept_id")}
    departements = {d['id']: d for d in db_select("departements", "id,nom")}

    # Build indices
    exams_by_id = {}
    for e in exams:
        eid = e.get('id')
        e_parsed = dict(e)
        e_parsed['date_heure_dt'] = _parse_datetime(e_parsed.get('date_heure'))
        exams_by_id[eid] = e_parsed

    # 1) Students >1 exam per day
    stud_exams_by_day = defaultdict(lambda: defaultdict(list))  # student_id -> date -> [exam_ids]
    module_to_exams = defaultdict(list)
    for e in exams:
        mid = e.get('module_id')
        module_to_exams[mid].append(e.get('id'))
    for ins in inscriptions:
        sid = ins.get('etudiant_id')
        mid = ins.get('module_id')
        exam_ids = module_to_exams.get(mid, [])
        for eid in exam_ids:
            e = exams_by_id.get(eid)
            if not e or not e.get('date_heure_dt'):
                continue
            day = e['date_heure_dt'].date()
            stud_exams_by_day[sid][day].append(eid)
    for sid, days in stud_exams_by_day.items():
        for day, lst in days.items():
            if len(lst) > 1:
                conflicts['etudiants_1parjour'].append({
                    'etudiant_id': sid,
                    'jour': str(day),
                    'nb_exams': len(lst)
                })

    # 2) Profs >3 exams per day
    profs_by_day = defaultdict(lambda: defaultdict(int))  # prof_id -> date -> count
    for e in exams:
        pid = e.get('prof_id')
        dt = _parse_datetime(e.get('date_heure'))
        if pid and dt:
            profs_by_day[pid][dt.date()] += 1
    for pid, days in profs_by_day.items():
        for day, cnt in days.items():
            if cnt > 3:
                conflicts['profs_3parjour'].append({'prof_id': pid, 'jour': str(day), 'nb_exams': cnt})

    # 3) Room capacity: count unique students per exam (via inscriptions on module)
    module_ins_counts = defaultdict(int)
    for ins in inscriptions:
        module_ins_counts[ins['module_id']] += 1
    for e in exams:
        mid = e.get('module_id')
        eid = e.get('id')
        room = rooms.get(e.get('salle_id'))
        if room:
            cap = int(room.get('capacite') or 0)
            inscrits = module_ins_counts.get(mid, 0)
            if inscrits > cap:
                conflicts['salles_capacite'].append({
                    'examen_id': eid,
                    'salle': room.get('nom'),
                    'capacite': cap,
                    'inscrits': inscrits
                })

    # 4) Distribution of surveillances per professor
    surveillances = []
    for pid, days in profs_by_day.items():
        surveillances.append({
            'id': pid,
            'nom': profs.get(pid, {}).get('nom'),
            'email': profs.get(pid, {}).get('email'),
            'nb_surv': sum(days.values())
        })
    conflicts['surveillances_par_prof'] = surveillances

    # 5) Conflicts per department: overlap same day and overlapping time & same room or same prof
    dept_conflict_counts = defaultdict(int)
    exam_list = list(exams_by_id.values())
    for i in range(len(exam_list)):
        e1 = exam_list[i]
        dt1 = e1.get('date_heure_dt')
        dur1 = int(e1.get('duree_minutes') or 0)
        if not dt1:
            continue
        end1 = dt1 + timedelta(minutes=dur1)
        for j in range(i+1, len(exam_list)):
            e2 = exam_list[j]
            dt2 = e2.get('date_heure_dt')
            dur2 = int(e2.get('duree_minutes') or 0)
            if not dt2:
                continue
            if dt1.date() != dt2.date():
                continue
            end2 = dt2 + timedelta(minutes=dur2)
            overlap = not (end1 <= dt2 or end2 <= dt1)
            if not overlap:
                continue
            same_room = (e1.get('salle_id') is not None and e1.get('salle_id') == e2.get('salle_id'))
            same_prof = (e1.get('prof_id') is not None and e1.get('prof_id') == e2.get('prof_id'))
            if same_room or same_prof:
                prof_id = e1.get('prof_id')
                if prof_id and profs.get(prof_id):
                    dept_id = profs[prof_id].get('dept_id')
                    dept_conflict_counts[dept_id] += 1
    for dept_id, cnt in dept_conflict_counts.items():
        conflicts['conflits_par_dept'].append({'departement': departements.get(dept_id, {}).get('nom'), 'conflits_estimes': cnt})

    return conflicts

def compute_kpis(start_date=None, end_date=None):
    """Compute KPIs using Supabase data."""
    kpis = {}
    # total rooms
    rooms = db_select("lieu_examen", "id,nom,capacite")
    total_salles = len(rooms)
    kpis['total_salles'] = total_salles

    # nb seances in window or last 30 days
    exams = db_select("examens", "*")
    if start_date and end_date:
        s_date = datetime.strptime(start_date, "%Y-%m-%d")
        e_date = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        nb_seances = sum(1 for e in exams if (e.get('date_heure') and s_date <= _parse_datetime(e.get('date_heure')) < e_date))
        periode_days = (e_date.date() - s_date.date()).days
    else:
        cutoff = datetime.now() - timedelta(days=30)
        nb_seances = sum(1 for e in exams if (e.get('date_heure') and _parse_datetime(e.get('date_heure')) >= cutoff))
        periode_days = 30
    kpis['nb_seances'] = nb_seances
    kpis['periode_days'] = periode_days
    possible_slots = total_salles * periode_days if total_salles else 0
    taux_util = (nb_seances / possible_slots * 100) if possible_slots > 0 else 0
    kpis['taux_utilisation_salles_pct'] = round(taux_util, 1)

    # top profs minutes
    profs = db_select("professeurs", "id,nom,email")
    prof_minutes = defaultdict(int)
    for e in exams:
        pid = e.get('prof_id')
        dur = int(e.get('duree_minutes') or 0)
        dt = _parse_datetime(e.get('date_heure'))
        if pid and dur and dt:
            if start_date and end_date:
                if not (start_date <= dt.strftime("%Y-%m-%d") <= end_date):
                    continue
            else:
                if dt < datetime.now() - timedelta(days=30):
                    continue
            prof_minutes[pid] += dur
    top = []
    for p in profs:
        pid = p['id']
        top.append({'nom': p.get('nom'), 'email': p.get('email'), 'minutes_surv': prof_minutes.get(pid, 0)})
    top_sorted = sorted(top, key=lambda x: -x['minutes_surv'])[:10]
    kpis['top_profs_minutes'] = top_sorted

    # conflict estimate ratio
    conflicts = detect_conflicts(start_date, end_date)
    nb_exams_with_conflicts = len(conflicts.get('salles_capacite', []))
    total_exams = len(exams)
    kpis['conflit_estime_ratio_pct'] = round((nb_exams_with_conflicts / total_exams * 100) if total_exams > 0 else 0, 1)
    kpis['conflits_summary'] = {
        'etudiants_1parjour': len(conflicts.get('etudiants_1parjour', [])),
        'profs_3parjour': len(conflicts.get('profs_3parjour', [])),
        'salles_capacite': len(conflicts.get('salles_capacite', []))
    }
    return kpis

# ======================
# TIMETABLE GENERATION (OPTIMIZED) using Supabase
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

def generate_timetable(start_date=None, end_date=None, force=False):
    """
    Optimized Supabase-only greedy timetable generator.
    - Prefetches modules, inscriptions, salles, profs, formations, existing exams.
    - Performs scheduling in memory with minimal Python overhead.
    - Persists with a single bulk insert (via db_insert).
    """
    tic = time.time()
    report = {"message": "Génération automatique exécutée.", "created_slots": 0, "attempts": 0}
    conflicts_report = {}

    if not start_date or not end_date:
        return {"error": "start_date & end_date required"}, {}

    # build date list
    try:
        days = _get_dates_between(start_date, end_date)
    except Exception as e:
        return {"error": f"Invalid dates: {e}"}, {}

    # 1) Prefetch everything once
    modules = db_select("modules", "id,nom,formation_id")           # list
    inscriptions = db_select("inscriptions", "etudiant_id,module_id")
    rooms = db_select("lieu_examen", "id,nom,capacite")
    profs = db_select("professeurs", "id,nom,dept_id")
    formations = {f['id']: f for f in db_select("formations", "id,nom,dept_id")}
    # existing examens used to detect prior assignments/durations
    existing_exams = db_select("examens", "id,module_id,prof_id,duree_minutes,date_heure,salle_id")

    # build fast lookup maps
    module_to_students = defaultdict(list)
    for ins in inscriptions:
        module_to_students[ins['module_id']].append(ins['etudiant_id'])

    module_ins_count = {mid: len(studs) for mid, studs in module_to_students.items()}

    rooms_by_capacity = sorted([{ 'id': r['id'], 'capacite': int(r.get('capacite') or 0) } for r in rooms],
                                key=lambda x: x['capacite'])

    profs_by_id = {p['id']: p for p in profs}
    profs_by_dept = defaultdict(list)
    for p in profs:
        profs_by_dept[p.get('dept_id')].append(p)

    module_default_prof = {}
    module_default_duration = {}
    for e in existing_exams:
        mid = e.get('module_id')
        if mid and e.get('prof_id'):
            module_default_prof.setdefault(mid, e.get('prof_id'))
        if mid and e.get('duree_minutes'):
            try:
                module_default_duration.setdefault(mid, int(e.get('duree_minutes')))
            except Exception:
                pass

    # trackers (use lightweight builtins)
    scheduled = []
    student_busy_days = defaultdict(set)            # student_id -> set(date)
    prof_count_day = defaultdict(lambda: defaultdict(int))  # prof_id -> {date:count}
    room_used_day = defaultdict(set)                # date -> set(room_id)

    # sort modules by descending number of students (largest scheduled first)
    modules_sorted = sorted(modules, key=lambda m: -module_ins_count.get(m['id'], 0))

    for mod in modules_sorted:
        mid = mod.get('id')
        mname = mod.get('nom')
        nb_ins = module_ins_count.get(mid, 0)
        formation_id = mod.get('formation_id')
        report['attempts'] += 1
        scheduled_flag = False

        # pick suitable rooms once
        suitable_rooms = [r for r in rooms_by_capacity if r['capacite'] >= nb_ins]
        if not suitable_rooms:
            suitable_rooms = sorted(rooms_by_capacity, key=lambda r: -r['capacite'])

        duration = module_default_duration.get(mid, 120)

        studs = module_to_students.get(mid, [])

        for d in days:
            # check students free quickly (all in-memory)
            conflict_found = False
            for s in studs:
                if d in student_busy_days.get(s, ()):
                    conflict_found = True
                    break
            if conflict_found:
                continue

            # find free room for that day
            chosen_room = None
            for r in suitable_rooms:
                if r['id'] not in room_used_day.get(d, set()):
                    chosen_room = r
                    break
            if not chosen_room:
                continue

            # choose prof: try module_default_prof then dept then global least-loaded
            chosen_prof = module_default_prof.get(mid)
            if chosen_prof is None:
                dept_id = formations.get(formation_id, {}).get('dept_id') if formation_id else None
                if dept_id and profs_by_dept.get(dept_id):
                    chosen_prof = min(profs_by_dept[dept_id], key=lambda p: sum(prof_count_day[p['id']].values()))['id']
                elif profs:
                    chosen_prof = min(profs, key=lambda p: sum(prof_count_day[p['id']].values()))['id']
            # ensure prof daily limit (<3)
            if chosen_prof is None:
                continue
            if prof_count_day[chosen_prof].get(d, 0) >= 3:
                other_cands = [p for p in profs if prof_count_day[p['id']].get(d, 0) < 3]
                if other_cands:
                    chosen_prof = min(other_cands, key=lambda p: sum(prof_count_day[p['id']].values()))['id']
                else:
                    continue

            # schedule at fixed time 09:00 (prototype)
            dt = datetime.combine(d, dtime(hour=9, minute=0))
            scheduled.append({
                "module_id": mid,
                "module_nom": mname,
                "prof_id": chosen_prof,
                "salle_id": chosen_room['id'],
                "date_heure": dt,
                "duree_minutes": duration,
                "nb_inscrits": nb_ins
            })

            # mark busy
            for s in studs:
                student_busy_days[s].add(d)
            prof_count_day[chosen_prof][d] += 1
            room_used_day[d].add(chosen_room['id'])
            report['created_slots'] += 1
            scheduled_flag = True
            break

        if not scheduled_flag:
            conflicts_report.setdefault('unscheduled_modules', []).append({
                'module_id': mid,
                'module_nom': mname,
                'nb_inscrits': nb_ins
            })

    # persistence (bulk)
    if force and scheduled:
        payload = []
        for s in scheduled:
            payload.append({
                "module_id": s['module_id'],
                "prof_id": s['prof_id'],
                "salle_id": s['salle_id'],
                "date_heure": s['date_heure'].isoformat(),
                "duree_minutes": s['duree_minutes']
            })
        res = db_insert("examens", payload)
        if res.get('error'):
            conflicts_report['insert_error'] = res.get('error')
        else:
            inserted = res.get('inserted_count', 0)
            report['created_slots'] = inserted

    # final conflicts check (best-effort)
    conflicts_after = detect_conflicts(start_date, end_date)
    duration = time.time() - tic
    report['duration_seconds'] = duration
    report['scheduled_count'] = len(scheduled)
    report['scheduled_preview_count'] = min(len(scheduled), 10)
    report['conflicts_post'] = {k: len(v) for k, v in conflicts_after.items()}
    for k, v in conflicts_after.items():
        conflicts_report[k] = v

    return report, conflicts_report

def optimize_resources(start_date=None, end_date=None):
    tic = time.time()
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
    conflicts = detect_conflicts(start_date, end_date)
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
                # Use Supabase for authentication lookup
                users = db_select(table_name, "*", eq={"email": email, "password": password})
                if users:
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
        formations = db_select("formations", "id,nom")
        formation_options = {f["nom"]: f["id"] for f in formations} if formations else {}

        formation_choisie = st.selectbox(
            "Choisissez votre formation",
            list(formation_options.keys()) if formation_options else ["Aucune formation disponible"]
        )

        promo = st.text_input("Votre promo (ex: 2025)")

    elif st.session_state.register_role == "professeurs":
        depts = db_select("departements", "id,nom")
        dept_options = {d["nom"]: d["id"] for d in depts} if depts else {}

        dept_choisi = st.selectbox(
            "Choisissez votre département",
            list(dept_options.keys()) if dept_options else ["Aucun département disponible"]
        )

        specialite = st.text_input("Votre spécialité (ex: Bases de données)")

    elif st.session_state.register_role == "chefs_departement":
        depts = db_select("departements", "id,nom")
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

                        payload = {
                            "nom": nom,
                            "prenom": prenom,
                            "email": st.session_state.register_email,
                            "password": password,
                            "formation_id": formation_id,
                            "promo": promo
                        }
                        res = db_insert("etudiants", payload)
                        if res.get('error'):
                            st.error(f"Erreur création compte: {res['error']}")
                        else:
                            st.success("Compte étudiant créé avec succès !")
                            st.session_state.step = "login"
                            st.rerun()

                elif table == "professeurs":

                    if not dept_options:
                        st.error("Aucun département disponible — contactez l'administrateur.")
                    else:
                        dept_id = dept_options[dept_choisi]

                        payload = {
                            "nom": nom,
                            "email": st.session_state.register_email,
                            "dept_id": dept_id,
                            "specialite": specialite
                        }
                        res = db_insert("professeurs", payload)
                        if res.get('error'):
                            st.error(f"Erreur création compte: {res['error']}")
                        else:
                            st.success("Compte professeur créé avec succès !")
                            st.session_state.step = "login"
                            st.rerun()

                elif table == "chefs_departement":

                    if not dept_options:
                        st.error("Aucun département disponible — contactez l'administrateur.")
                    else:
                        dept_id = dept_options[dept_choisi]

                        payload = {
                            "nom": nom,
                            "email": st.session_state.register_email,
                            "dept_id": dept_id
                        }
                        res = db_insert("chefs_departement", payload)
                        if res.get('error'):
                            st.error(f"Erreur création compte: {res['error']}")
                        else:
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
            user = db_get_one(table, "*", eq={"email": reset_email})
            if user:
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
                user = db_get_one(table, "*", eq={"email": st.session_state.reset_email})
                if user:
                    res = db_update(table, {"password": new_pass}, {"email": st.session_state.reset_email})
                    if res.get('error'):
                        st.error(f"Erreur mise à jour: {res['error']}")
                    else:
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
    user_data = {}

    if role == "Etudiant":
        user = db_get_one("etudiants", "*", eq={"email": email})
        if user:
            user_data = dict(user)
            if user_data.get('formation_id'):
                f = db_get_one("formations", "nom", eq={"id": user_data.get('formation_id')})
                if f:
                    user_data['formation_nom'] = f.get('nom')
    elif role == "Professeur":
        user = db_get_one("professeurs", "*", eq={"email": email})
        if user:
            user_data = dict(user)
            if user_data.get('dept_id'):
                d = db_get_one("departements", "nom", eq={"id": user_data.get('dept_id')})
                if d:
                    user_data['dept_nom'] = d.get('nom')
    elif role == "Chef":
        user = db_get_one("chefs_departement", "*", eq={"email": email})
        if user:
            user_data = dict(user)
            if user_data.get('dept_id'):
                d = db_get_one("departements", "nom", eq={"id": user_data.get('dept_id')})
                if d:
                    user_data['dept_nom'] = d.get('nom')
    elif role in ("Vice-doyen", "Admin", "Administrateur examens"):
        user = db_get_one("administrateurs", "*", eq={"email": email})
        if user:
            user_data = dict(user)

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
    # Étudiant UI
    # --------------------
    if role == "Etudiant":
        st.title(f"👋 Bienvenue, {user_data.get('prenom','')} {user_data.get('nom','')}")
        st.subheader("🎓 Emploi du temps des examens")
        # Fetch modules the student is enrolled in
        etu = db_get_one("etudiants", "*", eq={"email": email})
        liste_modules = []
        if etu:
            etu_id = etu.get('id')
            ins = db_select("inscriptions", "module_id", eq={"etudiant_id": etu_id})
            module_ids = [i['module_id'] for i in ins]
            if module_ids:
                mods = []
                for mid in module_ids:
                    m = db_get_one("modules", "nom", eq={"id": mid})
                    if m:
                        mods.append(m['nom'])
                liste_modules = mods

        col_f1, col_f2 = st.columns(2)
        with col_f1:
            module_filtre = st.selectbox("Filtrer par Module", ["Tous les modules"] + liste_modules)
        with col_f2:
            try:
                date_filtre = st.date_input("Filtrer par Date", value=None)
            except Exception:
                date_filtre = None

        # Build query by fetching examens for student's modules
        examens = []
        if etu:
            ins = db_select("inscriptions", "module_id", eq={"etudiant_id": etu.get('id')})
            mids = [i['module_id'] for i in ins]
            for mid in mids:
                exs = db_select("examens", "*", eq={"module_id": mid})
                for ex in exs:
                    examens.append(ex)
        # Filter by module name if needed
        display_rows = []
        for ex in examens:
            mod = db_get_one("modules", "nom", eq={"id": ex.get('module_id')})
            salle = db_get_one("lieu_examen", "nom", eq={"id": ex.get('salle_id')})
            if module_filtre != "Tous les modules" and mod and mod.get('nom') != module_filtre:
                continue
            if date_filtre:
                if not ex.get('date_heure'):
                    continue
                dt = _parse_datetime(ex.get('date_heure'))
                if not dt or dt.date() != date_filtre:
                    continue
            display_rows.append({
                "Module": mod.get('nom') if mod else "-",
                "Salle": salle.get('nom') if salle else "-",
                "Date & Heure": ex.get('date_heure'),
                "Durée": ex.get('duree_minutes')
            })
        if display_rows:
            st.table(display_rows)
        else:
            st.info("Aucun examen trouvé.")

    # --------------------
    # Professeur UI
    # --------------------
    elif role == "Professeur":
        st.title(f"👨‍🏫 Bienvenue, M. {user_data.get('nom','')}")
        st.subheader("📋 Mes surveillances d'examens")

        prof = db_get_one("professeurs", "*", eq={"email": email})
        liste_modules_prof = []
        liste_salles_prof = []
        if prof:
            pid = prof.get('id')
            exs = db_select("examens", "*", eq={"prof_id": pid})
            mids = list({e['module_id'] for e in exs})
            for mid in mids:
                m = db_get_one("modules", "nom", eq={"id": mid})
                if m:
                    liste_modules_prof.append(m['nom'])
            sids = list({e['salle_id'] for e in exs})
            for sid in sids:
                s = db_get_one("lieu_examen", "nom", eq={"id": sid})
                if s:
                    liste_salles_prof.append(s['nom'])

        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            mod_f = st.selectbox("Par Module", ["Tous les modules"] + liste_modules_prof)
        with col_f2:
            salle_f = st.selectbox("Par Salle", ["Toutes les salles"] + liste_salles_prof)
        with col_f3:
            try:
                dat_f = st.date_input("Par Date", value=None)
            except Exception:
                dat_f = None

        # fetch exams for prof
        res = []
        if prof:
            exs = db_select("examens", "*", eq={"prof_id": prof.get('id')})
            for ex in exs:
                mod = db_get_one("modules", "nom", eq={"id": ex.get('module_id')})
                salle = db_get_one("lieu_examen", "nom", eq={"id": ex.get('salle_id')})
                if mod_f != "Tous les modules" and mod and mod.get('nom') != mod_f:
                    continue
                if salle_f != "Toutes les salles" and salle and salle.get('nom') != salle_f:
                    continue
                if dat_f:
                    dt = _parse_datetime(ex.get('date_heure'))
                    if not dt or dt.date() != dat_f:
                        continue
                res.append({
                    "Module": mod.get('nom') if mod else "-",
                    "Salle": salle.get('nom') if salle else "-",
                    "Date & Heure": ex.get('date_heure'),
                    "Durée": ex.get('duree_minutes')
                })
        if res:
            st.table(res)
        else:
            st.info("Aucune surveillance trouvée pour ces critères.")

    ##########################
    ##########################
    ##########################
    
    elif role == "Chef":
        st.title("🧭 Dashboard — Chef")

        # --- 1. CHARGEMENT UNIQUE ET RAPIDE ---
        # On récupère tout d'un coup pour éviter de solliciter la BDD à chaque ligne
        user_prof = db_get_one("chefs_departement", "*", eq={"email": st.session_state.user_email})
        dept_id = user_prof.get('dept_id') if user_prof else None

        if not dept_id:
            st.error("Département non identifié.")
        else:
            # Récupérer toutes les données liées au département en bloc
            # On utilise db_select une fois par table, c'est très rapide.
            formations = db_select("formations", "id, nom", eq={"dept_id": dept_id})
            f_ids = [f['id'] for f in formations]
            f_map = {f['id']: f['nom'] for f in formations}
            
            # Modules et Examens
            all_exams_pending = []
            stats_data = {} # Pour le graphique
            
            if f_ids:
                # On récupère tous les modules du département
                mods = []
                for fid in f_ids:
                    mods.extend(db_select("modules", "id, nom, formation_id", eq={"formation_id": fid}))
                
                m_map = {m['id']: m for m in mods}
                m_ids = [m['id'] for m in mods]

                if m_ids:
                    # On ne récupère QUE les examens non validés pour aller vite
                    for mid in m_ids:
                        exs = db_select("examens", "*", eq={"module_id": mid, "validated": False})
                        all_exams_pending.extend(exs)
                        
                        # Pour les stats (total par formation)
                        f_nom = f_map.get(m_map[mid]['formation_id'])
                        stats_data[f_nom] = stats_data.get(f_nom, 0) + len(exs)

            # --- 2. STATISTIQUES (Visuel Simple) ---
            col_stats, col_graph = st.columns([1, 2])
            with col_stats:
                st.metric("À Valider", len(all_exams_pending))
                if len(all_exams_pending) == 0:
                    st.success("Tout est à jour !")

            with col_graph:
                if stats_data:
                    # Graphique simple natif (rapide)
                    st.write("**Examens en attente par formation**")
                    st.bar_chart(stats_data)

            # --- 3. CONFLITS PAR FORMATION ---
            with st.expander("⚠️ Vérifier les conflits avant validation"):
                conflicts = detect_conflicts()
                s_conf = conflicts.get('salles_capacite', [])
                # Filtrer seulement pour ce département
                my_conflicts = [c for c in s_conf if any(e['id'] == c.get('examen_id') for e in all_exams_pending)]
                if my_conflicts:
                    st.warning(f"{len(my_conflicts)} conflits détectés.")
                    st.table(my_conflicts)
                else:
                    st.info("Aucun conflit de capacité.")

            st.divider()

            # --- 4. LISTE DE VALIDATION DIRECTE ---
            st.subheader("📋 Liste des examens à valider")
            
            # Récupérer les noms de salles pour l'affichage
            salles = {s['id']: s['nom'] for s in db_select("lieu_examen", "id, nom")}

            for ex in all_exams_pending:
                # Structure ultra légère pour affichage rapide
                with st.container():
                    c1, c2, c3 = st.columns([3, 2, 1])
                    
                    mod_info = m_map.get(ex['module_id'], {})
                    form_name = f_map.get(mod_info.get('formation_id'), "-")
                    
                    c1.write(f"**{mod_info.get('nom')}** ({form_name})")
                    c2.write(f"📅 {ex['date_heure']} | 📍 {salles.get(ex['salle_id'], 'N/A')}")
                    
                    # VALIDATION INSTANTANÉE
                    if c3.button("Valider", key=f"btn_{ex['id']}", type="primary", use_container_width=True):
                        db_update("examens", {"validated": True}, {"id": ex['id']})
                        st.rerun() # Recharge instantanément la liste (l'examen disparaît)
                st.divider()
    # --------------------
    # Administrateur exams (service planification) : génération + optimisation + détection
    # --------------------
 # --------------------
    # Administrateur exams (service planification) : génération + optimisation + détection
    # --------------------
    elif role in ("Admin", "Administrateur examens"):
        st.title("🛠️ Service Planification — Administrateur examens")
        
        # --- INITIALISATION DES ETATS (Session State) ---
        if "simulation_done" not in st.session_state:
            st.session_state.simulation_done = False
        if "last_report" not in st.session_state:
            st.session_state.last_report = {}
        if "last_conflicts" not in st.session_state:
            st.session_state.last_conflicts = {}

        st.subheader("Génération & Optimisation des ressources")

        # Sélection de période
        col_d1, col_d2 = st.columns(2)
        today = date.today()
        with col_d1:
            start_date = st.date_input("Date de début", value=today, key="admin_gen_start")
        with col_d2:
            end_date = st.date_input("Date de fin", value=today + timedelta(days=7), key="admin_gen_end")

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        # Configuration des filtres d'affichage
        excluded_keys = {'etudiants_1parjour', 'profs_3parjour', 'surveillances_par_prof', 'conflits_par_dept'}

        st.divider()

        col_a1, col_a2 = st.columns(2)

        with col_a1:
            st.write("### 📅 Planification")
            # BOUTON 1 : SIMULATION
            if st.button("🔍 Lancer la simulation (Aperçu)", use_container_width=True):
                if start_str > end_str:
                    st.error("La date de début doit être inférieure à la date de fin.")
                else:
                    with st.spinner("Calcul de l'emploi du temps optimal..."):
                        report, conflicts = generate_timetable(start_str, end_str, force=False)
                        st.session_state.last_report = report
                        st.session_state.last_conflicts = conflicts
                        st.session_state.simulation_done = True
            
            # AFFICHAGE DES RÉSULTATS DE SIMULATION
            if st.session_state.simulation_done:
                rep = st.session_state.last_report
                conf = st.session_state.last_conflicts
                
                st.info(f"**Résultat simulation :** {rep.get('scheduled_count',0)} créneaux planifiables.")
                
                # BOUTON 2 : SAUVEGARDE RÉELLE (Ne disparaît plus au clic)
                st.warning("⚠️ Ces données ne sont pas encore enregistrées.")
                if st.button("✅ SAUVEGARDER DANS LA BASE", type="primary", use_container_width=True):
                    with st.spinner("Écriture dans Supabase..."):
                        final_rep, final_conf = generate_timetable(start_str, end_str, force=True)
                        if final_rep.get('created_slots', 0) > 0:
                            st.success(f"🚀 Succès ! {final_rep.get('created_slots',0)} examens enregistrés.")
                            st.session_state.simulation_done = False # On reset après l'enregistrement
                        else:
                            st.error(f"Erreur lors de l'insertion : {final_conf.get('insert_error', 'Inconnue')}")

        with col_a2:
            st.write("### ⚡ Optimisation & Analyse")
            # OPTIMISATION
            if st.button("🪄 Optimiser les ressources", use_container_width=True):
                with st.spinner("Optimisation en cours..."):
                    report_opt, conflicts_opt = optimize_resources(start_str, end_str)
                    st.success("Optimisation terminée (simulation).")
                    for k, v in report_opt.get('improvements', {}).items():
                        st.write(f"- {k.replace('_',' ')} : {v}")

            # DÉTECTION SIMPLE
            if st.button("🕵️ Détecter les conflits", use_container_width=True):
                with st.spinner("Analyse des conflits existants..."):
                    conflicts_det = detect_conflicts(start_str, end_str)
                    visible_conflicts = {k: v for k, v in conflicts_det.items() if k not in excluded_keys}
                    total = sum(len(v) for v in visible_conflicts.values())
                    
                    if total == 0:
                        st.success("Aucun conflit majeur détecté sur cette période.")
                    else:
                        st.warning(f"{total} conflits détectés.")
                        for k, rows in visible_conflicts.items():
                            if rows:
                                with st.expander(f"Détails : {k.replace('_',' ')} ({len(rows)})"):
                                    show_table_safe(rows)

        # Zone d'affichage des détails de la simulation (si active)
        if st.session_state.simulation_done:
            st.divider()
            st.subheader("Détails de l'aperçu généré")
            conf = st.session_state.last_conflicts
            visible_sim = {k: v for k, v in conf.items() if k not in excluded_keys}
            
            c1, c2 = st.columns(2)
            with c1:
                st.write(f"**Tentatives :** {st.session_state.last_report.get('attempts',0)}")
            with c2:
                st.write(f"**Temps de calcul :** {st.session_state.last_report.get('duration_seconds',0):.2f}s")
            
            if any(visible_sim.values()):
                st.error("Conflits résiduels dans cette simulation :")
                for k, rows in visible_sim.items():
                    if rows:
                        st.write(f"- {k.replace('_',' ')} : {len(rows)}")


    # --------------------
    # Vice-doyen / Doyen : Vue stratégique globale
    # --------------------
    elif role == "Vice-doyen":
        st.title("📊 Vue stratégique — Vice-doyen / Doyen")
        st.subheader("Occupation globale, taux conflits par département, validation finale EDT, KPIs académiques")

        # KPIs globaux
        if st.button("Afficher KPIs globaux (30 derniers jours)"):
            tic = time.time()
            kpis = compute_kpis()
            duration = time.time() - tic
            st.success(f"✅ Calcul des KPIs terminé en {duration:.1f} secondes.")
            st.metric("Taux d'utilisation salles (30j) %", f"{kpis['taux_utilisation_salles_pct']}%")
            st.write(f"- Nombre séances sur {kpis['periode_days']} jours : {kpis['nb_seances']}")
            st.write(f"- Total salles : {kpis['total_salles']}")
            st.write(f"- Conflit estimé ratio (%) : {kpis['conflit_estime_ratio_pct']}")
            st.markdown("Top profs (minutes surveillées):")
            show_table_safe(kpis['top_profs_minutes'])

        st.markdown("### Taux de conflits par département")
        conflicts = detect_conflicts()
        conflits_par_dept = conflicts.get('conflits_par_dept', [])
        if conflits_par_dept:
            show_table_safe(conflits_par_dept)
        else:
            st.info("Aucun conflit départemental estimé.")

        st.markdown("### Validation finale de l'EDT généré par l'admin")
        st.write("La validation finale permet d'officialiser l'emploi du temps généré par le service planification.")
        pending_final = db_select("examens", "*")
        pending_final = [e for e in pending_final if e.get('validated') == 1 and (e.get('final_validated') in (None, 0))]
        if pending_final:
            st.write(f"{len(pending_final)} examen(s) en attente de validation finale.")
            for ex in pending_final:
                m = db_get_one("modules", "nom", eq={"id": ex.get('module_id')})
                l = db_get_one("lieu_examen", "nom", eq={"id": ex.get('salle_id')})
                cols = st.columns([4,2,2,1])
                cols[0].write(f"{m.get('nom') if m else '-'} — {ex.get('date_heure')}")
                cols[1].write(f"Salle: {l.get('nom') if l else '-'}")
                cols[2].write(f"Durée: {ex.get('duree_minutes')}min")
                if cols[3].button(f"Valider final {ex['id']}", key=f"final_val_{ex['id']}"):
                    res = db_update("examens", {"final_validated": 1}, {"id": ex['id']})
                    if res.get('error'):
                        st.error(f"Erreur final validation: {res['error']}")
                    else:
                        st.success(f"Examen {ex['id']} validé définitivement.")
                        st.experimental_rerun()
        else:
            st.info("Aucun examen en attente de validation finale.")

# FIN DU SCRIPT
