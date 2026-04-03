"""
Production-safe migration patch for app.py.
Fixes:
1. Makes init_sqlite() migration-safe (adds missing columns if table already exists)
2. Adds /health route for diagnostics
3. Wraps inject_user context processor in try/except to prevent cascade 500s
"""
import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# ── 1. Replace init_sqlite with migration-safe version ────────────────────────
old_init = (
    "def init_sqlite():\n"
    '    """Create all tables if they don\'t exist."""\n'
    "    conn = sqlite3.connect('db_local.sqlite')\n"
)
# Find and replace the entire init_sqlite function
# We'll use a marker approach
new_sqlite_func = '''def init_sqlite():
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

'''

# Locate the old init_sqlite
start_marker = 'def init_sqlite():'
end_marker = 'init_sqlite()'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker, start_idx + len(start_marker))
end_idx = content.find('\n', end_idx) + 1  # include the line

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + new_sqlite_func + content[end_idx:]
    print("init_sqlite replaced OK")
else:
    print("WARNING: init_sqlite not found")

# ── 2. Wrap context processor in try/except ───────────────────────────────────
old_ctx = (
    "@app.context_processor\n"
    "def inject_user():\n"
    '    """Inject current user and unread count into every template."""\n'
    "    user = get_current_user()\n"
    "    unread = get_unread_count(user['id'] if user else None)\n"
    "    return dict(current_user=user, unread_count=unread)\n"
)
new_ctx = (
    "@app.context_processor\n"
    "def inject_user():\n"
    '    """Inject current user and unread count into every template."""\n'
    "    try:\n"
    "        user = get_current_user()\n"
    "        unread = get_unread_count(user['id'] if user else None)\n"
    "    except Exception as e:\n"
    "        import logging as _l\n"
    "        _l.getLogger(__name__).error(f'Context processor error: {e}')\n"
    "        user, unread = None, 0\n"
    "    return dict(current_user=user, unread_count=unread)\n"
)
if old_ctx in content:
    content = content.replace(old_ctx, new_ctx, 1)
    print("Context processor wrapped OK")
else:
    print("WARNING: context processor not found — searching loosely...")
    # Try to patch just the function body
    if "def inject_user():" in content:
        print("inject_user found but different whitespace — skipping")
    else:
        print("inject_user NOT found at all")

# ── 3. Add /health route before the Run block ─────────────────────────────────
health_route = '''@app.route('/health')
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


'''

run_marker = "# \u2500\u2500\u2500 Run"
if run_marker not in content:
    run_marker = "if __name__ == '__main__':"

idx = content.rfind(run_marker)
if idx != -1:
    content = content[:idx] + health_route + content[idx:]
    print("/health route added OK")
else:
    print("WARNING: run marker not found")

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("\nAll patches applied. Summary:")
print("  init_sqlite: migration-safe:", 'PRAGMA table_info' in content)
print("  context processor: protected:", 'Context processor error' in content)
print("  /health route:", '/health' in content)
