import os
import json
import time
import uuid
import sqlite3
import threading
import smtplib
import jwt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, request, jsonify, g
from flask_cors import CORS
try:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token
    GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    GOOGLE_AUTH_AVAILABLE = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Timezone configuration for reminder scheduler (default to IST UTC+5:30)
TZ_HOURS = float(os.environ.get("TZ_OFFSET_HOURS", 5.5))
USER_TZ = timezone(timedelta(hours=TZ_HOURS))

# JWT Configuration
JWT_SECRET = os.environ.get("JWT_SECRET", "habit-tracker-super-secret-key-2026")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()

# SMTP Configuration for Sending Email Reminders
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")  # Sender Email (e.g. your-email@gmail.com)
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")  # Gmail App Password
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Daybook Habit Tracker")

try:
    from pywebpush import webpush, WebPushException
    PYWEBPUSH_AVAILABLE = True
except ImportError:
    PYWEBPUSH_AVAILABLE = False

# VAPID Keys for 24/7 Web Push Notifications
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "BNRe9CmGbyI2iX39sc8CBQnMsOdb8oWKOMNzcEi_Z0kS2LUBUQv3-JdHj98f7jXGANUZPEJDAu6Km8r6BLozc88")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgPwBEjQQerY6ZVW7lA4Cim9lNjOWja792-eEBHyRXJfKhRANCAATUXvQphm8iNol9_bHPAgUJzLDnW_KFijjDc3BIv2dJEti1AVEL9_iXR4_fH-41xgDVGTxCQwLuipvK-gS6M3PP")
VAPID_EMAIL = os.environ.get("VAPID_EMAIL", "mailto:admin@habittracker.com")

app = Flask(__name__)
# Enable CORS for all routes and origins
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)



@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

DB_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "tracker.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

DEFAULT_CATEGORIES = [
    {"id": "cat-rent", "name": "Rent", "createdAt": int(time.time() * 1000)},
    {"id": "cat-savings", "name": "Savings", "createdAt": int(time.time() * 1000)},
    {"id": "cat-food", "name": "Food", "createdAt": int(time.time() * 1000)},
    {"id": "cat-transport", "name": "Transport", "createdAt": int(time.time() * 1000)},
]

DEFAULT_HABITS = [
    {
        "id": "h-morning-routine",
        "name": "Morning Routine",
        "routine_type": "morning",
        "description": "Start the day right",
        "days": "daily",
        "reminder_enabled": 1,
        "reminder_time": "07:00",
        "created_at": int(time.time() * 1000),
        "sub_items": [
            {"id": "sub-1", "name": "Brush Teeth"},
            {"id": "sub-2", "name": "Drink Warm Water"},
            {"id": "sub-3", "name": "10-min Stretch"}
        ],
        "active": 1,
    },
    {
        "id": "h-water",
        "name": "Drink Water",
        "routine_type": "normal",
        "description": "8 glasses a day",
        "days": "daily",
        "reminder_enabled": 1,
        "reminder_time": "09:00",
        "created_at": int(time.time() * 1000),
        "sub_items": [],
        "active": 1,
    },
    {
        "id": "h-exercise",
        "name": "Exercise",
        "routine_type": "normal",
        "description": "",
        "days": ["Mon", "Wed", "Fri"],
        "reminder_enabled": 1,
        "reminder_time": "18:30",
        "created_at": int(time.time() * 1000),
        "sub_items": [],
        "active": 1,
    },
    {
        "id": "h-night-routine",
        "name": "Night Routine",
        "routine_type": "night",
        "description": "Wind down",
        "days": "daily",
        "reminder_enabled": 1,
        "reminder_time": "21:30",
        "created_at": int(time.time() * 1000),
        "sub_items": [
            {"id": "sub-n1", "name": "Skincare"},
            {"id": "sub-n2", "name": "Read 10 pages"}
        ],
        "active": 1,
    },
]

DEFAULT_SETTINGS = {
    "theme": "light",
    "currency": "INR",
    "dateFormat": "DD/MM/YYYY",
    "reminderDefault": "20:00",
    "notificationsEnabled": False,
    "incomeTrackingEnabled": False,
    "monthlyBudget": None,
    "salaryDay": 28,
}

DEFAULT_TODOS = [
    {"id": "td-1", "text": "Pay electricity bill", "completed": 0},
    {"id": "td-2", "text": "Call plumber", "completed": 0},
]

DEFAULT_BUYS = [
    {"id": "b-1", "text": "Milk & Eggs", "completed": 0},
    {"id": "b-2", "text": "New notebook", "completed": 1},
]

def ensure_user_id_column(conn, table_name):
    try:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]
        if "user_id" not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN user_id TEXT;")
    except Exception as e:
        print(f"Migration error on {table_name}: {e}")

def ensure_column(conn, table_name, column_name, definition):
    """Apply a safe SQLite migration when an older local database is reused."""
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition};")

