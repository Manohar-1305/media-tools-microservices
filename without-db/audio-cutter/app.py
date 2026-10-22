import os
import subprocess
from pathlib import Path
import hashlib
import json
import os
import redis

redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    db=int(os.getenv("REDIS_DB", "0")),
    password=os.getenv("REDIS_PASSWORD") or None,
    decode_responses=False,
    socket_connect_timeout=3,
    socket_timeout=5,
    health_check_interval=30,
)

CACHE_TTL = int(os.getenv("REDIS_CACHE_TTL", "3600"))
CACHE_MAX_BYTES = int(os.getenv("REDIS_CACHE_MAX_BYTES", "5242880"))

from flask import Flask, jsonify, request, send_from_directory, Response
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac", ".wma"
}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>", methods=["GET"])
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/health", methods=["GET"])
def health():
    return jsonify(service="audio-cutter", status="ok"), 200


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify(error="Audio file exceeds the 500 MB upload limit"), 413


@app.route("/cut", methods=["POST"])
def cut_audio():
    uploaded_file = request.files.get("file") or request.files.get("audio")

    if uploaded_file is None or not uploaded_file.filename:
        return jsonify(error="Audio file is required"), 400

    if not allowed_file(uploaded_file.filename):
        return jsonify(error="Unsupported audio format"), 400

    try:
        start = float(request.form.get("start", ""))
        end = float(request.form.get("end", ""))
    except (TypeError, ValueError):
        return jsonify(error="Valid start and end times are required"), 400

    if start < 0 or end <= start:
        return jsonify(error="End time must be greater than start time"), 400

    filename = secure_filename(uploaded_file.filename)
    extension = Path(filename).suffix.lower()

    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-ss", str(start),
        "-i", "pipe:0",
        "-t", str(end - start),
        "-vn",
    ]

    output_format = extension.lstrip(".")

    if extension == ".mp3":
        command.extend(["-c:a", "libmp3lame", "-f", "mp3"])
    elif extension == ".wav":
        command.extend(["-c:a", "pcm_s16le", "-f", "wav"])
    elif extension == ".ogg":
        command.extend(["-c:a", "libvorbis", "-f", "ogg"])
    elif extension == ".flac":
        command.extend(["-c:a", "flac", "-f", "flac"])
    elif extension == ".wma":
        command.extend(["-c:a", "wmav2", "-f", "asf"])
    elif extension == ".aac":
        command.extend(["-c:a", "aac", "-f", "adts"])
    else:
        command.extend(["-c:a", "aac", "-f", "ipod"])

    command.append("pipe:1")

    try:
        uploaded_file.stream.seek(0)

        result = subprocess.run(
            command,
            input=uploaded_file.stream.read(),
            capture_output=True,
            timeout=600,
        )

        if result.returncode != 0:
            app.logger.error(
                "FFmpeg failed: %s",
                result.stderr.decode(errors="replace"),
            )
            return jsonify(error="Unable to process the audio file"), 500

        if not result.stdout:
            return jsonify(error="Audio output was not created"), 500

        response = Response(
            result.stdout,
            status=200,
            mimetype="application/octet-stream",
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{Path(filename).stem}_cut{extension}"'
        )
        response.headers["Content-Length"] = str(len(result.stdout))
        return response

    except subprocess.TimeoutExpired:
        return jsonify(error="Audio processing timed out"), 504

    except OSError:
        app.logger.exception("Unable to process audio file")
        return jsonify(error="Unable to process audio file"), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5001")),
        debug=False,
    )
