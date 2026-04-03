import json, os

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Firebase init block
old_fb = (
    "try:\n"
    "    if os.path.exists('firebase_key.json'):\n"
    "        cred = credentials.Certificate('firebase_key.json')\n"
    "        firebase_admin.initialize_app(cred)\n"
    "        db_firestore = firestore.client()\n"
    '        logger.info("Firebase initialised successfully")\n'
    "    else:\n"
    '        logger.warning("Firebase key not found. Running in local-only mode.")\n'
    "        db_firestore = None\n"
    "except Exception as e:\n"
    '    logger.error(f"Firebase initialisation failed: {e}")\n'
    "    db_firestore = None"
)
new_fb = (
    "try:\n"
    "    _fb_key_json = os.environ.get('FIREBASE_KEY_JSON')\n"
    "    if _fb_key_json:\n"
    '        _fb_dict = json.loads(_fb_key_json)\n'
    "        cred = credentials.Certificate(_fb_dict)\n"
    "        firebase_admin.initialize_app(cred)\n"
    "        db_firestore = firestore.client()\n"
    '        logger.info("Firebase initialised from FIREBASE_KEY_JSON env var")\n'
    "    elif os.path.exists('firebase_key.json'):\n"
    "        cred = credentials.Certificate('firebase_key.json')\n"
    "        firebase_admin.initialize_app(cred)\n"
    "        db_firestore = firestore.client()\n"
    '        logger.info("Firebase initialised from local firebase_key.json")\n'
    "    else:\n"
    '        logger.warning("No Firebase credentials found - running in local-only mode.")\n'
    "        db_firestore = None\n"
    "except Exception as e:\n"
    '    logger.error(f"Firebase initialisation failed: {e}")\n'
    "    db_firestore = None"
)

if old_fb in content:
    content = content.replace(old_fb, new_fb, 1)
    print("Firebase block replaced OK")
else:
    print("WARNING: Firebase block NOT found")

# 2. SQLite connection helper
old_sq = "def get_sqlite_connection():\n    return sqlite3.connect('db_local.sqlite')"
new_sq = (
    "SQLITE_PATH = os.environ.get('SQLITE_PATH', 'db_local.sqlite')\n\n"
    "def get_sqlite_connection():\n"
    "    return sqlite3.connect(SQLITE_PATH)"
)
if old_sq in content:
    content = content.replace(old_sq, new_sq, 1)
    print("SQLite path replaced OK")
else:
    print("WARNING: SQLite block NOT found")

# 3. app.run line
old_run = "    app.run(debug=True, port=5000)"
new_run = (
    "    port  = int(os.environ.get('PORT', 5000))\n"
    "    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'\n"
    "    app.run(debug=debug, host='0.0.0.0', port=port)"
)
if old_run in content:
    content = content.replace(old_run, new_run, 1)
    print("app.run replaced OK")
else:
    print("WARNING: app.run NOT found")

# 4. Startup log
content = content.replace(
    'Starting Medical Dashboard Application',
    'Starting SaveTheMommy MediCare - MVP 1', 1
)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("File written.")
print("FIREBASE_KEY_JSON present:", 'FIREBASE_KEY_JSON' in content)
print("SQLITE_PATH present:", 'SQLITE_PATH' in content)
print("PORT env var present:", "os.environ.get('PORT'" in content)
