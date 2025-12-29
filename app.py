"""
Medical Dashboard Backend with Split Database Architecture
Personal data: SQLite (local) - NAMES, PHONE, EMAIL only
Medical data: Firebase Firestore (cloud) - ANONYMOUS medical data only
Linked only by UUID for anonymity
Website shows MEDICAL DATA ONLY from Firebase
"""

import uuid
import sqlite3
import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import firebase_admin
from firebase_admin import credentials, firestore
import os
import logging

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-medical-app')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize SQLite database for PERSONAL DATA ONLY
def init_sqlite():
    conn = sqlite3.connect('db_local.sqlite')
    cursor = conn.cursor()
    
    # Create patients table for PERSONAL IDENTIFIABLE DATA ONLY
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE NOT NULL,           -- For linking ONLY - NOT personal
            full_name TEXT NOT NULL,              -- PERSONAL IDENTIFIABLE (NOT displayed)
            phone TEXT NOT NULL,                  -- PERSONAL IDENTIFIABLE (NOT displayed)
            email TEXT NOT NULL,                  -- PERSONAL IDENTIFIABLE (NOT displayed)
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("SQLite database initialized for personal data only")

init_sqlite()

# Initialize Firebase for ANONYMOUS MEDICAL DATA ONLY
try:
    if os.path.exists('firebase_key.json'):
        cred = credentials.Certificate('firebase_key.json')
        firebase_admin.initialize_app(cred)
        db_firestore = firestore.client()
        logger.info("Firebase initialized successfully for anonymous medical data")
    else:
        logger.warning("Firebase key not found. Running in local-only mode.")
        db_firestore = None
except Exception as e:
    logger.error(f"Firebase initialization failed: {e}")
    db_firestore = None

# Helper functions
def get_sqlite_connection():
    return sqlite3.connect('db_local.sqlite')

def create_uuid():
    """Generate unique UUID for patient linking between databases"""
    return str(uuid.uuid4())

def verify_no_personal_data(data_dict):
    """Verify that no personal data is being sent to Firebase"""
    personal_fields = ['full_name', 'name', 'phone', 'email', 'contact', 'address']
    for field in personal_fields:
        if field in data_dict:
            logger.error(f"SECURITY VIOLATION: Personal field '{field}' attempted in Firebase data")
            return False
    return True

def get_patient_by_uuid(uuid):
    """Get patient personal data from SQLite (for backend use only, not displayed)"""
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM patients WHERE uuid = ?', (uuid,))
    patient_data = cursor.fetchone()
    conn.close()
    
    if patient_data:
        return {
            'id': patient_data[0],
            'uuid': patient_data[1],
            'full_name': patient_data[2],  # PERSONAL - NOT DISPLAYED
            'phone': patient_data[3],      # PERSONAL - NOT DISPLAYED
            'email': patient_data[4],      # PERSONAL - NOT DISPLAYED
            'created_at': patient_data[5]
        }
    return None

# Routes
@app.route('/')
def dashboard():
    """Screen 1: Patient Dashboard - Shows ANONYMOUS medical data only"""
    medical_patients = []
    
    if db_firestore:
        try:
            # Get all medical records from Firebase
            docs = db_firestore.collection('patients_medical').stream()
            
            for doc in docs:
                medical_data = doc.to_dict()
                if medical_data:
                    # Create anonymous patient record (NO PERSONAL DATA)
                    patient = {
                        'uuid': medical_data.get('uuid', 'Unknown'),
                        'age': medical_data.get('age', 'Not specified'),
                        'height': medical_data.get('height', 'Not specified'),
                        'created_at': medical_data.get('created_at', datetime.now()),
                        'risk_score': medical_data.get('risk_metrics', {}).get('current_risk_score', 'N/A'),
                        'data_source': 'Firebase Cloud'
                    }
                    medical_patients.append(patient)
            
            logger.info(f"Dashboard loaded with {len(medical_patients)} anonymous medical records")
            
        except Exception as e:
            logger.error(f"Error loading medical data: {e}")
            flash('Error loading medical data from cloud', 'error')
    else:
        logger.warning("Firebase not available - no medical data to display")
    
    return render_template('dashboard.html', patients=medical_patients)

