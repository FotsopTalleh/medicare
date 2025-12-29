# Medical Dashboard - Split Database Architecture

A HIPAA-compliant medical dashboard application with anonymized data storage.

## Architecture Overview

### Database Structure
1. **Local Database (SQLite)**
   - Stores personal identifiable information only
   - Fields: Full name, phone number, email
   - Contains generated UUID for linking

2. **Cloud Database (Firebase Firestore)**
   - Stores anonymous medical data only
   - Fields: Age, height, medical metrics
   - Linked via UUID only (no personal data)

### Security Features
- No personal data in cloud storage
- UUID-based linking only
- Clear separation of concerns
- Medical disclaimer in UI

## Setup Instructions

### 1. Prerequisites
- Python 3.8+
- Firebase project with Firestore enabled

### 2. Installation
```bash
# Clone the repository
git clone <repository-url>
cd medical-dashboard

# Install Python dependencies
pip install flask firebase-admin

# Set up Firebase
# 1. Create a Firebase project
# 2. Enable Firestore database
# 3. Generate service account key
# 4. Save as firebase_key.json in project root