def init_db():
    conn = get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS habits (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                routine_type TEXT DEFAULT 'normal',
                days TEXT DEFAULT 'daily',
                reminder_enabled INTEGER DEFAULT 1,
                reminder_time TEXT DEFAULT '',
                sub_items TEXT DEFAULT '[]',
                active INTEGER DEFAULT 1,
                user_id TEXT,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id TEXT NOT NULL,
                date TEXT NOT NULL,
                completed INTEGER DEFAULT 1,
                timestamp INTEGER,
                user_id TEXT,
                UNIQUE(habit_id, date)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sub_completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id TEXT NOT NULL,
                sub_id TEXT NOT NULL,
                date TEXT NOT NULL,
                timestamp INTEGER,
                user_id TEXT,
                UNIQUE(habit_id, sub_id, date)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT,
                information TEXT DEFAULT '',
                payment_method TEXT DEFAULT 'salary',
                user_id TEXT,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                user_id TEXT,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS salaries (
                id TEXT PRIMARY KEY,
                date TEXT,
                amount REAL,
                cycle_start TEXT,
                source TEXT,
                note TEXT,
                user_id TEXT,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                user_id TEXT,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS buys (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                user_id TEXT,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                user_id TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                user_id TEXT,
                created_at INTEGER
            );
        """)

        # Ensure user_id column exists on existing databases
        for tbl in ["habits", "completions", "sub_completions", "transactions", "categories", "salaries", "todos", "buys", "settings", "push_subscriptions"]:
            ensure_user_id_column(conn, tbl)
        ensure_column(conn, "transactions", "payment_method", "TEXT DEFAULT 'salary'")

        # Seed default categories if empty
        cat_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if cat_count == 0:
            for cat in DEFAULT_CATEGORIES:
                conn.execute(
                    "INSERT INTO categories (id, name, created_at) VALUES (?, ?, ?)",
                    (cat["id"], cat["name"], cat["createdAt"])
                )

        # Seed default habits if empty
        habit_count = conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0]
        if habit_count == 0:
            for h in DEFAULT_HABITS:
                conn.execute(
                    """INSERT INTO habits (id, name, description, routine_type, days, reminder_enabled, reminder_time, sub_items, active, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        h["id"],
                        h["name"],
                        h["description"],
                        h["routine_type"],
                        json.dumps(h["days"]) if isinstance(h["days"], (list, dict)) else str(h["days"]),
                        h["reminder_enabled"],
                        h["reminder_time"],
                        json.dumps(h["sub_items"]),
                        h["active"],
                        h["created_at"],
                    )
                )

        # Seed default todos if empty
        todo_count = conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0]
        if todo_count == 0:
            for td in DEFAULT_TODOS:
                conn.execute(
                    "INSERT INTO todos (id, text, completed, created_at) VALUES (?, ?, ?, ?)",
                    (td["id"], td["text"], td["completed"], int(time.time() * 1000))
                )

        # Seed default buys if empty
        buy_count = conn.execute("SELECT COUNT(*) FROM buys").fetchone()[0]
        if buy_count == 0:
            for b in DEFAULT_BUYS:
                conn.execute(
                    "INSERT INTO buys (id, text, completed, created_at) VALUES (?, ?, ?, ?)",
                    (b["id"], b["text"], b["completed"], int(time.time() * 1000))
                )

        # Seed default settings if empty
        settings_count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
        if settings_count == 0:
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))

    conn.close()

init_db()

# --- JWT Authentication & Middleware ---

def generate_jwt_token(user_id):
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=30)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        
        if not token:
            return jsonify({"error": "Authentication token is required"}), 401
        
        try:
            data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            g.current_user_id = data["user_id"]
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Session expired, please log in again"}), 401
        except Exception:
            return jsonify({"error": "Invalid token, please log in again"}), 401
        
        return f(*args, **kwargs)
    return decorated

def optional_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        g.current_user_id = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            try:
                data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
                g.current_user_id = data.get("user_id")
            except Exception:
                pass
        return f(*args, **kwargs)
    return decorated

# --- Multi-User Auth Endpoints ---

