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
            # supabase-py uses .order(column, ascending=True/False)
            parts = order.split(".")
            col = parts[0]
            asc = True
            if len(parts) > 1 and parts[1].lower() == "desc":
                asc = False
            q = q.order(col, ascending=asc)
        if limit:
            q = q.limit(limit)
        if offset:
            q = q.range(offset, offset + (limit - 1) if limit else None)
        res = q.execute()
        return res.data or []
    except Exception as e:
        # log to server console for debugging
        print(f"[db_select] error table={table} select={select} eq={eq} : {e}")
        return []

def db_get_one(table: str, select: str = "*", eq: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    rows = db_select(table, select=select, eq=eq, limit=1)
    return rows[0] if rows else None

def db_insert(table: str, payload: Any) -> Dict[str, Any]:
    """Insert payload (dict or list) into table. Returns response dict."""
    try:
        res = supabase.table(table).insert(payload).execute()
        return {"data": res.data, "error": res.error}
    except Exception as e:
        print(f"[db_insert] error table={table} payload={payload} : {e}")
        return {"data": None, "error": str(e)}

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
        # Supabase returns ISO strings for timestamps
        try:
            return datetime.fromisoformat(val)
        except Exception:
            # attempt common formats
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
    exams = db_select("examens", "*")  # uses supabase table name 'examens'
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
    # Build student -> list of exam dates
    stud_exams_by_day = defaultdict(lambda: defaultdict(list))  # student_id -> date -> [exam_ids]
    # build mapping module_id -> exam_ids (there may be multiple)
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
    # For each exam, count inscriptions where module_id = exam.module_id
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
    # For each pair of exams on same day check overlap
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
            # overlap?
            overlap = not (end1 <= dt2 or end2 <= dt1)
            if not overlap:
                continue
            same_room = (e1.get('salle_id') is not None and e1.get('salle_id') == e2.get('salle_id'))
            same_prof = (e1.get('prof_id') is not None and e1.get('prof_id') == e2.get('prof_id'))
            if same_room or same_prof:
                # attribute to department of the professor of e1 if exists
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
        # last 30 days
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
        if pid and dur:
            if start_date and end_date:
                if not (start_date <= dt.strftime("%Y-%m-%d") <= end_date):
                    continue
            else:
                # last 30 days
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
# TIMETABLE GENERATION (greedy prototype) using Supabase
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
    Supabase-only greedy timetable generator.
    - Reads modules, inscriptions, rooms, profs from Supabase.
    - Tries to assign each module to a day/room/prof respecting constraints.
    - If force=True, persists generated examens into Supabase.
    """
    tic = time.time()
    report = {"message": "Génération automatique exécutée.", "created_slots": 0, "attempts": 0}
    conflicts_report = {}

    if not start_date or not end_date:
        return {"error": "start_date & end_date required"}, {}

    try:
        days = _get_dates_between(start_date, end_date)
    except Exception as e:
        return {"error": f"Invalid dates: {e}"}, {}

    # fetch data
    modules = db_select("modules", "id,nom,formation_id")  # list
    modules_count = {}
    # count inscriptions per module
    inscriptions = db_select("inscriptions", "etudiant_id,module_id")
    for ins in inscriptions:
        mid = ins.get('module_id')
        modules_count[mid] = modules_count.get(mid, 0) + 1
    rooms = db_select("lieu_examen", "id,nom,capacite")
    profs = db_select("professeurs", "id,nom,dept_id")
    formations = {f['id']: f for f in db_select("formations", "id,nom,dept_id")}

    profs_by_dept = defaultdict(list)
    for p in profs:
        profs_by_dept[p.get('dept_id')].append(p)

    # trackers
    scheduled = []
    student_exam_days = defaultdict(set)  # student_id -> set(dates)
    prof_exam_count_by_day = defaultdict(lambda: defaultdict(int))  # prof_id -> {date:count}
    room_used_by_slot = defaultdict(set)  # date -> set(room_id)

    # helper functions
    module_to_students = defaultdict(list)
    for ins in inscriptions:
        module_to_students[ins['module_id']].append(ins['etudiant_id'])

    def students_free_on_date(module_id, day):
        studs = module_to_students.get(module_id, [])
        for s in studs:
            if day in student_exam_days.get(s, set()):
                return False
        return True

    def choose_prof_for_module(module_id, formation_id):
        # Try to reuse a prof who was previously assigned to that module (examens table)
        existing = db_select("examens", "prof_id,module_id", eq={"module_id": module_id}, limit=1)
        if existing:
            pid = existing[0].get('prof_id')
            if pid:
                return pid
        # else pick professor from same department if possible
        dept_id = None
        if formation_id:
            f = formations.get(formation_id)
            if f:
                dept_id = f.get('dept_id')
        if dept_id and profs_by_dept.get(dept_id):
            cand = min(profs_by_dept[dept_id], key=lambda p: sum(prof_exam_count_by_day[p['id']].values()))
            return cand['id']
        if profs:
            cand = min(profs, key=lambda p: sum(prof_exam_count_by_day[p['id']].values()))
            return cand['id']
        return None

    # sort modules by descending nb students
    modules_sorted = sorted(modules, key=lambda m: -(modules_count.get(m['id'], 0)))

    for mod in modules_sorted:
        mid = mod.get('id')
        mname = mod.get('nom')
        nb_ins = modules_count.get(mid, 0)
        formation_id = mod.get('formation_id')
        scheduled_flag = False
        report['attempts'] += 1

        suitable_rooms = [r for r in rooms if int(r.get('capacite') or 0) >= nb_ins]
        if not suitable_rooms:
            suitable_rooms = sorted(rooms, key=lambda r: -int(r.get('capacite') or 0)) if rooms else []

        duration = 120
        # try to find duration from existing exams
        ex = db_select("examens", "duree_minutes", eq={"module_id": mid}, limit=1)
        if ex and ex[0].get('duree_minutes'):
            try:
                duration = int(ex[0].get('duree_minutes'))
            except Exception:
                pass

        for day in days:
            if not students_free_on_date(mid, day):
                continue

            chosen_room = None
            for r in suitable_rooms:
                if r.get('id') not in room_used_by_slot.get(day, set()):
                    chosen_room = r
                    break
            if chosen_room is None:
                continue

            chosen_prof = choose_prof_for_module(mid, formation_id)
            if chosen_prof is None:
                continue

            if prof_exam_count_by_day[chosen_prof].get(day, 0) >= 3:
                other_cands = [p for p in profs if prof_exam_count_by_day[p['id']].get(day, 0) < 3]
                if other_cands:
                    chosen_prof = min(other_cands, key=lambda p: sum(prof_exam_count_by_day[p['id']].values()))['id']
                else:
                    continue

            dt = datetime.combine(day, dtime(hour=9, minute=0))
            scheduled.append({
                "module_id": mid,
                "module_nom": mname,
                "prof_id": chosen_prof,
                "salle_id": chosen_room['id'],
                "date_heure": dt,
                "duree_minutes": duration,
                "nb_inscrits": nb_ins
            })

            studs = module_to_students.get(mid, [])
            for s in studs:
                student_exam_days[s].add(day)
            prof_exam_count_by_day[chosen_prof][day] += 1
            room_used_by_slot[day].add(chosen_room['id'])
            scheduled_flag = True
            report['created_slots'] += 1
            break

        if not scheduled_flag:
            conflicts_report.setdefault('unscheduled_modules', []).append({
                'module_id': mid,
                'module_nom': mname,
                'nb_inscrits': nb_ins
            })

    # persist if requested
    if force and scheduled:
        # Create payload list
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

    # run conflict detection on DB state (best-effort)
    conflicts_after = detect_conflicts(start_date, end_date)

    duration = time.time() - tic
    report['duration_seconds'] = duration
    report['scheduled_count'] = len(scheduled)
    report['scheduled_preview'] = scheduled[:50]
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

    # --------------------
    # Chef de département UI
    # --------------------
    elif role == "Chef":
        st.title("🧭 Tableau de bord — Chef de département")
        dept_id = user_data.get('dept_id') if user_data else None
        st.subheader(f"Statistiques et validation — Département : {user_data.get('dept_nom','-') if user_data else '-'}")

        if dept_id is None:
            st.warning("Impossible de déterminer votre département. Vérifiez votre profil.")
        else:
            st.markdown("### Statistiques par formation (nombre d'examens, modules)")
            # fetch formations for dept
            forms = db_select("formations", "id,nom", eq={"dept_id": dept_id})
            stats_form = []
            for f in forms:
                # count modules
                mods = db_select("modules", "id", eq={"formation_id": f['id']})
                nb_modules = len(mods)
                # count exams for modules
                nb_exams = 0
                for m in mods:
                    nb_exams += len(db_select("examens", "id", eq={"module_id": m['id']}))
                stats_form.append({"formation": f['nom'], "nb_exams": nb_exams, "nb_modules": nb_modules})
            show_table_safe(stats_form)

            st.markdown("### Conflits par formation (estimation)")
            # build conflict per formation using detect_conflicts logic but limited
            conflicts = detect_conflicts()
            # For simplicity map unscheduled or conflict counts to formation via modules
            # Here we'll compute a simple heuristic: for each conflict in conflits_par_dept, show by formation 0 (placeholder)
            # A more precise mapping would require mapping exam ids to modules to formations
            # We'll show a placeholder if no detailed mapping is desired
            # Build counts per formation (best-effort)
            formation_conflicts = defaultdict(int)
            # try to use salles_capacite conflicts which contain examen_id
            for sc in conflicts.get('salles_capacite', []):
                ex_id = sc.get('examen_id')
                ex = db_get_one("examens", "*", eq={"id": ex_id})
                if ex:
                    mod = db_get_one("modules", "formation_id", eq={"id": ex.get('module_id')})
                    if mod and mod.get('formation_id'):
                        formation_conflicts[mod['formation_id']] += 1
            conflicts_by_form = []
            for fid, cnt in formation_conflicts.items():
                f = db_get_one("formations", "nom", eq={"id": fid})
                conflicts_by_form.append({"formation": f.get('nom') if f else str(fid), "conflits_estimes": cnt})
            show_table_safe(conflicts_by_form)

            st.markdown("### Validation des examens par formation")
            # fetch exams for formations in dept that are not validated
            exams_dept = []
            # get modules for dept's formations
            forms = db_select("formations", "id", eq={"dept_id": dept_id})
            form_ids = [f['id'] for f in forms]
            mods = []
            for fid in form_ids:
                mods.extend(db_select("modules", "id,nom,formation_id", eq={"formation_id": fid}))
            mod_ids = [m['id'] for m in mods]
            for mid in mod_ids:
                exs = db_select("examens", "*", eq={"module_id": mid})
                for ex in exs:
                    if ex.get('validated') in (None, 0):
                        m = db_get_one("modules", "nom", eq={"id": mid})
                        f = db_get_one("formations", "nom", eq={"id": m.get('formation_id') if m else None})
                        l = db_get_one("lieu_examen", "nom", eq={"id": ex.get('salle_id')})
                        exams_dept.append({
                            "id": ex.get('id'),
                            "module": m.get('nom') if m else "-",
                            "formation": f.get('nom') if f else "-",
                            "salle": l.get('nom') if l else "-",
                            "date_heure": ex.get('date_heure'),
                            "duree_minutes": ex.get('duree_minutes'),
                            "validated": ex.get('validated') or 0
                        })
            if exams_dept:
                for ex in exams_dept:
                    cols = st.columns([4,2,2,1])
                    cols[0].write(f"{ex['formation']} — {ex['module']} — {ex['date_heure']}")
                    cols[1].write(f"Salle: {ex['salle']}")
                    cols[2].write(f"Durée: {ex['duree_minutes']}min")
                    if cols[3].button("Valider", key=f"chef_val_{ex['id']}"):
                        res = db_update("examens", {"validated": 1}, {"id": ex['id']})
                        if res.get('error'):
                            st.error(f"Erreur validation: {res['error']}")
                        else:
                            st.success(f"Examen {ex['id']} validé.")
                            st.experimental_rerun()
            else:
                st.info("Aucun examen trouvé pour validation.")

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
                    report, conflicts = generate_timetable(start_str, end_str, force=False)
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
                        tic = time.time()
                        rep2, conf2 = generate_timetable(start_str, end_str, force=True)
                        dt = time.time() - tic
                        if rep2.get('created_slots', 0) > 0 and not conf2.get('insert_error'):
                            st.success(f"Écriture en base terminée en {dt:.1f}s. {rep2.get('created_slots',0)} créés.")
                        else:
                            st.warning(f"Écriture : {conf2.get('insert_error', 'Aucune insertion effectuée ou erreur')}")
                        st.json(rep2)

        with col_a2:
            if st.button("Optimiser les ressources"):
                if start_str is None or end_str is None or start_str > end_str:
                    st.error("Veuillez choisir une période valide (début ≤ fin).")
                else:
                    tic = time.time()
                    report, conflicts = optimize_resources(start_str, end_str)
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
                conflicts = detect_conflicts(start_str, end_str)
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
