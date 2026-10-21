import os
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = Flask(__name__, static_folder=None)
app.config.from_object(Config)


def get_db():
    return pymysql.connect(
        host=app.config["DB_HOST"],
        port=app.config["DB_PORT"],
        user=app.config["DB_USER"],
        password=app.config["DB_PASSWORD"],
        database=app.config["DB_NAME"],
        cursorclass=DictCursor,
        autocommit=False,
    )


def initialize_database():
    connection = get_db()

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(255) NOT NULL UNIQUE,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        connection.commit()
    finally:
        connection.close()


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
    connection = get_db()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (username, email, password_hash),
            )
        connection.commit()
    except pymysql.err.IntegrityError:
        connection.rollback()
        return jsonify(error="Username or email already exists"), 409
    finally:
        connection.close()

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

    connection = get_db()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, username, email, password_hash
                FROM users
                WHERE LOWER(username) = %s OR LOWER(email) = %s
                """,
                (username_or_email, username_or_email),
            )
            user = cursor.fetchone()
    finally:
        connection.close()

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