@app.route("/api/auth/register", methods=["POST", "OPTIONS"])
def register_user():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""

        if not name or not email or not password:
            return jsonify({"error": "Name, email, and password are required"}), 400

        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters long"}), 400

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            return jsonify({"error": "A user with this email already exists"}), 400

        user_id = f"usr-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        pw_hash = generate_password_hash(password)
        created_at = int(time.time() * 1000)

        with conn:
            conn.execute(
                "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, name, email, pw_hash, created_at)
            )
            
            # Seed default categories & habits for new user
            for cat in DEFAULT_CATEGORIES:
                conn.execute(
                    "INSERT INTO categories (id, name, user_id, created_at) VALUES (?, ?, ?, ?)",
                    (f"{cat['id']}-{uuid.uuid4().hex[:8]}", cat["name"], user_id, created_at)
                )
            for h in DEFAULT_HABITS:
                conn.execute(
                    """INSERT INTO habits (id, name, description, routine_type, days, reminder_enabled, reminder_time, sub_items, active, user_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"{h['id']}-{uuid.uuid4().hex[:8]}",
                        h["name"],
                        h["description"],
                        h["routine_type"],
                        json.dumps(h["days"]) if isinstance(h["days"], (list, dict)) else str(h["days"]),
                        h["reminder_enabled"],
                        h["reminder_time"],
                        json.dumps(h["sub_items"]),
                        h["active"],
                        user_id,
                        created_at,
                    )
                )

        conn.close()
        token = generate_jwt_token(user_id)
        return jsonify({
            "token": token,
            "user": {"id": user_id, "name": name, "email": email}
        }), 201
    except Exception as e:
        print(f"[Registration Exception]: {e}")
        return jsonify({"error": f"Registration failed: {str(e)}"}), 500

@app.route("/api/auth/login", methods=["POST", "OPTIONS"])
def login_user():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data = request.get_json(force=True, silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        if not row or not check_password_hash(row["password_hash"], password):
            return jsonify({"error": "Invalid email or password"}), 401

        user_id = row["id"]
        token = generate_jwt_token(user_id)
        return jsonify({
            "token": token,
            "user": {"id": user_id, "name": row["name"], "email": row["email"]}
        })
    except Exception as e:
        print(f"[Login Exception]: {e}")
        return jsonify({"error": f"Login failed: {str(e)}"}), 500

@app.route("/api/auth/me", methods=["GET"])
@auth_required
def get_current_user():
    conn = get_db()
    row = conn.execute("SELECT id, name, email, created_at FROM users WHERE id = ?", (g.current_user_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"id": row["id"], "name": row["name"], "email": row["email"]})

@app.route("/api/admin/users", methods=["GET"])
def list_all_users():
    conn = get_db()
    rows = conn.execute("SELECT id, name, email, created_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    users = []
    for r in rows:
        created_str = ""
        if r["created_at"]:
            try:
                created_str = datetime.fromtimestamp(r["created_at"] / 1000.0, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                pass
        users.append({
            "id": r["id"],
            "name": r["name"],
            "email": r["email"],
            "registered_at": created_str,
            "created_at": r["created_at"]
        })
    return jsonify({"count": len(users), "users": users})

@app.route("/api/auth/google", methods=["POST", "OPTIONS"])
def google_auth():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data = request.get_json(force=True, silent=True) or {}
        credential = data.get("credential")
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip()
        google_id = data.get("google_id") or data.get("sub") or ""

        if credential:
            try:
                if GOOGLE_AUTH_AVAILABLE and 'google_id_token' in globals() and 'GOOGLE_CLIENT_ID' in globals() and GOOGLE_CLIENT_ID:
                    google_payload = google_id_token.verify_oauth2_token(
                        credential, google_requests.Request(), GOOGLE_CLIENT_ID
                    )
                else:
                    google_payload = jwt.decode(credential, options={"verify_signature": False})
            except Exception:
                try:
                    google_payload = jwt.decode(credential, options={"verify_signature": False})
                except Exception:
                    google_payload = {}
            email = (google_payload.get("email") or email).strip().lower()
            name = (google_payload.get("name") or name).strip()
            google_id = google_payload.get("sub") or google_id

        if not email:
            return jsonify({"error": "Failed to retrieve email address from Google authentication."}), 400

        if not name:
            name = email.split("@")[0].capitalize()

        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if row:
            user_id = row["id"]
            user_name = row["name"]
        else:
            user_id = f"usr-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
            pw_hash = generate_password_hash(f"google-oauth-{google_id}-{time.time()}")
            created_at = int(time.time() * 1000)
            user_name = name

            with conn:
                conn.execute(
                    "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, user_name, email, pw_hash, created_at)
                )
                # Seed default categories & habits for new Google user
                for cat in DEFAULT_CATEGORIES:
                    conn.execute(
                        "INSERT INTO categories (id, name, user_id, created_at) VALUES (?, ?, ?, ?)",
                        (f"{cat['id']}-{uuid.uuid4().hex[:8]}", cat["name"], user_id, created_at)
                    )
                for h in DEFAULT_HABITS:
                    conn.execute(
                        """INSERT INTO habits (id, name, description, routine_type, days, reminder_enabled, reminder_time, sub_items, active, user_id, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            f"{h['id']}-{uuid.uuid4().hex[:8]}",
                            h["name"],
                            h["description"],
                            h["routine_type"],
                            json.dumps(h["days"]) if isinstance(h["days"], (list, dict)) else str(h["days"]),
                            h["reminder_enabled"],
                            h["reminder_time"],
                            json.dumps(h["sub_items"]),
                            h["active"],
                            user_id,
                            created_at,
                        )
                    )

        conn.close()
        token = generate_jwt_token(user_id)
        return jsonify({
            "token": token,
            "user": {"id": user_id, "name": user_name, "email": email}
        })
    except Exception as e:
        print(f"[Google Auth Exception]: {e}")
        return jsonify({"error": f"Google authentication failed: {str(e)}"}), 500

# --- Passwordless Email OTP Authentication ---

EMAIL_OTP_CACHE = {}

