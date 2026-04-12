"""
Medical Dashboard Backend with Split Database Architecture
Personal data: SQLite (local) - NAMES, PHONE, EMAIL only
Medical data: Firebase Firestore (cloud) - ANONYMOUS medical data only
Linked only by UUID for anonymity
Website shows MEDICAL DATA ONLY from Firebase

NEW: User accounts, referral messaging, PDF/CSV downloads
"""

import uuid
import sqlite3
import json
import csv
import io
import bcrypt
import threading
import requests as http_requests
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, flash, session, send_file, g)
import firebase_admin
from firebase_admin import credentials, firestore
import os
import logging

# ─── ReportLab (PDF) ────────────────────────────────────────────────────────
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# ─── App Setup ───────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# ─── Logging ─────────────────────────────────────────────────────────────────
# On Railway (and other cloud platforms) write only to stdout;
# locally also write to app.log for convenience.
_log_handlers = [logging.StreamHandler()]
if not os.environ.get('RAILWAY_ENVIRONMENT'):
    _log_handlers.append(logging.FileHandler('app.log'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger(__name__)

# ─── Medical code validation ─────────────────────────────────────────────────
import re
MEDICAL_CODE_PATTERN = re.compile(r'^\d{6}[A-Z]{3}-[A-Z]$')

def validate_medical_code(code: str) -> bool:
    return bool(MEDICAL_CODE_PATTERN.match(code))

# ─── SQLite Initialisation ───────────────────────────────────────────────────
def init_sqlite():
    """Create / migrate all SQLite tables."""
    conn = sqlite3.connect(SQLITE_PATH)
    cursor = conn.cursor()

    # Patients table (personal + medical code)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid         TEXT UNIQUE NOT NULL,
            medical_code TEXT UNIQUE NOT NULL,
            full_name    TEXT NOT NULL,
            phone        TEXT NOT NULL,
            email        TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Migration: add medical_code column if upgrading from old schema
    cursor.execute("PRAGMA table_info(patients)")
    existing_cols = [row[1] for row in cursor.fetchall()]
    if 'medical_code' not in existing_cols:
        try:
            cursor.execute("ALTER TABLE patients ADD COLUMN medical_code TEXT")
            logger.info("Migration: added medical_code column to patients table")
        except Exception as e:
            logger.warning(f"Migration note: {e}")
    if 'email' in existing_cols:
        pass  # email already exists

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'Nurse',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Referral messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referral_messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id    INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            patient_uuid TEXT NOT NULL,
            note         TEXT,
            is_read      INTEGER DEFAULT 0,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sender_id)    REFERENCES users(id),
            FOREIGN KEY (recipient_id) REFERENCES users(id)
        )
    """)
    # ── Partograph: case sessions ──────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS partograph_cases (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_uuid          TEXT NOT NULL,
            admission_date        TEXT NOT NULL,
            admission_time        TEXT NOT NULL,
            gravida               INTEGER,
            para                  TEXT,
            hospital_number       TEXT,
            membranes_ruptured    TEXT DEFAULT 'intact',
            membrane_rupture_time TEXT,
            status                TEXT DEFAULT 'in_progress',
            created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by            INTEGER,
            FOREIGN KEY (patient_uuid) REFERENCES patients(uuid)
        )
    """)
    # ── Migrate existing partograph_cases tables that predate these columns ──
    existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(partograph_cases)").fetchall()}
    migrations = [
        ("gravida",               "ALTER TABLE partograph_cases ADD COLUMN gravida INTEGER"),
        ("para",                  "ALTER TABLE partograph_cases ADD COLUMN para TEXT"),
        ("hospital_number",       "ALTER TABLE partograph_cases ADD COLUMN hospital_number TEXT"),
        ("membranes_ruptured",    "ALTER TABLE partograph_cases ADD COLUMN membranes_ruptured TEXT DEFAULT 'intact'"),
        ("membrane_rupture_time", "ALTER TABLE partograph_cases ADD COLUMN membrane_rupture_time TEXT"),
    ]
    for col, sql in migrations:
        if col not in existing_cols:
            cursor.execute(sql)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fhr_entries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id    INTEGER NOT NULL,
            time       TEXT NOT NULL,
            fhr_value  INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cervix_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         INTEGER NOT NULL,
            time            TEXT NOT NULL,
            dilatation_cm   REAL NOT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS descent_entries (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id        INTEGER NOT NULL,
            time           TEXT NOT NULL,
            descent_value  REAL NOT NULL,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS moulding_entries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id    INTEGER NOT NULL,
            time       TEXT NOT NULL,
            grade      TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contraction_entries (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id          INTEGER NOT NULL,
            time             TEXT NOT NULL,
            frequency        INTEGER NOT NULL,
            intensity        TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS amniotic_fluid_entries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id    INTEGER NOT NULL,
            time       TEXT NOT NULL,
            status     TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vital_sign_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id      INTEGER NOT NULL,
            time         TEXT NOT NULL,
            systolic_bp  INTEGER,
            diastolic_bp INTEGER,
            pulse_bpm    INTEGER,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS temperature_entries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id    INTEGER NOT NULL,
            time       TEXT NOT NULL,
            celsius    REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medication_entries (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id          INTEGER NOT NULL,
            time             TEXT NOT NULL,
            medication_type  TEXT NOT NULL,
            medication_name  TEXT,
            dose             TEXT NOT NULL,
            route            TEXT NOT NULL,
            notes            TEXT,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS urine_entries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id    INTEGER NOT NULL,
            time       TEXT NOT NULL,
            protein    TEXT NOT NULL,
            acetone    TEXT NOT NULL,
            volume_ml  INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES partograph_cases(id)
        )
    """)

    conn.commit()
    conn.close()
    logger.info("SQLite database initialised / migrated OK")


# ─── Firebase ────────────────────────────────────────────────────────────────
try:
    _fb_key_json = os.environ.get('FIREBASE_KEY_JSON')
    if _fb_key_json:
        _fb_dict = json.loads(_fb_key_json)
        cred = credentials.Certificate(_fb_dict)
        firebase_admin.initialize_app(cred)
        db_firestore = firestore.client()
        logger.info("Firebase initialised from FIREBASE_KEY_JSON env var")
    elif os.path.exists('firebase_key.json'):
        cred = credentials.Certificate('firebase_key.json')
        firebase_admin.initialize_app(cred)
        db_firestore = firestore.client()
        logger.info("Firebase initialised from local firebase_key.json")
    else:
        logger.warning("No Firebase credentials found - running in local-only mode.")
        db_firestore = None
except Exception as e:
    logger.error(f"Firebase initialisation failed: {e}")
    db_firestore = None

# ─── Helpers ─────────────────────────────────────────────────────────────────
SQLITE_PATH = os.environ.get('SQLITE_PATH', 'db_local.sqlite')
# Auto-create directory for SQLite (needed when using Render persistent disk)
_sqlite_dir = os.path.dirname(os.path.abspath(SQLITE_PATH))
if _sqlite_dir and not os.path.exists(_sqlite_dir):
    try:
        os.makedirs(_sqlite_dir, exist_ok=True)
        logger.info(f'Created SQLite directory: {_sqlite_dir}')
    except Exception as _e:
        logger.warning(f'Cannot create SQLite dir {_sqlite_dir}: {_e} — falling back to local file')
        SQLITE_PATH = 'db_local.sqlite'

# Initialise tables on startup
init_sqlite()

def get_sqlite_connection():
    return sqlite3.connect(SQLITE_PATH)

def create_uuid():
    return str(uuid.uuid4())

# ─── Risk-Score API Integration ──────────────────────────────────────────────
RISK_API_URL = os.environ.get('RISK_API_URL', '').rstrip('/')

def _call_risk_api(patient_uuid: str) -> None:
    """
    Background thread: call the hosted pregnancy-risk-model service to score a
    newly registered patient.  Writes result back to risk_metrics in Firestore.
    Runs in a daemon thread so it never blocks the HTTP response.
    """
    if not RISK_API_URL:
        logger.warning("RISK_API_URL not set — skipping automatic risk scoring")
        return
    try:
        url = f"{RISK_API_URL}/api/firestore/score-patient/{patient_uuid}"
        resp = http_requests.post(url, timeout=30)
        if resp.ok:
            data = resp.json()
            logger.info(
                "Auto-scored patient %s → %.1f (%s)",
                patient_uuid[:8], data.get('current_risk_score', 0), data.get('risk_level', '?')
            )
        else:
            logger.warning("Risk API returned %s for patient %s", resp.status_code, patient_uuid[:8])
    except Exception as exc:
        logger.error("Risk API call failed for patient %s: %s", patient_uuid[:8], exc)

def trigger_risk_score(patient_uuid: str) -> None:
    """Spawn a daemon thread to score the patient without blocking the request."""
    t = threading.Thread(target=_call_risk_api, args=(patient_uuid,), daemon=True)
    t.start()

def verify_no_personal_data(data_dict):
    personal_fields = ['full_name', 'name', 'phone', 'email', 'contact', 'address']
    for field in personal_fields:
        if field in data_dict:
            logger.error(f"SECURITY VIOLATION: Personal field '{field}' attempted in Firebase data")
            return False
    return True

def get_current_user():
    """Return the logged-in user dict or None."""
    user_id = session.get('user_id')
    if not user_id:
        return None
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, email, role, created_at FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {'id': row[0], 'name': row[1], 'email': row[2], 'role': row[3], 'created_at': row[4]}
    return None

def get_unread_count(user_id):
    """Count unread referral messages for a user."""
    if not user_id:
        return 0
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM referral_messages WHERE recipient_id = ? AND is_read = 0', (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

# ─── Auth Decorator ──────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ─── Context Processor ───────────────────────────────────────────────────────
@app.context_processor
def inject_user():
    """Inject current user and unread count into every template."""
    try:
        user = get_current_user()
        unread = get_unread_count(user['id'] if user else None)
    except Exception as e:
        import logging as _l
        _l.getLogger(__name__).error(f'Context processor error: {e}')
        user, unread = None, 0
    return dict(current_user=user, unread_count=unread)

# ─── Auth Routes ─────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Please enter both email and password.', 'error')
            return render_template('login.html')

        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id, name, password_hash, role FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        conn.close()

        if user and bcrypt.checkpw(password.encode('utf-8'), user[2].encode('utf-8')):
            session['user_id'] = user[0]
            session['user_name'] = user[1]
            session['user_role'] = user[3]
            logger.info(f"User logged in: {email}")
            flash(f'Welcome back, {user[1]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password. Please try again.', 'error')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))

    roles = ['Doctor', 'Midwife', 'Nurse', 'Specialist', 'Healthcare Worker']

    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        role     = request.form.get('role', 'Nurse')
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')

        # Validation
        if not name or not email or not password:
            flash('Please fill in all required fields.', 'error')
            return render_template('register.html', roles=roles)

        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('register.html', roles=roles)

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('register.html', roles=roles)

        # Hash password
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        try:
            conn = get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)',
                (name, email, password_hash, role)
            )
            conn.commit()
            user_id = cursor.lastrowid
            conn.close()

            session['user_id'] = user_id
            session['user_name'] = name
            session['user_role'] = role
            logger.info(f"New user registered: {email} ({role})")
            flash(f'Account created! Welcome to SaveTheMommy, {name}!', 'success')
            return redirect(url_for('dashboard'))

        except sqlite3.IntegrityError:
            flash('An account with this email already exists.', 'error')
            return render_template('register.html', roles=roles)

    return render_template('register.html', roles=roles)


@app.route('/logout')
def logout():
    name = session.get('user_name', 'User')
    session.clear()
    flash(f'Goodbye, {name}! You have been logged out.', 'info')
    return redirect(url_for('login'))

# ─── Dashboard ───────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    """Patient Dashboard — anonymous medical data from Firebase, codes from SQLite."""
    medical_patients = []

    if db_firestore:
        try:
            docs = db_firestore.collection('patients_medical').stream()
            # Build a uuid→medical_code map from SQLite
            conn = get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT uuid, medical_code FROM patients')
            code_map = {row[0]: row[1] for row in cursor.fetchall()}
            conn.close()

            for doc in docs:
                md = doc.to_dict()
                if md:
                    uuid = md.get('uuid', '')
                    patient = {
                        'uuid':         uuid,
                        'medical_code': code_map.get(uuid, '—'),
                        'age':          md.get('age', 'Not specified'),
                        'height':       md.get('height', 'Not specified'),
                        'created_at':   md.get('created_at', datetime.now()),
                        'risk_score':   md.get('risk_metrics', {}).get('current_risk_score', 'N/A'),
                        'data_source':  'Firebase Cloud'
                    }
                    medical_patients.append(patient)
            logger.info(f"Dashboard loaded with {len(medical_patients)} records")
        except Exception as e:
            logger.error(f"Error loading medical data: {e}")
            flash('Error loading medical data from cloud', 'error')
    else:
        logger.warning("Firebase not available — no medical data to display")

    return render_template('dashboard.html', patients=medical_patients)

# ─── Add / Edit / Delete Patient ─────────────────────────────────────────────
@app.route('/add-patient', methods=['GET', 'POST'])
@login_required
def add_patient():
    if request.method == 'POST':
        try:
            medical_code = request.form.get('medical_code', '').strip().upper()
            full_name    = request.form.get('full_name', '').strip()
            phone        = request.form.get('phone', '').strip()
            email        = request.form.get('email', '').strip() or None

            # ── Clinical parameters
            age                     = request.form.get('age', '').strip()
            height                  = request.form.get('height', '').strip()
            gestational_weeks       = request.form.get('gestational_weeks', '').strip()
            fetus_count             = request.form.get('fetus_count', '').strip()
            given_birth_before      = request.form.get('given_birth_before', '')
            previous_cesarean       = request.form.get('previous_cesarean', '')
            maternal_medical_cond   = request.form.get('maternal_medical_condition', '')
            placenta_previa         = request.form.get('placenta_previa', '')
            fetal_distress          = request.form.get('fetal_distress', '')
            prev_uterine_surgery    = request.form.get('previous_uterine_surgery', '')
            fetal_abnormalities     = request.form.get('fetal_abnormalities', '')
            fetal_presentation      = request.form.getlist('fetal_presentation')  # checkboxes

            def yn(val): return True if val == 'yes' else (False if val == 'no' else None)

            # ── Validate medical code
            if not medical_code:
                flash('Medical code is required.', 'error')
                return render_template('patient_form.html', **request.form)
            if not validate_medical_code(medical_code):
                flash('Invalid medical code format. Required: 6 digits + 3 uppercase letters + hyphen + 1 uppercase letter (e.g. 123456ABC-D).', 'error')
                return render_template('patient_form.html', **request.form)

            if not full_name or not phone:
                flash('Full name and phone are required.', 'error')
                return render_template('patient_form.html', **request.form)

            patient_uuid = create_uuid()
            logger.info(f"Registering patient code {medical_code} → UUID {patient_uuid}")

            # — Store personal + code data locally in SQLite
            try:
                conn = get_sqlite_connection()
                cursor = conn.cursor()
                cursor.execute(
                    'INSERT INTO patients (uuid, medical_code, full_name, phone, email) VALUES (?, ?, ?, ?, ?)',
                    (patient_uuid, medical_code, full_name, phone, email)
                )
                conn.commit()
                conn.close()
                logger.info(f"Personal data stored in SQLite: code={medical_code}, uuid={patient_uuid}")
            except sqlite3.IntegrityError:
                flash(f'Medical code "{medical_code}" is already registered. Please use a different code.', 'error')
                return render_template('patient_form.html', **request.form)

            # — Store anonymous clinical data in Firebase (NO code, NO personal data)
            if db_firestore:
                try:
                    medical_data = {
                        'uuid':                       patient_uuid,
                        'age':                        int(age) if age and age.isdigit() else None,
                        'height':                     float(height) if height else None,
                        'gestational_weeks':          int(gestational_weeks) if gestational_weeks and gestational_weeks.isdigit() else None,
                        'fetus_count':                int(fetus_count) if fetus_count and fetus_count.isdigit() else 1,
                        'given_birth_before':         yn(given_birth_before),
                        'previous_cesarean':          yn(previous_cesarean),
                        'maternal_medical_condition': yn(maternal_medical_cond),
                        'placenta_previa':            yn(placenta_previa),
                        'fetal_distress':             yn(fetal_distress),
                        'previous_uterine_surgery':   yn(prev_uterine_surgery),
                        'fetal_abnormalities':        yn(fetal_abnormalities),
                        'fetal_presentation':         fetal_presentation if fetal_presentation else [],
                        'risk_metrics':               {'current_risk_score': None, 'last_assessment': None, 'risk_factors': []},
                        'created_at':                 datetime.now(),
                        'last_updated':               datetime.now(),
                        'data_type':                  'anonymous_medical_only',
                        'contains_personal_data':     False,
                        'registered_by':              session.get('user_name', 'Unknown'),
                        'registered_by_role':         session.get('user_role', 'Unknown')
                    }
                    if not verify_no_personal_data(medical_data):
                        raise ValueError("SECURITY: Personal data detected in medical data")
                    db_firestore.collection('patients_medical').document(patient_uuid).set(medical_data)
                    logger.info(f"Clinical data stored anonymously in Firebase for UUID: {patient_uuid}")
                    # ── Auto-score: call risk model API in background ──────────
                    trigger_risk_score(patient_uuid)
                except Exception as e:
                    logger.error(f"Firebase write error: {e}")

            flash(f'Patient {medical_code} registered successfully!', 'success')
            return redirect(url_for('dashboard'))

        except Exception as e:
            logger.error(f"Error adding patient: {e}")
            flash('Error adding patient. Please try again.', 'error')
            return redirect(url_for('add_patient'))

    return render_template('patient_form.html')


@app.route('/patient/<uuid>')
@login_required
def patient_detail(uuid):
    medical_data = None
    if db_firestore:
        try:
            doc_ref = db_firestore.collection('patients_medical').document(uuid)
            doc = doc_ref.get()
            if doc.exists:
                medical_data = doc.to_dict()
            else:
                flash('No medical data found for this patient', 'warning')
                return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f"Firebase read error for UUID {uuid}: {e}")
            flash('Error loading medical data', 'error')
            return redirect(url_for('dashboard'))
    else:
        flash('Medical data service unavailable', 'error')
        return redirect(url_for('dashboard'))

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    # Get this patient's medical_code — fall back to Firebase field if not in SQLite
    cursor.execute('SELECT medical_code FROM patients WHERE uuid = ?', (uuid,))
    row = cursor.fetchone()
    if row:
        medical_code = row[0]
    elif medical_data and medical_data.get('medical_code'):
        medical_code = medical_data['medical_code']
    else:
        medical_code = uuid[:8].upper()  # last-resort: show first 8 chars of UUID

    # Get all other users for referral dropdown
    cursor.execute('SELECT id, name, role FROM users WHERE id != ?', (session.get('user_id'),))
    all_users = [{'id': r[0], 'name': r[1], 'role': r[2]} for r in cursor.fetchall()]

    # Load active partograph case for inline panel
    cursor.execute(
        'SELECT * FROM partograph_cases WHERE patient_uuid = ? ORDER BY created_at DESC LIMIT 1',
        (uuid,)
    )
    pc_row = cursor.fetchone()
    active_case = None
    if pc_row:
        cols = [d[0] for d in cursor.description]
        active_case = dict(zip(cols, pc_row))

    conn.close()

    return render_template('patient_detail.html',
                           medical_data=medical_data,
                           patient_uuid=uuid,
                           medical_code=medical_code,
                           all_users=all_users,
                           active_case=active_case)


@app.route('/edit/<uuid>', methods=['GET', 'POST'])
@login_required
def edit_patient(uuid):
    if request.method == 'GET':
        medical_data = None
        if db_firestore:
            try:
                doc = db_firestore.collection('patients_medical').document(uuid).get()
                if doc.exists:
                    medical_data = doc.to_dict()
            except Exception as e:
                logger.error(f"Firebase read error during edit: {e}")
                flash('Error loading medical data for editing', 'error')
                return redirect(url_for('dashboard'))

        if not medical_data:
            flash('No medical data found for this patient', 'error')
            return redirect(url_for('dashboard'))

        # Fetch the locked medical_code and personal fields for display
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT medical_code, full_name, phone, email FROM patients WHERE uuid = ?', (uuid,))
        row = cursor.fetchone()
        conn.close()
        if row:
            kwargs = dict(patient_uuid=uuid, medical_data=medical_data,
                          medical_code=row[0], full_name=row[1], phone=row[2], email=row[3] or '')
        else:
            kwargs = dict(patient_uuid=uuid, medical_data=medical_data)
        return render_template('patient_form.html', **kwargs)

    else:
        def yn(val): return True if val == 'yes' else (False if val == 'no' else None)

        age                   = request.form.get('age', '').strip()
        height                = request.form.get('height', '').strip()
        gestational_weeks     = request.form.get('gestational_weeks', '').strip()
        fetus_count           = request.form.get('fetus_count', '').strip()
        given_birth_before    = request.form.get('given_birth_before', '')
        previous_cesarean     = request.form.get('previous_cesarean', '')
        maternal_medical_cond = request.form.get('maternal_medical_condition', '')
        placenta_previa       = request.form.get('placenta_previa', '')
        fetal_distress        = request.form.get('fetal_distress', '')
        prev_uterine_surgery  = request.form.get('previous_uterine_surgery', '')
        fetal_abnormalities   = request.form.get('fetal_abnormalities', '')
        fetal_presentation    = request.form.getlist('fetal_presentation')

        if db_firestore:
            try:
                medical_update = {
                    'age':                        int(age) if age and age.isdigit() else None,
                    'height':                     float(height) if height else None,
                    'gestational_weeks':          int(gestational_weeks) if gestational_weeks and gestational_weeks.isdigit() else None,
                    'fetus_count':                int(fetus_count) if fetus_count and fetus_count.isdigit() else None,
                    'given_birth_before':         yn(given_birth_before),
                    'previous_cesarean':          yn(previous_cesarean),
                    'maternal_medical_condition': yn(maternal_medical_cond),
                    'placenta_previa':            yn(placenta_previa),
                    'fetal_distress':             yn(fetal_distress),
                    'previous_uterine_surgery':   yn(prev_uterine_surgery),
                    'fetal_abnormalities':        yn(fetal_abnormalities),
                    'fetal_presentation':         fetal_presentation if fetal_presentation else [],
                    'last_updated':               datetime.now()
                }
                if not verify_no_personal_data(medical_update):
                    raise ValueError("Personal data detected in medical update")
                db_firestore.collection('patients_medical').document(uuid).update(medical_update)
                flash('Clinical data updated successfully!', 'success')
            except Exception as e:
                logger.error(f"Firebase update error: {e}")
                flash('Error updating clinical data', 'error')

        return redirect(url_for('patient_detail', uuid=uuid))


@app.route('/delete/<uuid>', methods=['POST'])
@login_required
def delete_patient(uuid):
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM patients WHERE uuid = ?', (uuid,))
        conn.commit()
        conn.close()

        if db_firestore:
            db_firestore.collection('patients_medical').document(uuid).delete()

        return jsonify({'success': True, 'message': 'Patient deleted from both databases'})
    except Exception as e:
        logger.error(f"Error deleting patient: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ─── Referral / Messaging ─────────────────────────────────────────────────────
@app.route('/send-referral', methods=['POST'])
@login_required
def send_referral():
    """Send a referral message to another user."""
    sender_id    = session.get('user_id')
    recipient_id = request.form.get('recipient_id', type=int)
    patient_uuid = request.form.get('patient_uuid', '').strip()
    note         = request.form.get('note', '').strip()

    if not recipient_id or not patient_uuid:
        flash('Missing referral information.', 'error')
        return redirect(url_for('patient_detail', uuid=patient_uuid))

    if recipient_id == sender_id:
        flash('You cannot refer a patient to yourself.', 'error')
        return redirect(url_for('patient_detail', uuid=patient_uuid))

    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO referral_messages (sender_id, recipient_id, patient_uuid, note)
               VALUES (?, ?, ?, ?)''',
            (sender_id, recipient_id, patient_uuid, note)
        )
        conn.commit()
        conn.close()

        logger.info(f"Referral sent: patient {patient_uuid[:8]}... from user {sender_id} to user {recipient_id}")
        flash('Referral sent successfully!', 'success')
    except Exception as e:
        logger.error(f"Error sending referral: {e}")
        flash('Error sending referral. Please try again.', 'error')

    return redirect(url_for('patient_detail', uuid=patient_uuid))


@app.route('/messages')
@login_required
def messages():
    """Referral inbox/chat page."""
    user_id = session.get('user_id')

    conn = get_sqlite_connection()
    cursor = conn.cursor()

    # Received messages
    cursor.execute('''
        SELECT rm.id, rm.patient_uuid, rm.note, rm.is_read, rm.created_at,
               u.name AS sender_name, u.role AS sender_role
        FROM referral_messages rm
        JOIN users u ON rm.sender_id = u.id
        WHERE rm.recipient_id = ?
        ORDER BY rm.created_at DESC
    ''', (user_id,))
    received_rows = cursor.fetchall()
    received = [
        {
            'id': r[0], 'patient_uuid': r[1], 'note': r[2],
            'is_read': r[3], 'created_at': r[4],
            'sender_name': r[5], 'sender_role': r[6]
        }
        for r in received_rows
    ]

    # Sent messages
    cursor.execute('''
        SELECT rm.id, rm.patient_uuid, rm.note, rm.is_read, rm.created_at,
               u.name AS recipient_name, u.role AS recipient_role
        FROM referral_messages rm
        JOIN users u ON rm.recipient_id = u.id
        WHERE rm.sender_id = ?
        ORDER BY rm.created_at DESC
    ''', (user_id,))
    sent_rows = cursor.fetchall()
    sent = [
        {
            'id': r[0], 'patient_uuid': r[1], 'note': r[2],
            'is_read': r[3], 'created_at': r[4],
            'recipient_name': r[5], 'recipient_role': r[6]
        }
        for r in sent_rows
    ]

    # Mark all received as read
    cursor.execute(
        'UPDATE referral_messages SET is_read = 1 WHERE recipient_id = ? AND is_read = 0',
        (user_id,)
    )
    conn.commit()
    conn.close()

    return render_template('messages.html', received=received, sent=sent)


@app.route('/messages/<int:msg_id>')
@login_required
def message_detail(msg_id):
    """Return JSON detail of a single referral message (for modal / inline view)."""
    user_id = session.get('user_id')
    conn = get_sqlite_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT rm.id, rm.patient_uuid, rm.note, rm.is_read, rm.created_at,
               s.name, s.role, r.name, r.role
        FROM referral_messages rm
        JOIN users s ON rm.sender_id = s.id
        JOIN users r ON rm.recipient_id = r.id
        WHERE rm.id = ? AND (rm.sender_id = ? OR rm.recipient_id = ?)
    ''', (msg_id, user_id, user_id))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'Message not found'}), 404

    # Fetch anonymous medical data from Firebase for the patient
    medical_summary = {}
    if db_firestore:
        try:
            doc = db_firestore.collection('patients_medical').document(row[1]).get()
            if doc.exists:
                d = doc.to_dict()
                medical_summary = {
                    'age': d.get('age'),
                    'height': d.get('height'),
                    'risk_score': d.get('risk_metrics', {}).get('current_risk_score'),
                    'risk_factors': d.get('risk_metrics', {}).get('risk_factors', []),
                    'last_bp': d.get('vital_signs', {}).get('last_bp'),
                    'last_glucose': d.get('vital_signs', {}).get('last_glucose'),
                    'registered_by': d.get('registered_by'),
                    'registered_by_role': d.get('registered_by_role'),
                }
        except Exception as e:
            logger.error(f"Firebase read error in message_detail: {e}")

    return jsonify({
        'id': row[0],
        'patient_uuid': row[1],
        'note': row[2],
        'is_read': row[3],
        'created_at': row[4],
        'sender_name': row[5],
        'sender_role': row[6],
        'recipient_name': row[7],
        'recipient_role': row[8],
        'medical_summary': medical_summary
    })


@app.route('/download-referral/<int:msg_id>/<format>')
@login_required
def download_referral(msg_id, format):
    """Download a referral as PDF or CSV."""
    user_id = session.get('user_id')
    conn = get_sqlite_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT rm.patient_uuid, rm.note, rm.created_at,
               s.name, s.role, r.name, r.role
        FROM referral_messages rm
        JOIN users s ON rm.sender_id = s.id
        JOIN users r ON rm.recipient_id = r.id
        WHERE rm.id = ? AND (rm.sender_id = ? OR rm.recipient_id = ?)
    ''', (msg_id, user_id, user_id))
    row = cursor.fetchone()
    conn.close()

    if not row:
        flash('Referral not found.', 'error')
        return redirect(url_for('messages'))

    patient_uuid, note, created_at, sender_name, sender_role, recipient_name, recipient_role = row

    # Get anonymous medical data
    medical_summary = {}
    if db_firestore:
        try:
            doc = db_firestore.collection('patients_medical').document(patient_uuid).get()
            if doc.exists:
                d = doc.to_dict()
                medical_summary = {
                    'Age': d.get('age', 'N/A'),
                    'Height (cm)': d.get('height', 'N/A'),
                    'Risk Score': d.get('risk_metrics', {}).get('current_risk_score', 'N/A'),
                    'Risk Factors': ', '.join(d.get('risk_metrics', {}).get('risk_factors', [])) or 'None',
                    'Last BP': d.get('vital_signs', {}).get('last_bp', 'N/A'),
                    'Last Glucose': d.get('vital_signs', {}).get('last_glucose', 'N/A'),
                    'Registered By': d.get('registered_by', 'N/A'),
                }
        except Exception as e:
            logger.error(f"Firebase error in download_referral: {e}")

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    patient_short = patient_uuid[:8]

    # ── CSV ──────────────────────────────────────────────────────────────────
    if format == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['SaveTheMommy — Patient Referral Report'])
        writer.writerow(['Generated', datetime.now().strftime('%Y-%m-%d %H:%M')])
        writer.writerow([])
        writer.writerow(['Referral Information'])
        writer.writerow(['From', f"{sender_name} ({sender_role})"])
        writer.writerow(['To', f"{recipient_name} ({recipient_role})"])
        writer.writerow(['Date', created_at])
        writer.writerow(['Patient ID (Anonymous)', patient_uuid])
        writer.writerow(['Note', note or 'No additional note'])
        writer.writerow([])
        writer.writerow(['Anonymous Medical Summary'])
        for key, value in medical_summary.items():
            writer.writerow([key, value])
        writer.writerow([])
        writer.writerow(['DISCLAIMER: This report contains anonymous medical data only. No personal identifiers are included.'])

        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'referral_{patient_short}_{timestamp}.csv'
        )

    # ── PDF ──────────────────────────────────────────────────────────────────
    elif format == 'pdf':
        if not REPORTLAB_AVAILABLE:
            flash('PDF generation is not available. Please install reportlab.', 'error')
            return redirect(url_for('messages'))

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter,
                                rightMargin=inch, leftMargin=inch,
                                topMargin=inch, bottomMargin=inch)
        styles = getSampleStyleSheet()

        # Custom styles
        title_style = ParagraphStyle('Title2', parent=styles['Title'],
                                     textColor=colors.HexColor('#2C3E50'), fontSize=20)
        header_style = ParagraphStyle('Header2', parent=styles['Heading2'],
                                      textColor=colors.HexColor('#00ffcc'))
        normal_style = styles['Normal']

        story = []

        # Header
        story.append(Paragraph('SaveTheMommy', title_style))
        story.append(Paragraph('Patient Referral Report', styles['Heading1']))
        story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", normal_style))
        story.append(Spacer(1, 0.3 * inch))

        # Privacy notice
        story.append(Paragraph('⚠️ PRIVACY NOTICE', header_style))
        story.append(Paragraph(
            'This report contains anonymous medical data only. No personal identifiers '
            '(name, phone, email) are included. Patient identity is protected by UUID-based anonymisation.',
            normal_style
        ))
        story.append(Spacer(1, 0.3 * inch))

        # Referral info
        story.append(Paragraph('Referral Information', header_style))
        ref_data = [
            ['From:', f"{sender_name} ({sender_role})"],
            ['To:', f"{recipient_name} ({recipient_role})"],
            ['Date:', str(created_at)],
            ['Patient ID:', patient_uuid],
            ['Note:', note or 'No additional note'],
        ]
        ref_table = Table(ref_data, colWidths=[1.5 * inch, 5 * inch])
        ref_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f0f0')),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(ref_table)
        story.append(Spacer(1, 0.3 * inch))

        # Medical summary
        story.append(Paragraph('Anonymous Medical Summary', header_style))
        if medical_summary:
            med_data = [['Parameter', 'Value']] + [[k, str(v)] for k, v in medical_summary.items()]
            med_table = Table(med_data, colWidths=[2.5 * inch, 4 * inch])
            med_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2C3E50')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (1, 1), (-1, -1), [colors.white, colors.HexColor('#f0fff8')]),
                ('PADDING', (0, 0), (-1, -1), 8),
            ]))
            story.append(med_table)
        else:
            story.append(Paragraph('Medical data unavailable (Firebase offline).', normal_style))

        story.append(Spacer(1, 0.5 * inch))
        story.append(Paragraph(
            'This document is for healthcare use only. Always consult a licensed medical professional.',
            ParagraphStyle('Disclaimer', parent=normal_style, textColor=colors.grey, fontSize=8)
        ))

        doc.build(story)
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'referral_{patient_short}_{timestamp}.pdf'
        )

    flash('Invalid download format.', 'error')
    return redirect(url_for('messages'))

# ─── API ──────────────────────────────────────────────────────────────────────
@app.route('/api/medical-data')
@login_required
def get_medical_data():
    if not db_firestore:
        return jsonify({'error': 'Firebase not available'}), 500
    try:
        docs = db_firestore.collection('patients_medical').stream()
        medical_records = []
        for doc in docs:
            data = doc.to_dict()
            for field in ['full_name', 'name', 'phone', 'email', 'contact']:
                data.pop(field, None)
            medical_records.append(data)
        return jsonify({'count': len(medical_records), 'data': medical_records,
                        'note': 'Anonymous medical data only'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/verify-separation')
@login_required
def verify_separation():
    results = {
        'sqlite_personal_count': 0,
        'firebase_medical_count': 0,
        'personal_in_firebase': False,
        'note': 'Website displays medical data from Firebase only'
    }
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM patients')
    results['sqlite_personal_count'] = cursor.fetchone()[0]
    conn.close()

    if db_firestore:
        docs = list(db_firestore.collection('patients_medical').stream())
        results['firebase_medical_count'] = len(docs)
        for doc in docs:
            data = doc.to_dict()
            for field in ['full_name', 'name', 'phone', 'email']:
                if field in data:
                    results['personal_in_firebase'] = True
                    break

    return jsonify(results)


@app.route('/api/unread-count')
@login_required
def api_unread_count():
    count = get_unread_count(session.get('user_id'))
    return jsonify({'unread_count': count})


@app.route('/health')
def health():
    """Health check endpoint — returns JSON status for debugging."""
    status = {'status': 'ok', 'firebase': db_firestore is not None, 'sqlite': False}
    try:
        conn = get_sqlite_connection()
        conn.execute("SELECT 1")
        conn.close()
        status['sqlite'] = True
    except Exception as e:
        status['sqlite_error'] = str(e)
    from flask import jsonify
    return jsonify(status)


# ═══════════════════════════════════════════════════════════════════════════════
# ─── PARTOGRAPH MODULE ────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def _case_belongs_to_user(case_id, user_id):
    """Return case row if it exists (auth check placeholder — extend for RBAC)."""
    conn = get_sqlite_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM partograph_cases WHERE id = ?', (case_id,))
    row = c.fetchone()
    conn.close()
    return row

def _rows_to_dicts(cursor, rows):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in rows]

def _hours_elapsed(admission_date, admission_time, event_time):
    """Return decimal hours between admission datetime and HH:MM event_time (same day)."""
    try:
        from datetime import datetime as dt
        adm = dt.strptime(f"{admission_date} {admission_time}", "%Y-%m-%d %H:%M")
        evt = dt.strptime(f"{admission_date} {event_time}", "%Y-%m-%d %H:%M")
        diff = (evt - adm).total_seconds() / 3600
        return round(max(diff, 0), 2)
    except Exception:
        return 0

# ── Page: open or create partograph for a patient ─────────────────────────────
@app.route('/patient/<uuid>/partograph', methods=['GET', 'POST'])
@login_required
def partograph_page(uuid):
    conn = get_sqlite_connection()
    cur = conn.cursor()

    # Resolve medical_code — SQLite first, then Firebase, then UUID prefix
    cur.execute('SELECT medical_code FROM patients WHERE uuid = ?', (uuid,))
    row = cur.fetchone()
    if row:
        medical_code = row[0]
    else:
        # Try Firebase
        medical_code = None
        if db_firestore:
            try:
                doc = db_firestore.collection('patients_medical').document(uuid).get()
                if doc.exists:
                    medical_code = doc.to_dict().get('medical_code')
            except Exception:
                pass
        if not medical_code:
            medical_code = uuid[:8].upper()

    if request.method == 'POST':
        admission_date  = request.form.get('admission_date', '').strip()
        admission_time  = request.form.get('admission_time', '').strip()
        if not admission_date or not admission_time:
            flash('Admission date and time are required.', 'error')
            conn.close()
            return redirect(url_for('partograph_page', uuid=uuid))
        gravida       = request.form.get('gravida') or None
        para          = request.form.get('para', '').strip() or None
        hosp_num      = request.form.get('hospital_number', '').strip() or None
        membranes     = request.form.get('membranes_ruptured', 'intact')
        rupture_time  = request.form.get('membrane_rupture_time', '').strip() or None
        cur.execute(
            '''INSERT INTO partograph_cases
               (patient_uuid, admission_date, admission_time,
                gravida, para, hospital_number,
                membranes_ruptured, membrane_rupture_time, created_by)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            (uuid, admission_date, admission_time,
             gravida, para, hosp_num,
             membranes, rupture_time, session.get('user_id'))
        )
        conn.commit()
        case_id = cur.lastrowid
        conn.close()
        return redirect(url_for('partograph_page', uuid=uuid) + f'?case_id={case_id}')


    # Load existing cases
    cur.execute('SELECT * FROM partograph_cases WHERE patient_uuid = ? ORDER BY created_at DESC', (uuid,))
    cases = _rows_to_dicts(cur, cur.fetchall())

    # Active case
    case_id = request.args.get('case_id', type=int)
    active_case = None
    if case_id:
        cur.execute('SELECT * FROM partograph_cases WHERE id = ? AND patient_uuid = ?', (case_id, uuid))
        r = cur.fetchone()
        if r:
            active_case = dict(zip([d[0] for d in cur.description], r))
    elif cases:
        active_case = cases[0]

    conn.close()
    return render_template('partograph.html',
                           patient_uuid=uuid,
                           medical_code=medical_code,
                           cases=cases,
                           active_case=active_case)


# ── Helper: get/create active case ────────────────────────────────────────────
def _get_case_or_404(case_id):
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM partograph_cases WHERE id = ?', (case_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None, None, None
    case = dict(zip([d[0] for d in cur.description], row))
    conn.close()
    return case, get_sqlite_connection(), case


# ─── Generic CRUD factory ─────────────────────────────────────────────────────
def _parto_list(case_id, table, order_col='time'):
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM {table} WHERE case_id = ? ORDER BY {order_col}', (case_id,))
    rows = _rows_to_dicts(cur, cur.fetchall())
    conn.close()
    return jsonify({'success': True, 'entries': rows})

def _parto_delete(case_id, table, entry_id):
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute(f'DELETE FROM {table} WHERE id = ? AND case_id = ?', (entry_id, case_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ─── FHR ─────────────────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/fhr', methods=['GET', 'POST'])
@login_required
def api_fhr(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'fhr_entries')
    data = request.get_json(force=True)
    t, val = data.get('time','').strip(), data.get('fhr_value')
    if not t or val is None:
        return jsonify({'success': False, 'error': 'time and fhr_value required'}), 400
    val = int(val)
    if not (80 <= val <= 200):
        return jsonify({'success': False, 'error': 'FHR must be 80–200 bpm'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO fhr_entries (case_id, time, fhr_value) VALUES (?,?,?)', (case_id, t, val))
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return jsonify({'success': True, 'id': entry_id, 'time': t, 'fhr_value': val}), 201

@app.route('/api/partograph/<int:case_id>/fhr/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_fhr_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'fhr_entries', entry_id)
    data = request.get_json(force=True)
    t, val = data.get('time','').strip(), int(data.get('fhr_value', 0))
    conn = get_sqlite_connection()
    conn.execute('UPDATE fhr_entries SET time=?, fhr_value=? WHERE id=? AND case_id=?', (t, val, entry_id, case_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Cervix ───────────────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/cervix', methods=['GET', 'POST'])
@login_required
def api_cervix(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'cervix_entries')
    data = request.get_json(force=True)
    t, val = data.get('time','').strip(), data.get('dilatation_cm')
    if not t or val is None:
        return jsonify({'success': False, 'error': 'time and dilatation_cm required'}), 400
    val = float(val)
    if not (0 <= val <= 10):
        return jsonify({'success': False, 'error': 'Dilatation must be 0–10 cm'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO cervix_entries (case_id, time, dilatation_cm) VALUES (?,?,?)', (case_id, t, val))
    conn.commit(); entry_id = cur.lastrowid; conn.close()
    return jsonify({'success': True, 'id': entry_id, 'time': t, 'dilatation_cm': val}), 201

@app.route('/api/partograph/<int:case_id>/cervix/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_cervix_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'cervix_entries', entry_id)
    data = request.get_json(force=True)
    t, val = data.get('time','').strip(), float(data.get('dilatation_cm', 0))
    conn = get_sqlite_connection()
    conn.execute('UPDATE cervix_entries SET time=?, dilatation_cm=? WHERE id=? AND case_id=?', (t, val, entry_id, case_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Descent ──────────────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/descent', methods=['GET', 'POST'])
@login_required
def api_descent(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'descent_entries')
    data = request.get_json(force=True)
    t, val = data.get('time','').strip(), data.get('descent_value')
    if not t or val is None:
        return jsonify({'success': False, 'error': 'time and descent_value required'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO descent_entries (case_id, time, descent_value) VALUES (?,?,?)', (case_id, t, float(val)))
    conn.commit(); entry_id = cur.lastrowid; conn.close()
    return jsonify({'success': True, 'id': entry_id, 'time': t, 'descent_value': float(val)}), 201

@app.route('/api/partograph/<int:case_id>/descent/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_descent_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'descent_entries', entry_id)
    data = request.get_json(force=True)
    t, val = data.get('time','').strip(), float(data.get('descent_value', 0))
    conn = get_sqlite_connection()
    conn.execute('UPDATE descent_entries SET time=?, descent_value=? WHERE id=? AND case_id=?', (t, val, entry_id, case_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Moulding ─────────────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/moulding', methods=['GET', 'POST'])
@login_required
def api_moulding(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'moulding_entries')
    data = request.get_json(force=True)
    t, grade = data.get('time','').strip(), data.get('grade','').strip()
    if not t or grade not in ('0', '+', '++', '+++'):
        return jsonify({'success': False, 'error': 'Valid grade required (0, +, ++, +++)'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO moulding_entries (case_id, time, grade) VALUES (?,?,?)', (case_id, t, grade))
    conn.commit(); entry_id = cur.lastrowid; conn.close()
    return jsonify({'success': True, 'id': entry_id, 'time': t, 'grade': grade}), 201

@app.route('/api/partograph/<int:case_id>/moulding/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_moulding_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'moulding_entries', entry_id)
    data = request.get_json(force=True)
    t, grade = data.get('time','').strip(), data.get('grade','').strip()
    conn = get_sqlite_connection()
    conn.execute('UPDATE moulding_entries SET time=?, grade=? WHERE id=? AND case_id=?', (t, grade, entry_id, case_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Contractions ─────────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/contractions', methods=['GET', 'POST'])
@login_required
def api_contractions(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'contraction_entries')
    data = request.get_json(force=True)
    t = data.get('time','').strip()
    freq = data.get('frequency')
    intensity = data.get('intensity','').strip()
    dur = data.get('duration_seconds')
    if not t or freq is None or not intensity or dur is None:
        return jsonify({'success': False, 'error': 'All fields required'}), 400
    freq, dur = int(freq), int(dur)
    if not (0 <= freq <= 5) or not (20 <= dur <= 90):
        return jsonify({'success': False, 'error': 'Frequency 0–5, duration 20–90s'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO contraction_entries (case_id, time, frequency, intensity, duration_seconds) VALUES (?,?,?,?,?)',
                (case_id, t, freq, intensity, dur))
    conn.commit(); entry_id = cur.lastrowid; conn.close()
    return jsonify({'success': True, 'id': entry_id}), 201

@app.route('/api/partograph/<int:case_id>/contractions/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_contractions_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'contraction_entries', entry_id)
    data = request.get_json(force=True)
    conn = get_sqlite_connection()
    conn.execute('UPDATE contraction_entries SET time=?, frequency=?, intensity=?, duration_seconds=? WHERE id=? AND case_id=?',
                 (data.get('time'), data.get('frequency'), data.get('intensity'), data.get('duration_seconds'), entry_id, case_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Amniotic Fluid ───────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/amniotic-fluid', methods=['GET', 'POST'])
@login_required
def api_amniotic_fluid(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'amniotic_fluid_entries')
    data = request.get_json(force=True)
    t, status = data.get('time','').strip(), data.get('status','').strip()
    valid = ('intact', 'clear', 'green', 'yellow', 'ruptured')
    if not t or status not in valid:
        return jsonify({'success': False, 'error': f'Status must be one of {valid}'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO amniotic_fluid_entries (case_id, time, status) VALUES (?,?,?)', (case_id, t, status))
    conn.commit(); entry_id = cur.lastrowid; conn.close()
    return jsonify({'success': True, 'id': entry_id, 'time': t, 'status': status}), 201

@app.route('/api/partograph/<int:case_id>/amniotic-fluid/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_amniotic_fluid_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'amniotic_fluid_entries', entry_id)
    data = request.get_json(force=True)
    conn = get_sqlite_connection()
    conn.execute('UPDATE amniotic_fluid_entries SET time=?, status=? WHERE id=? AND case_id=?',
                 (data.get('time'), data.get('status'), entry_id, case_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Vitals (BP + Pulse) ──────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/vitals', methods=['GET', 'POST'])
@login_required
def api_vitals(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'vital_sign_entries')
    data = request.get_json(force=True)
    t = data.get('time','').strip()
    sys_bp = data.get('systolic_bp')
    dia_bp = data.get('diastolic_bp')
    pulse  = data.get('pulse_bpm')
    if not t:
        return jsonify({'success': False, 'error': 'time required'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO vital_sign_entries (case_id, time, systolic_bp, diastolic_bp, pulse_bpm) VALUES (?,?,?,?,?)',
                (case_id, t,
                 int(sys_bp) if sys_bp is not None else None,
                 int(dia_bp) if dia_bp is not None else None,
                 int(pulse)  if pulse  is not None else None))
    conn.commit(); entry_id = cur.lastrowid; conn.close()
    return jsonify({'success': True, 'id': entry_id}), 201

@app.route('/api/partograph/<int:case_id>/vitals/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_vitals_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'vital_sign_entries', entry_id)
    data = request.get_json(force=True)
    conn = get_sqlite_connection()
    conn.execute('UPDATE vital_sign_entries SET time=?, systolic_bp=?, diastolic_bp=?, pulse_bpm=? WHERE id=? AND case_id=?',
                 (data.get('time'), data.get('systolic_bp'), data.get('diastolic_bp'), data.get('pulse_bpm'), entry_id, case_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Temperature ──────────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/temperature', methods=['GET', 'POST'])
@login_required
def api_temperature(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'temperature_entries')
    data = request.get_json(force=True)
    t, val = data.get('time','').strip(), data.get('celsius')
    if not t or val is None:
        return jsonify({'success': False, 'error': 'time and celsius required'}), 400
    val = float(val)
    if not (34 <= val <= 41):
        return jsonify({'success': False, 'error': 'Temperature 34–41°C'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO temperature_entries (case_id, time, celsius) VALUES (?,?,?)', (case_id, t, val))
    conn.commit(); entry_id = cur.lastrowid; conn.close()
    return jsonify({'success': True, 'id': entry_id, 'time': t, 'celsius': val}), 201

@app.route('/api/partograph/<int:case_id>/temperature/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_temperature_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'temperature_entries', entry_id)
    data = request.get_json(force=True)
    conn = get_sqlite_connection()
    conn.execute('UPDATE temperature_entries SET time=?, celsius=? WHERE id=? AND case_id=?',
                 (data.get('time'), data.get('celsius'), entry_id, case_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Medications ──────────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/medications', methods=['GET', 'POST'])
@login_required
def api_medications(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'medication_entries')
    data = request.get_json(force=True)
    t    = data.get('time','').strip()
    mtype = data.get('medication_type','').strip()
    dose  = data.get('dose','').strip()
    route = data.get('route','').strip()
    if not all([t, mtype, dose, route]):
        return jsonify({'success': False, 'error': 'time, type, dose and route required'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO medication_entries (case_id, time, medication_type, medication_name, dose, route, notes) VALUES (?,?,?,?,?,?,?)',
        (case_id, t, mtype, data.get('medication_name',''), dose, route, data.get('notes',''))
    )
    conn.commit(); entry_id = cur.lastrowid; conn.close()
    return jsonify({'success': True, 'id': entry_id}), 201

@app.route('/api/partograph/<int:case_id>/medications/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_medications_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'medication_entries', entry_id)
    data = request.get_json(force=True)
    conn = get_sqlite_connection()
    conn.execute(
        'UPDATE medication_entries SET time=?, medication_type=?, medication_name=?, dose=?, route=?, notes=? WHERE id=? AND case_id=?',
        (data.get('time'), data.get('medication_type'), data.get('medication_name'),
         data.get('dose'), data.get('route'), data.get('notes'), entry_id, case_id)
    )
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Urine ────────────────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/urine', methods=['GET', 'POST'])
@login_required
def api_urine(case_id):
    if request.method == 'GET':
        return _parto_list(case_id, 'urine_entries')
    data = request.get_json(force=True)
    t       = data.get('time','').strip()
    protein = data.get('protein','').strip()
    acetone = data.get('acetone','').strip()
    vol     = data.get('volume_ml')
    if not all([t, protein, acetone]):
        return jsonify({'success': False, 'error': 'time, protein and acetone required'}), 400
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO urine_entries (case_id, time, protein, acetone, volume_ml) VALUES (?,?,?,?,?)',
                (case_id, t, protein, acetone, int(vol) if vol is not None else None))
    conn.commit(); entry_id = cur.lastrowid; conn.close()
    return jsonify({'success': True, 'id': entry_id}), 201

@app.route('/api/partograph/<int:case_id>/urine/<int:entry_id>', methods=['PUT', 'DELETE'])
@login_required
def api_urine_entry(case_id, entry_id):
    if request.method == 'DELETE':
        return _parto_delete(case_id, 'urine_entries', entry_id)
    data = request.get_json(force=True)
    conn = get_sqlite_connection()
    conn.execute('UPDATE urine_entries SET time=?, protein=?, acetone=?, volume_ml=? WHERE id=? AND case_id=?',
                 (data.get('time'), data.get('protein'), data.get('acetone'), data.get('volume_ml'), entry_id, case_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ─── Alerts helper ────────────────────────────────────────────────────────────
def _build_alerts(case_id, case, cur):
    """Return a list of {level, message} dicts for the given case."""
    alerts = []
    # FHR
    cur.execute('SELECT fhr_value FROM fhr_entries WHERE case_id=? ORDER BY time DESC LIMIT 1', (case_id,))
    r = cur.fetchone()
    if r:
        if r[0] > 160: alerts.append({'level':'warning', 'message': f'Fetal tachycardia: {r[0]} bpm (>160)'})
        elif r[0] < 120: alerts.append({'level':'danger',  'message': f'Fetal bradycardia: {r[0]} bpm (<120)'})
    # Vitals
    cur.execute('SELECT systolic_bp, diastolic_bp, pulse_bpm FROM vital_sign_entries WHERE case_id=? ORDER BY time DESC LIMIT 1', (case_id,))
    r = cur.fetchone()
    if r:
        sys_bp, dia_bp, pulse = r
        if sys_bp and sys_bp >= 160: alerts.append({'level':'danger',  'message': f'Severe hypertension: BP {sys_bp}/{dia_bp}'})
        elif sys_bp and sys_bp >= 140: alerts.append({'level':'warning', 'message': f'Hypertension: BP {sys_bp}/{dia_bp}'})
        if pulse and pulse > 100: alerts.append({'level':'warning', 'message': f'Maternal tachycardia: {pulse} bpm'})
    # Amniotic fluid
    cur.execute("SELECT status FROM amniotic_fluid_entries WHERE case_id=? ORDER BY time DESC LIMIT 1", (case_id,))
    r = cur.fetchone()
    if r and r[0] in ('green', 'yellow'):
        alerts.append({'level':'warning', 'message': 'Meconium staining detected — increase fetal monitoring'})
    return alerts

# ─── Summary + Alerts ─────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/summary')
@login_required
def api_partograph_summary(case_id):
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM partograph_cases WHERE id = ?', (case_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Case not found'}), 404
    case = dict(zip([d[0] for d in cur.description], row))

    from datetime import datetime as dt
    adm_dt = dt.strptime(f"{case['admission_date']} {case['admission_time']}", "%Y-%m-%d %H:%M")
    time_in_labor_h = round((dt.now() - adm_dt).total_seconds() / 3600, 2)

    def latest(table, col):
        cur.execute(f'SELECT {col} FROM {table} WHERE case_id = ? ORDER BY time DESC LIMIT 1', (case_id,))
        r = cur.fetchone()
        return r[0] if r else None

    latest_fhr   = latest('fhr_entries', 'fhr_value')
    latest_cervix = latest('cervix_entries', 'dilatation_cm')
    latest_pulse = latest('vital_sign_entries', 'pulse_bpm')
    latest_sys   = latest('vital_sign_entries', 'systolic_bp')
    latest_dia   = latest('vital_sign_entries', 'diastolic_bp')
    latest_temp  = latest('temperature_entries', 'celsius')

    # Cervical change rate
    cur.execute('SELECT dilatation_cm, time FROM cervix_entries WHERE case_id = ? ORDER BY time', (case_id,))
    cx_rows = cur.fetchall()
    rate = None
    if len(cx_rows) >= 2:
        try:
            t1 = dt.strptime(f"{case['admission_date']} {cx_rows[-2][1]}", "%Y-%m-%d %H:%M")
            t2 = dt.strptime(f"{case['admission_date']} {cx_rows[-1][1]}", "%Y-%m-%d %H:%M")
            diff_h = (t2 - t1).total_seconds() / 3600
            if diff_h > 0:
                rate = round((cx_rows[-1][0] - cx_rows[-2][0]) / diff_h, 2)
        except Exception:
            pass

    # Status badge
    badge = 'normal'
    if latest_cervix is not None:
        hrs = _hours_elapsed(case['admission_date'], case['admission_time'],
                             cx_rows[-1][1] if cx_rows else case['admission_time'])
        alert_line = 4 + 0.5 * hrs
        action_line = alert_line + 1
        if latest_cervix >= action_line:
            badge = 'action'
        elif latest_cervix >= alert_line:
            badge = 'alert'

    conn.close()
    return jsonify({
        'success': True,
        'time_in_labor_hours': time_in_labor_h,
        'latest_fhr': latest_fhr,
        'latest_cervical_dilatation': latest_cervix,
        'cervical_change_rate': rate,
        'latest_systolic_bp': latest_sys,
        'latest_diastolic_bp': latest_dia,
        'latest_pulse': latest_pulse,
        'latest_temperature': latest_temp,
        'status_badge': badge,
        # ── extra fields used by inline patient-detail panel ──
        'status':         case['status'],
        'duration_hours': time_in_labor_h,
        'fhr_count':      cur.execute('SELECT COUNT(*) FROM fhr_entries WHERE case_id=?',(case_id,)).fetchone()[0],
        'cervix_count':   cur.execute('SELECT COUNT(*) FROM cervix_entries WHERE case_id=?',(case_id,)).fetchone()[0],
        'vitals_count':   cur.execute('SELECT COUNT(*) FROM vital_sign_entries WHERE case_id=?',(case_id,)).fetchone()[0],
        'alerts':         _build_alerts(case_id, case, cur),
    })


@app.route('/api/partograph/<int:case_id>/alerts')
@login_required
def api_partograph_alerts(case_id):
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM partograph_cases WHERE id = ?', (case_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Case not found'}), 404
    case = dict(zip([d[0] for d in cur.description], row))

    alerts = {'critical': [], 'warning': [], 'info': []}

    def add(level, msg):
        alerts[level].append({'message': msg})

    # FHR
    cur.execute('SELECT fhr_value FROM fhr_entries WHERE case_id = ? ORDER BY time DESC LIMIT 1', (case_id,))
    r = cur.fetchone()
    if r:
        if r[0] > 160: add('warning', f'⚠️ Fetal tachycardia: {r[0]} bpm (>160)')
        elif r[0] < 120: add('warning', f'⚠️ Fetal bradycardia: {r[0]} bpm (<120)')

    # Amniotic fluid
    cur.execute("SELECT status FROM amniotic_fluid_entries WHERE case_id = ? ORDER BY time DESC LIMIT 1", (case_id,))
    r = cur.fetchone()
    if r and r[0] in ('green', 'yellow'):
        add('warning', '⚠️ Meconium staining detected. Increase fetal monitoring.')

    # Vitals
    cur.execute('SELECT systolic_bp, diastolic_bp, pulse_bpm FROM vital_sign_entries WHERE case_id = ? ORDER BY time DESC LIMIT 1', (case_id,))
    r = cur.fetchone()
    if r:
        sys_bp, dia_bp, pulse = r
        if sys_bp and dia_bp:
            if sys_bp > 160 or dia_bp > 110:
                add('critical', f'🔴 SEVERE HYPERTENSION {sys_bp}/{dia_bp}. Risk of eclampsia.')
            elif sys_bp < 90 or dia_bp < 60:
                add('warning', f'⚠️ Hypotension {sys_bp}/{dia_bp}. Check for bleeding.')
        if pulse and pulse > 110:
            add('warning', f'⚠️ Maternal tachycardia: {pulse} bpm.')

    # Temperature
    cur.execute('SELECT celsius FROM temperature_entries WHERE case_id = ? ORDER BY time DESC LIMIT 1', (case_id,))
    r = cur.fetchone()
    if r and r[0] > 38.0:
        add('warning', f'⚠️ FEVER: {r[0]}°C. Assess for chorioamnionitis.')

    # Contractions
    cur.execute('SELECT frequency, intensity FROM contraction_entries WHERE case_id = ? ORDER BY time DESC LIMIT 1', (case_id,))
    r = cur.fetchone()
    if r:
        if r[0] < 2: add('warning', '⚠️ Inadequate contractions (<2/10 min). Consider oxytocin.')

    # Urine
    cur.execute("SELECT protein FROM urine_entries WHERE case_id = ? ORDER BY time DESC LIMIT 1", (case_id,))
    r = cur.fetchone()
    if r and r[0] in ('++', '+++'):
        add('warning', f'⚠️ Significant proteinuria ({r[0]}). Monitor for preeclampsia.')

    # Moulding + cervix
    cur.execute("SELECT grade FROM moulding_entries WHERE case_id = ? ORDER BY time DESC LIMIT 1", (case_id,))
    mr = cur.fetchone()
    cur.execute('SELECT dilatation_cm, time FROM cervix_entries WHERE case_id = ? ORDER BY time', (case_id,))
    cx = cur.fetchall()
    if mr and mr[0] == '+++' and len(cx) >= 2:
        add('critical', '🔴 SEVERE MOULDING + SLOW PROGRESS. High risk of CPD.')

    if not alerts['critical'] and not alerts['warning']:
        add('info', '✅ No active alerts. Labor appears to be progressing normally.')

    conn.close()
    return jsonify({'success': True, 'alerts': alerts})


# ─── CSV Export ───────────────────────────────────────────────────────────────
@app.route('/api/partograph/<int:case_id>/export/csv')
@login_required
def api_partograph_export_csv(case_id):
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM partograph_cases WHERE id = ?', (case_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    case = dict(zip([d[0] for d in cur.description], row))

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['SaveTheMommy — Partograph Export'])
    w.writerow(['Patient UUID', case['patient_uuid']])
    w.writerow(['Admission Date', case['admission_date']])
    w.writerow(['Admission Time', case['admission_time']])
    w.writerow([])

    for table, label, cols in [
        ('fhr_entries', 'FHR', ['time','fhr_value']),
        ('cervix_entries', 'Cervical Dilatation', ['time','dilatation_cm']),
        ('descent_entries', 'Head Descent', ['time','descent_value']),
        ('moulding_entries', 'Moulding', ['time','grade']),
        ('contraction_entries', 'Contractions', ['time','frequency','intensity','duration_seconds']),
        ('amniotic_fluid_entries', 'Amniotic Fluid', ['time','status']),
        ('vital_sign_entries', 'Vital Signs', ['time','systolic_bp','diastolic_bp','pulse_bpm']),
        ('temperature_entries', 'Temperature', ['time','celsius']),
        ('medication_entries', 'Medications', ['time','medication_type','medication_name','dose','route']),
        ('urine_entries', 'Urine', ['time','protein','acetone','volume_ml']),
    ]:
        cur.execute(f'SELECT {",".join(cols)} FROM {table} WHERE case_id = ? ORDER BY time', (case_id,))
        rows = cur.fetchall()
        w.writerow([label])
        w.writerow(cols)
        for r in rows:
            w.writerow(r)
        w.writerow([])

    conn.close()
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'partograph_{case_id}_{case["admission_date"]}.csv'
    )



if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Starting SaveTheMommy MediCare - MVP 1")
    logger.info("Auth: User accounts enabled")
    logger.info("Referral: In-platform messaging enabled")
    logger.info("=" * 60)
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=port)