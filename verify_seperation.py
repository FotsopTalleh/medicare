#!/usr/bin/env python3
"""
Verification script to ensure proper data separation
Checks that no personal data exists in Firebase
"""

import sqlite3
import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

def verify_data_separation():
    print("=" * 60)
    print("DATA SEPARATION VERIFICATION")
    print("=" * 60)
    
    # Check SQLite (should have personal data only)
    print("\n1. Checking LOCAL SQLite database...")
    conn = sqlite3.connect('db_local.sqlite')
    cursor = conn.cursor()
    
    # Get all patients
    cursor.execute('SELECT uuid, full_name, phone, email FROM patients')
    patients = cursor.fetchall()
    
    print(f"   Found {len(patients)} patients in SQLite (personal data only)")
    
    if patients:
        print("   Sample patient data (personal info only):")
        for i, (uuid, name, phone, email) in enumerate(patients[:3]):  # Show first 3
            print(f"   Patient {i+1}:")
            print(f"     UUID: {uuid}")
            print(f"     Name: {name}")
            print(f"     Phone: {phone}")
            print(f"     Email: {email}")
            print()
    
    # Check for medical data in SQLite (should be none)
    cursor.execute("PRAGMA table_info(patients)")
    columns = [col[1] for col in cursor.fetchall()]
    medical_columns = ['age', 'height', 'medical', 'risk', 'blood', 'glucose', 'weight', 'bmi']
    
    medical_fields_found = []
    for col in columns:
        if any(med in col.lower() for med in medical_columns):
            medical_fields_found.append(col)
    
    if medical_fields_found:
        print(f"   ⚠️  WARNING: Medical fields found in SQLite: {medical_fields_found}")
    else:
        print("   ✓ No medical fields in SQLite (CORRECT)")
    
    conn.close()
    
    # Check Firebase (should have anonymous medical data only)
    print("\n2. Checking CLOUD Firebase database...")
    
    if os.path.exists('firebase_key.json'):
        try:
            cred = credentials.Certificate('firebase_key.json')
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            
            # Get all medical documents
            docs = list(db.collection('patients_medical').stream())
            
            print(f"   Found {len(docs)} medical records in Firebase")
            
            personal_data_found = False
            personal_fields_detected = []
            
            print("   Checking for personal data in Firebase...")
            for doc in docs:
                data = doc.to_dict()
                
                # Check for personal identifiers in Firebase
                personal_fields = ['full_name', 'name', 'phone', 'email', 'contact', 'address']
                for field in personal_fields:
                    if field in data:
                        personal_data_found = True
                        personal_fields_detected.append(field)
                        print(f"   ⚠️  SECURITY BREACH: Personal field '{field}' found in Firebase!")
                
                # Check that UUID exists
                if 'uuid' not in data:
                    print(f"   ⚠️  WARNING: Document missing UUID!")
                else:
                    # Verify UUID format (should be 36 chars for UUID4)
                    if len(data['uuid']) != 36:
                        print(f"   ⚠️  WARNING: UUID format suspicious: {data['uuid']}")
                
                # Check for anonymous medical data
                medical_fields = ['age', 'height', 'medical_history', 'vital_signs', 'risk_metrics']
                medical_present = [field for field in medical_fields if field in data]
                
                if not medical_present:
                    print(f"   ⚠️  WARNING: Document has no medical data: {doc.id}")
            
            if not personal_data_found:
                print("   ✓ No personal data found in Firebase (CORRECT)")
            else:
                print(f"   ✗ PERSONAL DATA FOUND IN FIREBASE - {len(set(personal_fields_detected))} field types detected!")
            
            # Check linkage between databases
            print("\n3. Checking database linkage...")
            if patients and len(docs) > 0:
                # Get UUIDs from both databases
                sqlite_uuids = [p[0] for p in patients]
                
                firebase_uuids = []
                for doc in docs:
                    data = doc.to_dict()
                    if 'uuid' in data:
                        firebase_uuids.append(data['uuid'])
                
                # Check for orphans
                sqlite_only = set(sqlite_uuids) - set(firebase_uuids)
                firebase_only = set(firebase_uuids) - set(sqlite_uuids)
                
                if sqlite_only:
                    print(f"   ⚠️  {len(sqlite_only)} patients in SQLite without Firebase medical records")
                    for uuid in list(sqlite_only)[:3]:
                        print(f"      - {uuid}")
                    if len(sqlite_only) > 3:
                        print(f"      ... and {len(sqlite_only) - 3} more")
                
                if firebase_only:
                    print(f"   ⚠️  {len(firebase_only)} Firebase medical records without SQLite patients")
                    for uuid in list(firebase_only)[:3]:
                        print(f"      - {uuid}")
                    if len(firebase_only) > 3:
                        print(f"      ... and {len(firebase_only) - 3} more")
                
                linked_count = len(set(sqlite_uuids) & set(firebase_uuids))
                print(f"   ✓ {linked_count} patients properly linked between databases")
                
                if not sqlite_only and not firebase_only:
                    print("   ✓ All patients properly linked between databases")
                else:
                    print("   ⚠️  Some linkage issues found")
            
        except Exception as e:
            print(f"   ✗ Firebase error: {e}")
            print("   Please check firebase_key.json configuration")
    else:
        print("   ⚠️  Firebase key not found - skipping cloud verification")
        print("   Running in local-only mode")
    
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    summary = {
        "sqlite_patients": len(patients),
        "firebase_medical_records": len(docs) if 'docs' in locals() else 0,
        "medical_in_sqlite": len(medical_fields_found) > 0,
        "personal_in_firebase": personal_data_found if 'personal_data_found' in locals() else False,
        "linkage_issues": (len(sqlite_only) > 0 or len(firebase_only) > 0) if 'sqlite_only' in locals() else False
    }
    
    print(json.dumps(summary, indent=2))
    
    print("\nSECURITY RECOMMENDATIONS:")
    print("1. ✅ Regular audits: Run this verification script weekly")
    print("2. ✅ Access logs: Monitor who accesses each database")
    print("3. 🔒 Encryption: Consider encrypting personal data in SQLite")
    print("4. 💾 Backups: Regular encrypted backups of SQLite database")
    print("5. 👥 Access control: Limit who can access the local server")
    print("6. 🚨 Alerts: Set up alerts for security violations")
    
    return summary

if __name__ == '__main__':
    verify_data_separation()