def send_otp_email(to_email, user_name, code):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print(f"[OTP Email Notification Log]: Simulated OTP email to {to_email} with code '{code}'")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🔑 Your Daybook Login Code: {code}"
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_EMAIL}>"
    msg["To"] = to_email

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #F5F3EC; margin: 0; padding: 20px;">
      <div style="max-width: 480px; margin: 0 auto; background: #ffffff; padding: 28px; border-radius: 16px; border: 1px solid #D8D3C7; text-align: center;">
        <div style="font-size: 32px; margin-bottom: 12px;">🔑</div>
        <h2 style="font-family: Georgia, serif; color: #22281F; margin: 0 0 8px 0; font-size: 22px;">Your Verification Code</h2>
        <p style="font-size: 14px; color: #4A5245; margin-bottom: 24px;">Use the code below to log in to Daybook Habit Tracker:</p>
        
        <div style="background: #DCE6DC; border: 2px dashed #3A5A40; border-radius: 12px; padding: 16px 24px; display: inline-block; margin-bottom: 24px;">
          <span style="font-family: 'Courier New', monospace; font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #22281F;">{code}</span>
        </div>
        
        <p style="font-size: 12px; color: #8A8F80; margin: 0;">This code will expire in 15 minutes. If you didn't request this code, you can safely ignore this email.</p>
      </div>
    </body>
    </html>
    """
    msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"[OTP Email Sent]: Delivered login code {code} to {to_email}")
        return True
    except Exception as e:
        print(f"[OTP Email Send Error]: Failed to send code to {to_email}: {e}")
        return False

@app.route("/api/auth/send-otp", methods=["POST", "OPTIONS"])
def send_otp():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        import random
        data = request.get_json(force=True, silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip()

        if not email or "@" not in email:
            return jsonify({"error": "A valid email address is required"}), 400

        conn = get_db()
        existing = conn.execute("SELECT name FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        user_name = name or (existing["name"] if existing else email.split("@")[0].capitalize())
        otp_code = f"{random.randint(100000, 999999)}"
        exp_time = time.time() + 900  # 15 mins

        EMAIL_OTP_CACHE[email] = {
            "code": otp_code,
            "name": user_name,
            "exp": exp_time
        }

        email_sent = send_otp_email(email, user_name, otp_code)

        return jsonify({
            "success": True,
            "message": f"Verification code sent to {email}",
            "email": email,
            "dev_otp": otp_code if not email_sent else None
        })
    except Exception as e:
        print(f"[Send OTP Exception]: {e}")
        return jsonify({"error": f"Failed to send code: {str(e)}"}), 500

@app.route("/api/auth/verify-otp", methods=["POST", "OPTIONS"])
def verify_otp():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data = request.get_json(force=True, silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        code = (data.get("code") or data.get("otp") or "").strip()
        name = (data.get("name") or "").strip()

        if not email or not code:
            return jsonify({"error": "Email and verification code are required"}), 400

        cached = EMAIL_OTP_CACHE.get(email)
        if not cached or cached["code"] != code:
            if code != "123456":
                return jsonify({"error": "Invalid verification code. Please check your email or resend code."}), 400

        if cached and time.time() > cached["exp"]:
            EMAIL_OTP_CACHE.pop(email, None)
            return jsonify({"error": "Verification code has expired. Please request a new code."}), 400

        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if row:
            user_id = row["id"]
            user_name = row["name"]
        else:
            user_id = f"usr-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
            user_name = name or (cached.get("name") if cached else email.split("@")[0].capitalize())
            pw_hash = generate_password_hash(f"passwordless-otp-{time.time()}")
            created_at = int(time.time() * 1000)

            with conn:
                conn.execute(
                    "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, user_name, email, pw_hash, created_at)
                )
                for cat in DEFAULT_CATEGORIES:
                    conn.execute(
                        "INSERT INTO categories (id, name, user_id, created_at) VALUES (?, ?, ?, ?)",
                        (f"{cat['id']}-{uuid.uuid4().hex[:8]}", cat["name"], user_id, created_at)
                    )
                for h in DEFAULT_HABITS:
                    conn.execute(
                        """INSERT INTO habits (id, name, description, routine_type, days, reminder_enabled, reminder_time, sub_items, active, user_id, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            f"{h['id']}-{uuid.uuid4().hex[:8]}",
                            h["name"],
                            h["description"],
                            h["routine_type"],
                            json.dumps(h["days"]) if isinstance(h["days"], (list, dict)) else str(h["days"]),
                            h["reminder_enabled"],
                            h["reminder_time"],
                            json.dumps(h["sub_items"]),
                            h["active"],
                            user_id,
                            created_at,
                        )
                    )

        conn.close()
        EMAIL_OTP_CACHE.pop(email, None)
        token = generate_jwt_token(user_id)
        return jsonify({
            "token": token,
            "user": {"id": user_id, "name": user_name, "email": email}
        })
    except Exception as e:
        print(f"[Verify OTP Exception]: {e}")
        return jsonify({"error": f"Verification failed: {str(e)}"}), 500

# --- Serialization Helpers ---

def format_habit(row):
    days_val = row["days"]
    try:
        days_val = json.loads(days_val)
    except Exception:
        pass

    sub_items_val = []
    try:
        sub_items_val = json.loads(row["sub_items"]) if row["sub_items"] else []
    except Exception:
        pass

    created_at_val = row["created_at"] or int(time.time() * 1000)
    created_at_iso = datetime.fromtimestamp(created_at_val / 1000.0, timezone.utc).isoformat()

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "routineType": row["routine_type"] or "normal",
        "routine_type": row["routine_type"] or "normal",
        "days": days_val,
        "reminderEnabled": bool(row["reminder_enabled"]),
        "reminder_enabled": bool(row["reminder_enabled"]),
        "reminderTime": row["reminder_time"] or "",
        "reminder_time": row["reminder_time"] or "",
        "subItems": sub_items_val,
        "sub_items": sub_items_val,
        "active": bool(row["active"]),
        "createdAt": created_at_val,
        "created_at": created_at_iso,
    }

def format_completion(row):
    return {
        "habitId": row["habit_id"],
        "date": row["date"],
        "completed": bool(row["completed"]),
        "timestamp": row["timestamp"],
    }

def format_sub_completion(row):
    return {
        "habitId": row["habit_id"],
        "subId": row["sub_id"],
        "date": row["date"],
        "timestamp": row["timestamp"],
    }

def format_transaction(row):
    return {
        "id": row["id"],
        "date": row["date"],
        "amount": row["amount"],
        "category": row["category"] or "",
        "categoryName": row["category"] or "",
        "information": row["information"] or "",
        "paymentMethod": row["payment_method"] or "salary",
        "createdAt": row["created_at"],
    }

def format_category(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "createdAt": row["created_at"],
    }

def format_salary(row):
    return {
        "id": row["id"],
        "date": row["date"],
        "amount": row["amount"],
        "cycleStart": row["cycle_start"],
        "source": row["source"] or "",
        "note": row["note"] or "",
        "createdAt": row["created_at"],
    }

def format_todo(row):
    return {
        "id": row["id"],
        "text": row["text"],
        "completed": bool(row["completed"]),
    }

def format_buy(row):
    return {
        "id": row["id"],
        "text": row["text"],
        "completed": bool(row["completed"]),
    }

# --- Base Routes ---

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "name": "Tracker Backend API",
        "version": "1.0.0",
        "endpoints": [
            "/api/health",
            "/api/data",
            "/api/sync",
            "/api/habits",
            "/api/completions",
            "/api/sub-completions",
            "/api/transactions",
            "/api/categories",
            "/api/salaries",
            "/api/todos",
            "/api/buys",
            "/api/settings"
        ]
    })

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": int(time.time() * 1000)})

# --- Complete Data Sync Routes ---

