import os
import secrets
import smtplib
from email.message import EmailMessage
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


def send_temporary_password(email, temporary_password):
    gmail_username = os.environ.get("GMAIL_USERNAME")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_username or not gmail_app_password:
        raise RuntimeError("Gmail SMTP credentials are not configured")

    message = EmailMessage()
    message["Subject"] = "Media Tools - Temporary Password"
    message["From"] = gmail_username
    message["To"] = email
    message.set_content(
        f"""Hello,

Your temporary Media Tools password is:

{temporary_password}

Sign in to Media Tools using this password, then change it from your account dashboard.

If you did not request this password reset, please contact support.

Regards,
Media Tools
"""
    )

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.starttls()
        server.login(gmail_username, gmail_app_password)
        server.send_message(message)


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


@app.route("/forgot-password", methods=["GET"])
def forgot_password_page():
    return send_from_directory(STATIC_DIR, "forgot-password.html")


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

    connection = get_db()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (username, email, generate_password_hash(password)),
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


@app.route("/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json(silent=True) or request.form
    email = str(data.get("email", "")).strip().lower()

    if not email:
        return jsonify(error="Email is required"), 400

    connection = get_db()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM users WHERE LOWER(email) = %s",
                (email,),
            )
            user = cursor.fetchone()

        if user is None:
            return jsonify(
                message="If the email is registered, a temporary password has been sent."
            ), 200

        temporary_password = secrets.token_urlsafe(12)
        password_hash = generate_password_hash(temporary_password)

        # Send the email before changing the password, so a failed email
        # delivery does not lock the user out of their existing password.
        send_temporary_password(email, temporary_password)

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (password_hash, user["id"]),
            )

        connection.commit()

    except Exception:
        connection.rollback()
        app.logger.exception("Password reset failed")
        return jsonify(
            error="Unable to process the password reset. Please try again later."
        ), 500
    finally:
        connection.close()

    return jsonify(
        message="If the email is registered, a temporary password has been sent."
    ), 200


@app.route("/change-password", methods=["POST"])
def change_password():
    data = request.get_json(silent=True) or request.form

    username_or_email = str(
        data.get("username", data.get("email", ""))
    ).strip().lower()
    current_password = str(data.get("current_password", ""))
    new_password = str(data.get("new_password", ""))

    if not username_or_email or not current_password or not new_password:
        return jsonify(
            error="Username/email, current password and new password are required"
        ), 400

    if len(new_password) < 8:
        return jsonify(
            error="New password must contain at least 8 characters"
        ), 400

    connection = get_db()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, password_hash
                FROM users
                WHERE LOWER(username) = %s OR LOWER(email) = %s
                """,
                (username_or_email, username_or_email),
            )
            user = cursor.fetchone()

        if user is None or not check_password_hash(
            user["password_hash"], current_password
        ):
            return jsonify(
                error="Invalid username/email or current password"
            ), 401

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (generate_password_hash(new_password), user["id"]),
            )

        connection.commit()
    finally:
        connection.close()

    return jsonify(message="Password changed successfully"), 200


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5006")),
        debug=False,
    )
