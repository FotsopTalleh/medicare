"""
Fix: auto-create the SQLite directory if it doesn't exist,
fallback to local 'db_local.sqlite' if the directory can't be created.
"""
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = "SQLITE_PATH = os.environ.get('SQLITE_PATH', 'db_local.sqlite')\n"
new = (
    "SQLITE_PATH = os.environ.get('SQLITE_PATH', 'db_local.sqlite')\n"
    "# Auto-create directory for SQLite (needed when using Render persistent disk)\n"
    "_sqlite_dir = os.path.dirname(os.path.abspath(SQLITE_PATH))\n"
    "if _sqlite_dir and not os.path.exists(_sqlite_dir):\n"
    "    try:\n"
    "        os.makedirs(_sqlite_dir, exist_ok=True)\n"
    "        logger.info(f'Created SQLite directory: {_sqlite_dir}')\n"
    "    except Exception as _e:\n"
    "        logger.warning(f'Cannot create SQLite dir {_sqlite_dir}: {_e} — falling back to local file')\n"
    "        SQLITE_PATH = 'db_local.sqlite'\n"
)

if old in content:
    content = content.replace(old, new, 1)
    print("SQLITE_PATH block patched OK")
else:
    print("WARNING: SQLITE_PATH line not found")

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Also update init_sqlite to use SQLITE_PATH correctly (not the fallback string)
print("Verifying SQLITE_PATH usage...")
with open('app.py', 'r', encoding='utf-8') as f:
    c = f.read()
print("  os.makedirs present:", 'os.makedirs' in c)
print("  SQLITE_PATH fallback present:", "falling back to local file" in c)