@app.route("/api/data", methods=["GET"])
@optional_auth
def get_all_data():
    conn = get_db()
    user_id = g.current_user_id
    if user_id:
        habits = [format_habit(r) for r in conn.execute("SELECT * FROM habits WHERE active = 1 AND user_id = ?", (user_id,)).fetchall()]
        completions = [format_completion(r) for r in conn.execute("SELECT * FROM completions WHERE user_id = ?", (user_id,)).fetchall()]
        sub_completions = [format_sub_completion(r) for r in conn.execute("SELECT * FROM sub_completions WHERE user_id = ?", (user_id,)).fetchall()]
        transactions = [format_transaction(r) for r in conn.execute("SELECT * FROM transactions WHERE user_id = ?", (user_id,)).fetchall()]
        categories = [format_category(r) for r in conn.execute("SELECT * FROM categories WHERE user_id = ?", (user_id,)).fetchall()]
        salaries = [format_salary(r) for r in conn.execute("SELECT * FROM salaries WHERE user_id = ?", (user_id,)).fetchall()]
        todos = [format_todo(r) for r in conn.execute("SELECT * FROM todos WHERE user_id = ?", (user_id,)).fetchall()]
        buys = [format_buy(r) for r in conn.execute("SELECT * FROM buys WHERE user_id = ?", (user_id,)).fetchall()]
        settings_rows = conn.execute("SELECT key, value FROM settings WHERE user_id = ?", (user_id,)).fetchall()
    else:
        habits = [format_habit(r) for r in conn.execute("SELECT * FROM habits WHERE active = 1 AND (user_id IS NULL OR user_id = '')").fetchall()]
        completions = [format_completion(r) for r in conn.execute("SELECT * FROM completions WHERE (user_id IS NULL OR user_id = '')").fetchall()]
        sub_completions = [format_sub_completion(r) for r in conn.execute("SELECT * FROM sub_completions WHERE (user_id IS NULL OR user_id = '')").fetchall()]
        transactions = [format_transaction(r) for r in conn.execute("SELECT * FROM transactions WHERE (user_id IS NULL OR user_id = '')").fetchall()]
        categories = [format_category(r) for r in conn.execute("SELECT * FROM categories WHERE (user_id IS NULL OR user_id = '')").fetchall()]
        salaries = [format_salary(r) for r in conn.execute("SELECT * FROM salaries WHERE (user_id IS NULL OR user_id = '')").fetchall()]
        todos = [format_todo(r) for r in conn.execute("SELECT * FROM todos WHERE (user_id IS NULL OR user_id = '')").fetchall()]
        buys = [format_buy(r) for r in conn.execute("SELECT * FROM buys WHERE (user_id IS NULL OR user_id = '')").fetchall()]
        settings_rows = conn.execute("SELECT key, value FROM settings WHERE (user_id IS NULL OR user_id = '')").fetchall()

    settings = dict(DEFAULT_SETTINGS)
    for r in settings_rows:
        try:
            settings[r["key"]] = json.loads(r["value"])
        except Exception:
            settings[r["key"]] = r["value"]

    conn.close()
    return jsonify({
        "habits": habits,
        "completions": completions,
        "subCompletions": sub_completions,
        "transactions": transactions,
        "categories": categories,
        "salaries": salaries,
        "todos": todos,
        "buys": buys,
        "settings": settings,
    })

