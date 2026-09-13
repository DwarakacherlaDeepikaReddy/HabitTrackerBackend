import os
import json
import time
import uuid
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS

# Timezone configuration for reminder scheduler (default to IST UTC+5:30)
TZ_HOURS = float(os.environ.get("TZ_OFFSET_HOURS", 5.5))
USER_TZ = timezone(timedelta(hours=TZ_HOURS))

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

def init_db():
    conn = get_db()
    with conn:
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
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
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
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS buys (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at INTEGER
            );
        """)

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
        "information": row["information"] or "",
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
def get_all_data():
    conn = get_db()
    habits = [format_habit(r) for r in conn.execute("SELECT * FROM habits WHERE active = 1").fetchall()]
    completions = [format_completion(r) for r in conn.execute("SELECT * FROM completions").fetchall()]
    sub_completions = [format_sub_completion(r) for r in conn.execute("SELECT * FROM sub_completions").fetchall()]
    transactions = [format_transaction(r) for r in conn.execute("SELECT * FROM transactions").fetchall()]
    categories = [format_category(r) for r in conn.execute("SELECT * FROM categories").fetchall()]
    salaries = [format_salary(r) for r in conn.execute("SELECT * FROM salaries").fetchall()]
    todos = [format_todo(r) for r in conn.execute("SELECT * FROM todos").fetchall()]
    buys = [format_buy(r) for r in conn.execute("SELECT * FROM buys").fetchall()]
    
    settings_rows = conn.execute("SELECT key, value FROM settings").fetchall()
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
def sync_data():
    payload = request.get_json(force=True, silent=True) or {}
    conn = get_db()
    with conn:
        if "habits" in payload and isinstance(payload["habits"], list):
            conn.execute("DELETE FROM habits")
            for h in payload["habits"]:
                days_json = json.dumps(h.get("days")) if isinstance(h.get("days"), (list, dict)) else str(h.get("days", "daily"))
                sub_items_json = json.dumps(h.get("subItems") or h.get("sub_items") or [])
                conn.execute(
                    """INSERT OR REPLACE INTO habits (id, name, description, routine_type, days, reminder_enabled, reminder_time, sub_items, active, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        h.get("createdAt") or int(time.time() * 1000)
                    )
                )

        if "completions" in payload and isinstance(payload["completions"], list):
            conn.execute("DELETE FROM completions")
            for c in payload["completions"]:
                conn.execute(
                    "INSERT OR REPLACE INTO completions (habit_id, date, completed, timestamp) VALUES (?, ?, ?, ?)",
                    (c.get("habitId"), c.get("date"), 1 if c.get("completed", True) else 0, c.get("timestamp", int(time.time() * 1000)))
                )

        if "subCompletions" in payload and isinstance(payload["subCompletions"], list):
            conn.execute("DELETE FROM sub_completions")
            for sc in payload["subCompletions"]:
                conn.execute(
                    "INSERT OR REPLACE INTO sub_completions (habit_id, sub_id, date, timestamp) VALUES (?, ?, ?, ?)",
                    (sc.get("habitId"), sc.get("subId"), sc.get("date"), sc.get("timestamp", int(time.time() * 1000)))
                )

        if "transactions" in payload and isinstance(payload["transactions"], list):
            conn.execute("DELETE FROM transactions")
            for t in payload["transactions"]:
                conn.execute(
                    "INSERT OR REPLACE INTO transactions (id, date, amount, category, information, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (t.get("id"), t.get("date"), float(t.get("amount", 0)), t.get("category", ""), t.get("information", ""), t.get("createdAt", int(time.time() * 1000)))
                )

        if "categories" in payload and isinstance(payload["categories"], list):
            conn.execute("DELETE FROM categories")
            for cat in payload["categories"]:
                conn.execute(
                    "INSERT OR REPLACE INTO categories (id, name, created_at) VALUES (?, ?, ?)",
                    (cat.get("id"), cat.get("name", ""), cat.get("createdAt", int(time.time() * 1000)))
                )

        if "salaries" in payload and isinstance(payload["salaries"], list):
            conn.execute("DELETE FROM salaries")
            for s in payload["salaries"]:
                conn.execute(
                    "INSERT OR REPLACE INTO salaries (id, date, amount, cycle_start, source, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (s.get("id"), s.get("date"), float(s.get("amount", 0)), s.get("cycleStart") or s.get("cycle_start", ""), s.get("source", ""), s.get("note", ""), s.get("createdAt", int(time.time() * 1000)))
                )

        if "todos" in payload and isinstance(payload["todos"], list):
            conn.execute("DELETE FROM todos")
            for td in payload["todos"]:
                conn.execute(
                    "INSERT OR REPLACE INTO todos (id, text, completed, created_at) VALUES (?, ?, ?, ?)",
                    (td.get("id"), td.get("text", ""), 1 if td.get("completed") else 0, int(time.time() * 1000))
                )

        if "buys" in payload and isinstance(payload["buys"], list):
            conn.execute("DELETE FROM buys")
            for b in payload["buys"]:
                conn.execute(
                    "INSERT OR REPLACE INTO buys (id, text, completed, created_at) VALUES (?, ?, ?, ?)",
                    (b.get("id"), b.get("text", ""), 1 if b.get("completed") else 0, int(time.time() * 1000))
                )

        if "settings" in payload and isinstance(payload["settings"], dict):
            for k, v in payload["settings"].items():
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))

    conn.close()
    return jsonify({"success": True, "message": "Data synchronized successfully"})

