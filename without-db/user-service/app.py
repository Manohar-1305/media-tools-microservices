import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATABASE = BASE_DIR / "users.db"

app = Flask(__name__, static_folder=None)


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_db() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


initialize_database()


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>", methods=["GET"])
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/health", methods=["GET"])
def health():
    return jsonify(service="user", status="ok"), 200


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or request.form

    username = str(data.get("username", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not username or not email or not password:
        return jsonify(error="Username, email and password are required"), 400

    if len(password) < 8:
        return jsonify(error="Password must contain at least 8 characters"), 400

    password_hash = generate_password_hash(password)

    try:
        with get_db() as connection:
            connection.execute(
                """
                INSERT INTO users (username, email, password_hash)
                VALUES (?, ?, ?)
                """,
                (username, email, password_hash),
            )
    except sqlite3.IntegrityError:
        return jsonify(error="Username or email already exists"), 409

    return jsonify(message="Account created successfully"), 201


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or request.form

    username_or_email = str(
        data.get("username", data.get("email", ""))
    ).strip().lower()
    password = str(data.get("password", ""))

    if not username_or_email or not password:
        return jsonify(error="Username/email and password are required"), 400

    with get_db() as connection:
        user = connection.execute(
            """
            SELECT id, username, email, password_hash
            FROM users
            WHERE lower(username) = ? OR lower(email) = ?
            """,
            (username_or_email, username_or_email),
        ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify(error="Invalid username/email or password"), 401

    return jsonify(
        message="Login successful",
        user={
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
        },
    ), 200


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5006")),
        debug=False,
    )
