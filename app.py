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
# On Render (and other cloud platforms) write only to stdout;
# locally also write to app.log for convenience.
_log_handlers = [logging.StreamHandler()]
if not os.environ.get('RENDER') and not os.environ.get('DYNO'):
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
    conn = sqlite3.connect(SQLITE_PATH if 'SQLITE_PATH' in dir() else 'db_local.sqlite')
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

def get_sqlite_connection():
    return sqlite3.connect(SQLITE_PATH)

def create_uuid():
    return str(uuid.uuid4())

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
    # Get this patient's medical_code
    cursor.execute('SELECT medical_code FROM patients WHERE uuid = ?', (uuid,))
    row = cursor.fetchone()
    medical_code = row[0] if row else '—'
    # Get all other users for referral dropdown
    cursor.execute('SELECT id, name, role FROM users WHERE id != ?', (session.get('user_id'),))
    all_users = [{'id': r[0], 'name': r[1], 'role': r[2]} for r in cursor.fetchall()]
    conn.close()

    return render_template('patient_detail.html',
                           medical_data=medical_data,
                           patient_uuid=uuid,
                           medical_code=medical_code,
                           all_users=all_users)


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


# ─── Run ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Starting SaveTheMommy MediCare - MVP 1")
    logger.info("Auth: User accounts enabled")
    logger.info("Referral: In-platform messaging enabled")
    logger.info("=" * 60)
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=port)