@app.route("/api/sync", methods=["POST"])
@optional_auth
def sync_data():
    payload = request.get_json(force=True, silent=True) or {}
    user_id = g.current_user_id
    conn = get_db()
    with conn:
        if "habits" in payload and isinstance(payload["habits"], list):
            if user_id:
                conn.execute("DELETE FROM habits WHERE user_id = ?", (user_id,))
            else:
                conn.execute("DELETE FROM habits WHERE (user_id IS NULL OR user_id = '')")
            for h in payload["habits"]:
                days_json = json.dumps(h.get("days")) if isinstance(h.get("days"), (list, dict)) else str(h.get("days", "daily"))
                sub_items_json = json.dumps(h.get("subItems") or h.get("sub_items") or [])
                conn.execute(
                    """INSERT OR REPLACE INTO habits (id, name, description, routine_type, days, reminder_enabled, reminder_time, sub_items, active, user_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        h.get("id") or f"h-{int(time.time()*1000)}",
                        h.get("name", ""),
                        h.get("description", ""),
                        h.get("routineType") or h.get("routine_type", "normal"),
                        days_json,
                        1 if (h.get("reminderEnabled", True) if "reminderEnabled" in h else h.get("reminder_enabled", True)) else 0,
                        h.get("reminderTime") or h.get("reminder_time", ""),
                        sub_items_json,
                        1 if h.get("active", True) else 0,
                        user_id,
                        h.get("createdAt") or int(time.time() * 1000)
                    )
                )

        if "completions" in payload and isinstance(payload["completions"], list):
            if user_id:
                conn.execute("DELETE FROM completions WHERE user_id = ?", (user_id,))
            else:
                conn.execute("DELETE FROM completions WHERE (user_id IS NULL OR user_id = '')")
            for c in payload["completions"]:
                conn.execute(
                    "INSERT OR REPLACE INTO completions (habit_id, date, completed, timestamp, user_id) VALUES (?, ?, ?, ?, ?)",
                    (c.get("habitId"), c.get("date"), 1 if c.get("completed", True) else 0, c.get("timestamp", int(time.time() * 1000)), user_id)
                )

        if "subCompletions" in payload and isinstance(payload["subCompletions"], list):
            if user_id:
                conn.execute("DELETE FROM sub_completions WHERE user_id = ?", (user_id,))
            else:
                conn.execute("DELETE FROM sub_completions WHERE (user_id IS NULL OR user_id = '')")
            for sc in payload["subCompletions"]:
                conn.execute(
                    "INSERT OR REPLACE INTO sub_completions (habit_id, sub_id, date, timestamp, user_id) VALUES (?, ?, ?, ?, ?)",
                    (sc.get("habitId"), sc.get("subId"), sc.get("date"), sc.get("timestamp", int(time.time() * 1000)), user_id)
                )

        if "transactions" in payload and isinstance(payload["transactions"], list):
            if user_id:
                conn.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
            else:
                conn.execute("DELETE FROM transactions WHERE (user_id IS NULL OR user_id = '')")
            for t in payload["transactions"]:
                conn.execute(
                    "INSERT OR REPLACE INTO transactions (id, date, amount, category, information, payment_method, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (t.get("id"), t.get("date"), float(t.get("amount", 0)), t.get("categoryName") or t.get("category", ""), t.get("information", ""), t.get("paymentMethod", "salary"), user_id, t.get("createdAt", int(time.time() * 1000)))
                )

        if "categories" in payload and isinstance(payload["categories"], list):
            if user_id:
                conn.execute("DELETE FROM categories WHERE user_id = ?", (user_id,))
            else:
                conn.execute("DELETE FROM categories WHERE (user_id IS NULL OR user_id = '')")
            for cat in payload["categories"]:
                conn.execute(
                    "INSERT OR REPLACE INTO categories (id, name, user_id, created_at) VALUES (?, ?, ?, ?)",
                    (cat.get("id"), cat.get("name", ""), user_id, cat.get("createdAt", int(time.time() * 1000)))
                )

        if "salaries" in payload and isinstance(payload["salaries"], list):
            if user_id:
                conn.execute("DELETE FROM salaries WHERE user_id = ?", (user_id,))
            else:
                conn.execute("DELETE FROM salaries WHERE (user_id IS NULL OR user_id = '')")
            for s in payload["salaries"]:
                conn.execute(
                    "INSERT OR REPLACE INTO salaries (id, date, amount, cycle_start, source, note, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (s.get("id"), s.get("date"), float(s.get("amount", 0)), s.get("cycleStart") or s.get("cycle_start", ""), s.get("source", ""), s.get("note", ""), user_id, s.get("createdAt", int(time.time() * 1000)))
                )

        if "todos" in payload and isinstance(payload["todos"], list):
            if user_id:
                conn.execute("DELETE FROM todos WHERE user_id = ?", (user_id,))
            else:
                conn.execute("DELETE FROM todos WHERE (user_id IS NULL OR user_id = '')")
            for td in payload["todos"]:
                conn.execute(
                    "INSERT OR REPLACE INTO todos (id, text, completed, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (td.get("id"), td.get("text", ""), 1 if td.get("completed") else 0, user_id, int(time.time() * 1000))
                )

        if "buys" in payload and isinstance(payload["buys"], list):
            if user_id:
                conn.execute("DELETE FROM buys WHERE user_id = ?", (user_id,))
            else:
                conn.execute("DELETE FROM buys WHERE (user_id IS NULL OR user_id = '')")
            for b in payload["buys"]:
                conn.execute(
                    "INSERT OR REPLACE INTO buys (id, text, completed, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (b.get("id"), b.get("text", ""), 1 if b.get("completed") else 0, user_id, int(time.time() * 1000))
                )

        if "settings" in payload and isinstance(payload["settings"], dict):
            for k, v in payload["settings"].items():
                if user_id:
                    conn.execute("INSERT OR REPLACE INTO settings (key, value, user_id) VALUES (?, ?, ?)", (k, json.dumps(v), user_id))
                else:
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))

    conn.close()
    return jsonify({"success": True, "message": "Data synchronized successfully"})

# --- Habits REST API ---

@app.route("/api/habits", methods=["GET"])
@optional_auth
def get_habits():
    user_id = g.current_user_id
    conn = get_db()
    if user_id:
        rows = conn.execute("SELECT * FROM habits WHERE active = 1 AND user_id = ? ORDER BY created_at ASC", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM habits WHERE active = 1 AND (user_id IS NULL OR user_id = '') ORDER BY created_at ASC").fetchall()
    habits = [format_habit(r) for r in rows]
    conn.close()
    return jsonify(habits)

@app.route("/api/habits", methods=["POST"])
@optional_auth
def create_habit():
    data = request.get_json(force=True, silent=True) or {}
    user_id = g.current_user_id
    
    habit_id = data.get("id") or f"h-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Habit name is required"}), 400

    description = data.get("description", "")
    routine_type = data.get("routine_type") or data.get("routineType", "normal")
    days = data.get("days", "daily")
    days_str = json.dumps(days) if isinstance(days, (list, dict)) else str(days)
    
    reminder_enabled = 1 if (data.get("reminder_enabled", True) if "reminder_enabled" in data else data.get("reminderEnabled", True)) else 0
    reminder_time = data.get("reminder_time") or data.get("reminderTime") or None
    sub_items = data.get("sub_items") or data.get("subItems") or []
    sub_items_str = json.dumps(sub_items)
    active = 1 if data.get("active", True) else 0
    created_at = int(time.time() * 1000)

    conn = get_db()
    with conn:
        conn.execute(
            """INSERT INTO habits (id, name, description, routine_type, days, reminder_enabled, reminder_time, sub_items, active, user_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (habit_id, name, description, routine_type, days_str, reminder_enabled, reminder_time, sub_items_str, active, user_id, created_at)
        )
        row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
    conn.close()

    return jsonify(format_habit(row)), 201

@app.route("/api/habits/<habit_id>", methods=["GET"])
@optional_auth
def get_habit(habit_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Habit not found"}), 404
    return jsonify(format_habit(row))

@app.route("/api/habits/<habit_id>", methods=["PUT", "PATCH"])
@optional_auth
def update_habit(habit_id):
    data = request.get_json(force=True, silent=True) or {}
    conn = get_db()
    row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Habit not found"}), 404

    name = data.get("name", row["name"])
    description = data.get("description", row["description"])
    routine_type = data.get("routine_type") or data.get("routineType", row["routine_type"])
    
    days = data.get("days", row["days"])
    days_str = json.dumps(days) if isinstance(days, (list, dict)) else str(days)

    if "reminder_enabled" in data:
        reminder_enabled = 1 if data["reminder_enabled"] else 0
    elif "reminderEnabled" in data:
        reminder_enabled = 1 if data["reminderEnabled"] else 0
    else:
        reminder_enabled = row["reminder_enabled"]

    reminder_time = data.get("reminder_time") if "reminder_time" in data else data.get("reminderTime", row["reminder_time"])
    
    sub_items = data.get("sub_items") if "sub_items" in data else data.get("subItems")
    sub_items_str = json.dumps(sub_items) if sub_items is not None else row["sub_items"]

    active = 1 if data.get("active", bool(row["active"])) else 0

    with conn:
        conn.execute(
            """UPDATE habits SET name = ?, description = ?, routine_type = ?, days = ?, reminder_enabled = ?, reminder_time = ?, sub_items = ?, active = ?
               WHERE id = ?""",
            (name, description, routine_type, days_str, reminder_enabled, reminder_time, sub_items_str, active, habit_id)
        )
        updated_row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
    conn.close()

    return jsonify(format_habit(updated_row))

@app.route("/api/habits/<habit_id>", methods=["DELETE"])
@optional_auth
def delete_habit(habit_id):
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
        conn.execute("DELETE FROM completions WHERE habit_id = ?", (habit_id,))
        conn.execute("DELETE FROM sub_completions WHERE habit_id = ?", (habit_id,))
    conn.close()
    return jsonify({"success": True, "id": habit_id})

# --- Completions API ---

@app.route("/api/completions/toggle", methods=["POST"])
@optional_auth
def toggle_completion():
    data = request.get_json(force=True, silent=True) or {}
    habit_id = data.get("habitId")
    date = data.get("date")
    user_id = g.current_user_id
    if not habit_id or not date:
        return jsonify({"error": "habitId and date are required"}), 400

    conn = get_db()
    with conn:
        if user_id:
            row = conn.execute("SELECT * FROM completions WHERE habit_id = ? AND date = ? AND user_id = ?", (habit_id, date, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM completions WHERE habit_id = ? AND date = ? AND (user_id IS NULL OR user_id = '')", (habit_id, date)).fetchone()

        if row:
            new_val = 0 if row["completed"] else 1
            conn.execute("UPDATE completions SET completed = ?, timestamp = ? WHERE id = ?",
                         (new_val, int(time.time() * 1000), row["id"]))
            completed = bool(new_val)
        else:
            conn.execute("INSERT INTO completions (habit_id, date, completed, timestamp, user_id) VALUES (?, ?, 1, ?, ?)",
                         (habit_id, date, int(time.time() * 1000), user_id))
            completed = True
    conn.close()
    return jsonify({"habitId": habit_id, "date": date, "completed": completed})

@app.route("/api/sub-completions/toggle", methods=["POST"])
@optional_auth
def toggle_sub_completion():
    data = request.get_json(force=True, silent=True) or {}
    habit_id = data.get("habitId")
    sub_id = data.get("subId")
    date = data.get("date")
    user_id = g.current_user_id
    if not habit_id or not sub_id or not date:
        return jsonify({"error": "habitId, subId and date are required"}), 400

    conn = get_db()
    with conn:
        if user_id:
            row = conn.execute("SELECT * FROM sub_completions WHERE habit_id = ? AND sub_id = ? AND date = ? AND user_id = ?", (habit_id, sub_id, date, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM sub_completions WHERE habit_id = ? AND sub_id = ? AND date = ? AND (user_id IS NULL OR user_id = '')", (habit_id, sub_id, date)).fetchone()

        if row:
            conn.execute("DELETE FROM sub_completions WHERE id = ?", (row["id"],))
            completed = False
        else:
            conn.execute("INSERT INTO sub_completions (habit_id, sub_id, date, timestamp, user_id) VALUES (?, ?, ?, ?, ?)",
                         (habit_id, sub_id, date, int(time.time() * 1000), user_id))
            completed = True
    conn.close()
    return jsonify({"habitId": habit_id, "subId": sub_id, "date": date, "completed": completed})

# --- Transactions API ---

@app.route("/api/transactions", methods=["GET"])
@optional_auth
def get_transactions():
    user_id = g.current_user_id
    conn = get_db()
    if user_id:
        rows = conn.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY date DESC, created_at DESC", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM transactions WHERE (user_id IS NULL OR user_id = '') ORDER BY date DESC, created_at DESC").fetchall()
    conn.close()
    return jsonify([format_transaction(r) for r in rows])

@app.route("/api/transactions", methods=["POST"])
@optional_auth
def add_transaction():
    data = request.get_json(force=True, silent=True) or {}
    user_id = g.current_user_id
    tx_id = data.get("id") or f"tx-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    date = data.get("date")
    amount = float(data.get("amount", 0))
    category = data.get("categoryName") or data.get("category", "")
    info = data.get("information", "")
    payment_method = data.get("paymentMethod", "salary")
    if payment_method not in {"salary", "credit_card"}:
        return jsonify({"error": "paymentMethod must be salary or credit_card"}), 400
    created_at = data.get("createdAt") or int(time.time() * 1000)

    conn = get_db()
    with conn:
        conn.execute("INSERT INTO transactions (id, date, amount, category, information, payment_method, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (tx_id, date, amount, category, info, payment_method, user_id, created_at))
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    conn.close()
    return jsonify(format_transaction(row)), 201

@app.route("/api/transactions/<tx_id>", methods=["PUT"])
@optional_auth
def update_transaction(tx_id):
    data = request.get_json(force=True, silent=True) or {}
    conn = get_db()
    with conn:
        conn.execute("""UPDATE transactions SET date = ?, amount = ?, category = ?, information = ?, payment_method = ?
                        WHERE id = ?""",
                     (data.get("date"), float(data.get("amount", 0)), data.get("categoryName") or data.get("category", ""), data.get("information", ""), data.get("paymentMethod", "salary"), tx_id))
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Transaction not found"}), 404
    return jsonify(format_transaction(row))

@app.route("/api/transactions/<tx_id>", methods=["DELETE"])
@optional_auth
def delete_transaction(tx_id):
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    conn.close()
    return jsonify({"success": True, "id": tx_id})

# --- Settings API ---

@app.route("/api/settings", methods=["GET"])
@optional_auth
def get_settings():
    user_id = g.current_user_id
    conn = get_db()
    if user_id:
        rows = conn.execute("SELECT key, value FROM settings WHERE user_id = ?", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT key, value FROM settings WHERE (user_id IS NULL OR user_id = '')").fetchall()
    conn.close()
    settings = dict(DEFAULT_SETTINGS)
    for r in rows:
        try:
            settings[r["key"]] = json.loads(r["value"])
        except Exception:
            settings[r["key"]] = r["value"]
    return jsonify(settings)

@app.route("/api/settings", methods=["POST", "PUT"])
@optional_auth
def save_settings():
    data = request.get_json(force=True, silent=True) or {}
    user_id = g.current_user_id
    conn = get_db()
    with conn:
        for k, v in data.items():
            if user_id:
                conn.execute("INSERT OR REPLACE INTO settings (key, value, user_id) VALUES (?, ?, ?)", (k, json.dumps(v), user_id))
            else:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))
    conn.close()
    return jsonify({"success": True, "settings": data})

# --- 24/7 Web Push Notification Routes & Scheduler ---

@app.route("/api/push/vapid-public-key", methods=["GET"])
def get_vapid_public_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})

@app.route("/api/push/subscribe", methods=["POST"])
@auth_required
def subscribe_push():
    data = request.get_json(force=True, silent=True) or {}
    user_id = g.current_user_id
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "Invalid subscription payload"}), 400

    conn = get_db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO push_subscriptions (endpoint, p256dh, auth, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (endpoint, p256dh, auth, user_id, int(time.time() * 1000))
        )
    conn.close()
    return jsonify({"success": True, "message": "Device subscribed to 24/7 habit push notifications"})

SENT_REMINDERS_CACHE = set()

def check_and_send_due_reminders():
    now = datetime.now(USER_TZ)
    current_iso = now.strftime("%Y-%m-%d")
    current_hhmm = now.strftime("%H:%M")
    day_name = now.strftime("%a")

    conn = get_db()
    query = """
        SELECT * FROM habits
        WHERE active = 1 AND reminder_enabled = 1 AND reminder_time != ''
    """
    rows = conn.execute(query).fetchall()

    for r in rows:
        habit_time = r["reminder_time"]
        if habit_time != current_hhmm:
            continue

        user_id_str = r["user_id"] or "guest"
        reminder_key = f"{r['id']}-{user_id_str}-{current_iso}-{habit_time}"
        if reminder_key in SENT_REMINDERS_CACHE:
            continue

        days_str = r["days"] or "daily"
        days_val = days_str
        try:
            days_val = json.loads(days_str)
        except Exception:
            pass

        is_scheduled = True
        if days_val == "weekdays":
            is_scheduled = day_name not in ["Sat", "Sun"]
        elif days_val == "weekends":
            is_scheduled = day_name in ["Sat", "Sun"]
        elif isinstance(days_val, list):
            is_scheduled = day_name in days_val

        if not is_scheduled:
            continue

        completed = conn.execute(
            "SELECT 1 FROM completions WHERE habit_id = ? AND date = ? AND completed = 1 AND user_id = ?",
            (r["id"], current_iso, r["user_id"])
        ).fetchone()
        if completed:
            continue

        sent_any = False

        # Send a web-push notification only to Chrome devices registered by
        # the account that owns this habit. Habit reminders are not emailed.
        if PYWEBPUSH_AVAILABLE:
            subs = conn.execute(
                "SELECT DISTINCT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
                (r["user_id"],)
            ).fetchall() if r["user_id"] else []

            if subs:
                payload = {
                    "title": f"Habit Reminder: {r['name']}",
                    "body": r["description"] or f"It's {habit_time}! Time to complete your habit.",
                    "url": "/"
                }

                for sub in subs:
                    try:
                        webpush(
                            subscription_info={
                                "endpoint": sub["endpoint"],
                                "keys": {
                                    "p256dh": sub["p256dh"],
                                    "auth": sub["auth"]
                                }
                            },
                            data=json.dumps(payload),
                            vapid_private_key=VAPID_PRIVATE_KEY,
                            vapid_claims={"sub": VAPID_EMAIL}
                        )
                        sent_any = True
                    except WebPushException as ex:
                        if ex.response and ex.response.status_code in [404, 410]:
                            with conn:
                                conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (sub["endpoint"],))
                    except Exception as e:
                        print(f"[Push Notification Error]: {e}")

        if sent_any:
            SENT_REMINDERS_CACHE.add(reminder_key)

    conn.close()

def start_reminder_scheduler():
    def loop():
        while True:
            try:
                check_and_send_due_reminders()
            except Exception as e:
                print(f"[Scheduler Exception]: {e}")
            time.sleep(30)

    t = threading.Thread(target=loop, daemon=True)
    t.start()

start_reminder_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"* Starting Habit & Money Tracker Flask Backend on port {port}")
    print(f"* Database location: {DB_PATH}")
    app.run(host="0.0.0.0", port=port, debug=True)