@app.route('/add-patient', methods=['GET', 'POST'])
def add_patient():
    """Screen 3: Add Patient Form - Split storage implementation"""
    if request.method == 'POST':
        try:
            # Get form data
            full_name = request.form.get('full_name', '').strip()
            phone = request.form.get('phone', '').strip()
            email = request.form.get('email', '').strip()
            age = request.form.get('age', '').strip()
            height = request.form.get('height', '').strip()
            
            # Validate required personal fields
            if not full_name or not phone or not email:
                flash('Please fill in all required personal fields', 'error')
                return redirect(url_for('add_patient'))
            
            # Generate UUID for linking databases
            patient_uuid = create_uuid()
            logger.info(f"Generated UUID for new patient: {patient_uuid}")
            
            # ============================================
            # STORE PERSONAL DATA IN SQLITE (LOCAL) - NOT DISPLAYED
            # ============================================
            conn = get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO patients (uuid, full_name, phone, email)
                VALUES (?, ?, ?, ?)
            ''', (patient_uuid, full_name, phone, email))
            conn.commit()
            conn.close()
            
            logger.info(f"Personal data stored in SQLite (not displayed) for UUID: {patient_uuid}")
            
            # ============================================
            # STORE MEDICAL DATA IN FIREBASE (CLOUD) - DISPLAYED ON WEBSITE
            # ============================================
            if db_firestore:
                try:
                    # Create ANONYMOUS medical data - NO PERSONAL INFO
                    medical_data = {
                        'uuid': patient_uuid,  # ONLY linker, not personal
                        'age': int(age) if age and age.isdigit() else None,
                        'height': float(height) if height else None,
                        'medical_history': {
                            'placeholder': True,
                            'note': 'Medical history will be added here'
                        },
                        'vital_signs': {
                            'last_bp': None,
                            'last_glucose': None,
                            'last_weight': None
                        },
                        'risk_metrics': {
                            'current_risk_score': None,
                            'last_assessment': None,
                            'risk_factors': []
                        },
                        'created_at': datetime.now(),
                        'last_updated': datetime.now(),
                        'data_type': 'anonymous_medical_only',
                        'contains_personal_data': False
                    }
                    
                    # SECURITY CHECK: Verify no personal data in medical_data
                    if not verify_no_personal_data(medical_data):
                        raise ValueError("SECURITY: Personal data detected in medical data")
                    
                    # Store in Firestore - ANONYMOUS DATA ONLY (DISPLAYED ON WEBSITE)
                    db_firestore.collection('patients_medical').document(patient_uuid).set(medical_data)
                    
                    logger.info(f"✓ Medical data stored ANONYMOUSLY in Firebase for UUID: {patient_uuid}")
                    logger.info(f"  - Age: {age if age else 'Not specified'} (Displayed on website)")
                    logger.info(f"  - Height: {height if height else 'Not specified'} (Displayed on website)")
                    logger.info("  - NO personal identifiers in Firebase")
                    
                except Exception as e:
                    logger.error(f"✗ Firebase write error: {e}")
                    logger.warning("  Continuing with local storage only - medical data not saved")
            else:
                logger.warning("Firebase not configured - medical data not saved to cloud")
            
            flash(f'Patient added successfully! Medical data is now available anonymously.', 'success')
            return redirect(url_for('dashboard'))
            
        except Exception as e:
            logger.error(f"Error adding patient: {e}")
            flash('Error adding patient. Please try again.', 'error')
            return redirect(url_for('add_patient'))
    
    return render_template('patient_form.html', patient=None, medical_data=None)

@app.route('/patient/<uuid>')
def patient_detail(uuid):
    """Screen 2: Patient Details & Risk Analysis - Shows MEDICAL DATA ONLY from Firebase"""
    
    # Get ANONYMOUS medical data from Firebase (cloud) - THIS IS DISPLAYED
    medical_data = None
    if db_firestore:
        try:
            doc_ref = db_firestore.collection('patients_medical').document(uuid)
            doc = doc_ref.get()
            if doc.exists:
                medical_data = doc.to_dict()
                logger.info(f"Loaded anonymous medical data from Firebase for UUID: {uuid}")
                
                # Security audit: Verify no personal data in Firebase
                if 'full_name' in medical_data or 'phone' in medical_data or 'email' in medical_data:
                    logger.error(f"SECURITY BREACH: Personal data found in Firebase for UUID: {uuid}")
                    medical_data['security_warning'] = 'Personal data detected in medical records!'
            else:
                logger.warning(f"No medical data found in Firebase for UUID: {uuid}")
                flash('No medical data found for this patient', 'warning')
                return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f"Firebase read error for UUID {uuid}: {e}")
            flash('Error loading medical data', 'error')
            return redirect(url_for('dashboard'))
    else:
        logger.warning("Firebase not available - cannot show medical data")
        flash('Medical data service unavailable', 'error')
        return redirect(url_for('dashboard'))
    
    # NOTE: We do NOT fetch or pass personal data from SQLite to template
    # Only medical data from Firebase is passed
    
    return render_template('patient_detail.html', 
                         medical_data=medical_data,
                         patient_uuid=uuid)  # Only pass UUID for reference

@app.route('/edit/<uuid>', methods=['GET', 'POST'])
def edit_patient(uuid):
    """Edit Patient Form - Update medical data only"""
    if request.method == 'GET':
        # Get MEDICAL data from Firebase (for editing)
        medical_data = None
        if db_firestore:
            try:
                doc_ref = db_firestore.collection('patients_medical').document(uuid)
                doc = doc_ref.get()
                if doc.exists:
                    medical_data = doc.to_dict()
            except Exception as e:
                logger.error(f"Firebase read error during edit: {e}")
                flash('Error loading medical data for editing', 'error')
                return redirect(url_for('dashboard'))
        
        if not medical_data:
            flash('No medical data found for this patient', 'error')
            return redirect(url_for('dashboard'))
        
        # NOTE: We do NOT fetch personal data for the form
        # Only medical data is editable on the website
        
        return render_template('patient_form.html', 
                             patient_uuid=uuid, 
                             medical_data=medical_data)
    
    else:  # POST request - Update medical data only
        # Update MEDICAL data in Firebase
        age = request.form.get('age', '').strip()
        height = request.form.get('height', '').strip()
        
        if db_firestore:
            try:
                # Prepare ANONYMOUS medical data update
                medical_update = {
                    'age': int(age) if age and age.isdigit() else None,
                    'height': float(height) if height else None,
                    'last_updated': datetime.now()
                }
                
                # SECURITY CHECK
                if not verify_no_personal_data(medical_update):
                    raise ValueError("Personal data detected in medical update")
                
                # Update in Firebase
                db_firestore.collection('patients_medical').document(uuid).update(medical_update)
                logger.info(f"Updated anonymous medical data in Firebase for UUID: {uuid}")
                
                flash('Medical data updated successfully!', 'success')
                
            except Exception as e:
                logger.error(f"Firebase update error: {e}")
                flash('Error updating medical data', 'error')
        
        return redirect(url_for('patient_detail', uuid=uuid))

@app.route('/delete/<uuid>', methods=['POST'])
def delete_patient(uuid):
    """Delete patient from both databases (admin function)"""
    try:
        # Delete from SQLite (PERSONAL DATA - backend only)
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM patients WHERE uuid = ?', (uuid,))
        conn.commit()
        conn.close()
        
        logger.info(f"Deleted personal data from SQLite for UUID: {uuid}")
        
        # Delete from Firebase (MEDICAL DATA - website data)
        if db_firestore:
            try:
                db_firestore.collection('patients_medical').document(uuid).delete()
                logger.info(f"Deleted anonymous medical data from Firebase for UUID: {uuid}")
            except Exception as e:
                logger.error(f"Firebase delete error: {e}")
        
        return jsonify({'success': True, 'message': 'Patient data deleted from both databases'})
        
    except Exception as e:
        logger.error(f"Error deleting patient: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/medical-data')
def get_medical_data():
    """API endpoint to get all medical data (anonymous)"""
    if not db_firestore:
        return jsonify({'error': 'Firebase not available'}), 500
    
    try:
        docs = db_firestore.collection('patients_medical').stream()
        medical_records = []
        
        for doc in docs:
            data = doc.to_dict()
            # Ensure no personal data is included
            data.pop('full_name', None)
            data.pop('name', None)
            data.pop('phone', None)
            data.pop('email', None)
            data.pop('contact', None)
            
            medical_records.append(data)
        
        return jsonify({
            'count': len(medical_records),
            'data': medical_records,
            'note': 'Anonymous medical data only - no personal identifiers'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/verify-separation')
def verify_separation():
    """API endpoint to verify data separation"""
    results = {
        'sqlite_personal_count': 0,
        'firebase_medical_count': 0,
        'personal_in_firebase': False,
        'medical_in_sqlite': False,
        'note': 'Website displays medical data from Firebase only'
    }
    
    # Check SQLite
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM patients')
    results['sqlite_personal_count'] = cursor.fetchone()[0]
    conn.close()
    
    # Check Firebase
    if db_firestore:
        docs = list(db_firestore.collection('patients_medical').stream())
        results['firebase_medical_count'] = len(docs)
        
        # Check for personal data in Firebase
        personal_fields = ['full_name', 'name', 'phone', 'email']
        for doc in docs:
            data = doc.to_dict()
            for field in personal_fields:
                if field in data:
                    results['personal_in_firebase'] = True
                    break
    
    return jsonify(results)

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Starting Medical Dashboard Application")
    logger.info("Data Display: MEDICAL DATA FROM FIREBASE ONLY")
    logger.info("Data Storage: Personal→SQLite, Medical→Firebase")
    logger.info("=" * 60)
    app.run(debug=True, port=5000)