# --- Habits REST API (Direct Replacement for Supabase) ---

@app.route("/api/habits", methods=["GET"])
def get_habits():
    conn = get_db()
    rows = conn.execute("SELECT * FROM habits WHERE active = 1 ORDER BY created_at ASC").fetchall()
    habits = [format_habit(r) for r in rows]
    conn.close()
    return jsonify(habits)

@app.route("/api/habits", methods=["POST"])
def create_habit():
    data = request.get_json(force=True, silent=True) or {}
    
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
            """INSERT INTO habits (id, name, description, routine_type, days, reminder_enabled, reminder_time, sub_items, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (habit_id, name, description, routine_type, days_str, reminder_enabled, reminder_time, sub_items_str, active, created_at)
        )
        row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
    conn.close()

    return jsonify(format_habit(row)), 201

@app.route("/api/habits/<habit_id>", methods=["GET"])
def get_habit(habit_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Habit not found"}), 404
    return jsonify(format_habit(row))

@app.route("/api/habits/<habit_id>", methods=["PUT", "PATCH"])
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
def toggle_completion():
    data = request.get_json(force=True, silent=True) or {}
    habit_id = data.get("habitId")
    date = data.get("date")
    if not habit_id or not date:
        return jsonify({"error": "habitId and date are required"}), 400

    conn = get_db()
    with conn:
        row = conn.execute("SELECT * FROM completions WHERE habit_id = ? AND date = ?", (habit_id, date)).fetchone()
        if row:
            new_val = 0 if row["completed"] else 1
            conn.execute("UPDATE completions SET completed = ?, timestamp = ? WHERE habit_id = ? AND date = ?",
                         (new_val, int(time.time() * 1000), habit_id, date))
            completed = bool(new_val)
        else:
            conn.execute("INSERT INTO completions (habit_id, date, completed, timestamp) VALUES (?, ?, 1, ?)",
                         (habit_id, date, int(time.time() * 1000)))
            completed = True
    conn.close()
    return jsonify({"habitId": habit_id, "date": date, "completed": completed})

@app.route("/api/sub-completions/toggle", methods=["POST"])
def toggle_sub_completion():
    data = request.get_json(force=True, silent=True) or {}
    habit_id = data.get("habitId")
    sub_id = data.get("subId")
    date = data.get("date")
    if not habit_id or not sub_id or not date:
        return jsonify({"error": "habitId, subId and date are required"}), 400

    conn = get_db()
    with conn:
        row = conn.execute("SELECT * FROM sub_completions WHERE habit_id = ? AND sub_id = ? AND date = ?", (habit_id, sub_id, date)).fetchone()
        if row:
            conn.execute("DELETE FROM sub_completions WHERE habit_id = ? AND sub_id = ? AND date = ?", (habit_id, sub_id, date))
            completed = False
        else:
            conn.execute("INSERT INTO sub_completions (habit_id, sub_id, date, timestamp) VALUES (?, ?, ?, ?)",
                         (habit_id, sub_id, date, int(time.time() * 1000)))
            completed = True
    conn.close()
    return jsonify({"habitId": habit_id, "subId": sub_id, "date": date, "completed": completed})

# --- Transactions API ---

@app.route("/api/transactions", methods=["GET"])
def get_transactions():
    conn = get_db()
    rows = conn.execute("SELECT * FROM transactions ORDER BY date DESC, created_at DESC").fetchall()
    conn.close()
    return jsonify([format_transaction(r) for r in rows])

@app.route("/api/transactions", methods=["POST"])
def add_transaction():
    data = request.get_json(force=True, silent=True) or {}
    tx_id = data.get("id") or f"tx-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    date = data.get("date")
    amount = float(data.get("amount", 0))
    category = data.get("category", "")
    info = data.get("information", "")
    created_at = data.get("createdAt") or int(time.time() * 1000)

    conn = get_db()
    with conn:
        conn.execute("INSERT INTO transactions (id, date, amount, category, information, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (tx_id, date, amount, category, info, created_at))
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    conn.close()
    return jsonify(format_transaction(row)), 201

@app.route("/api/transactions/<tx_id>", methods=["PUT"])
def update_transaction(tx_id):
    data = request.get_json(force=True, silent=True) or {}
    conn = get_db()
    with conn:
        conn.execute("""UPDATE transactions SET date = ?, amount = ?, category = ?, information = ?
                        WHERE id = ?""",
                     (data.get("date"), float(data.get("amount", 0)), data.get("category", ""), data.get("information", ""), tx_id))
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Transaction not found"}), 404
    return jsonify(format_transaction(row))

@app.route("/api/transactions/<tx_id>", methods=["DELETE"])
def delete_transaction(tx_id):
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    conn.close()
    return jsonify({"success": True, "id": tx_id})

# --- Settings API ---

@app.route("/api/settings", methods=["GET"])
def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    settings = dict(DEFAULT_SETTINGS)
    for r in rows:
        try:
            settings[r["key"]] = json.loads(r["value"])
        except Exception:
            settings[r["key"]] = r["value"]
    return jsonify(settings)

@app.route("/api/settings", methods=["POST", "PUT"])
def save_settings():
    data = request.get_json(force=True, silent=True) or {}
    conn = get_db()
    with conn:
        for k, v in data.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))
    conn.close()
    return jsonify({"success": True, "settings": data})

# --- 24/7 Web Push Notification Routes & Scheduler ---

@app.route("/api/push/vapid-public-key", methods=["GET"])
def get_vapid_public_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})

@app.route("/api/push/subscribe", methods=["POST"])
def subscribe_push():
    data = request.get_json(force=True, silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "Invalid subscription payload"}), 400

    conn = get_db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO push_subscriptions (endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?)",
            (endpoint, p256dh, auth, int(time.time() * 1000))
        )
    conn.close()
    return jsonify({"success": True, "message": "Device subscribed to 24/7 habit push notifications"})

def check_and_send_due_reminders():
    if not PYWEBPUSH_AVAILABLE:
        return

    now = datetime.now(USER_TZ)
    current_iso = now.strftime("%Y-%m-%d")
    current_hhmm = now.strftime("%H:%M")
    day_name = now.strftime("%a")

    conn = get_db()
    rows = conn.execute("SELECT * FROM habits WHERE active = 1 AND reminder_enabled = 1 AND reminder_time != ''").fetchall()

    for r in rows:
        habit_time = r["reminder_time"]
        if habit_time != current_hhmm:
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

        completed = conn.execute("SELECT 1 FROM completions WHERE habit_id = ? AND date = ? AND completed = 1", (r["id"], current_iso)).fetchone()
        if completed:
            continue

        subs = conn.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions").fetchall()
        if not subs:
            continue

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
            except WebPushException as ex:
                if ex.response and ex.response.status_code in [404, 410]:
                    with conn:
                        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (sub["endpoint"],))
            except Exception as e:
                print(f"[Push Notification Error]: {e}")

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