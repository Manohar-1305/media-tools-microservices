import os
import uuid
import subprocess
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, send_file
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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


@app.route("/cut", methods=["POST"])
def cut_audio():
    uploaded_file = request.files.get("file")
    start_time = request.form.get("start")
    end_time = request.form.get("end")

    if not uploaded_file or not uploaded_file.filename:
        return jsonify(error="Audio file is required"), 400

    if not allowed_file(uploaded_file.filename):
        return jsonify(error="Unsupported audio format"), 400

    try:
        start = float(start_time)
        end = float(end_time)
    except (TypeError, ValueError):
        return jsonify(error="Valid start and end times are required"), 400

    if start < 0 or end <= start:
        return jsonify(error="End time must be greater than start time"), 400

    filename = secure_filename(uploaded_file.filename)
    extension = Path(filename).suffix.lower()
    file_id = uuid.uuid4().hex

    input_path = UPLOAD_DIR / f"{file_id}{extension}"
    output_path = OUTPUT_DIR / f"{file_id}_cut{extension}"

    uploaded_file.save(input_path)

    try:
        command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-ss", str(start),
            "-i", str(input_path),
            "-t", str(end - start),
            "-vn",
            "-c:a", "libmp3lame" if extension == ".mp3" else "aac",
            str(output_path),
        ]

        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )

        return send_file(
            output_path,
            as_attachment=True,
            download_name=f"{Path(filename).stem}_cut{extension}",
        )

    except subprocess.TimeoutExpired:
        return jsonify(error="Audio processing timed out"), 504

    except subprocess.CalledProcessError:
        app.logger.exception("FFmpeg failed to cut audio")
        return jsonify(error="Unable to process the audio file"), 500

    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5001")),
        debug=False,
    )
