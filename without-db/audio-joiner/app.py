import os
import io
import subprocess
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac", ".wma"
}

INPUT_FORMATS = {
    ".mp3": "mp3",
    ".wav": "wav",
    ".aac": "aac",
    ".m4a": "mov",
    ".ogg": "ogg",
    ".flac": "flac",
    ".wma": "asf",
}

OUTPUT_FORMATS = {
    "mp3": ("libmp3lame", "mp3"),
    "wav": ("pcm_s16le", "wav"),
    "aac": ("aac", "adts"),
    "m4a": ("aac", "ipod"),
    "ogg": ("libvorbis", "ogg"),
    "flac": ("flac", "flac"),
    "wma": ("wmav2", "asf"),
}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def run_ffmpeg(command, input_data, timeout=600):
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        output, error = process.communicate(
            input=input_data,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise

    if process.returncode != 0:
        app.logger.error(
            "FFmpeg failed: %s",
            error.decode(errors="replace"),
        )
        raise RuntimeError("FFmpeg processing failed")

    if not output:
        raise RuntimeError("FFmpeg produced no output")

    return output


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>", methods=["GET"])
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/health", methods=["GET"])
def health():
    return jsonify(service="audio-joiner", status="ok"), 200


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify(error="Audio files exceed the 500 MB upload limit"), 413


@app.route("/join", methods=["POST"])
def join_audio():
    uploaded_files = request.files.getlist("files")

    if len(uploaded_files) < 2:
        return jsonify(error="Select at least two audio files"), 400

    audio_files = []

    for uploaded_file in uploaded_files:
        if not uploaded_file or not uploaded_file.filename:
            return jsonify(error="One or more audio files are missing"), 400

        if not allowed_file(uploaded_file.filename):
            return jsonify(
                error=f"Unsupported audio format: {uploaded_file.filename}"
            ), 400

        filename = secure_filename(uploaded_file.filename)
        data = uploaded_file.read()

        if not data:
            return jsonify(
                error=f"Audio file is empty: {filename}"
            ), 400

        audio_files.append({
            "filename": filename,
            "extension": Path(filename).suffix.lower(),
            "data": data,
        })

    output_format = request.form.get(
        "format", "mp3"
    ).lower().lstrip(".")

    if output_format not in OUTPUT_FORMATS:
        return jsonify(error="Unsupported output format"), 400

    codec, muxer = OUTPUT_FORMATS[output_format]

    try:
        # Decode each uploaded file to a common raw PCM format in memory.
        # This avoids incorrectly treating multiple uploads as separate
        # FFmpeg pipe inputs when subprocess.communicate provides only stdin.
        pcm_chunks = []

        for audio_file in audio_files:
            decode_command = [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel", "error",
                "-i", "pipe:0",
                "-map", "0:a:0",
                "-vn",
                "-ac", "2",
                "-ar", "44100",
                "-f", "s16le",
                "pipe:1",
            ]

            pcm = run_ffmpeg(
                decode_command,
                audio_file["data"],
            )
            pcm_chunks.append(pcm)

        # Concatenate decoded PCM samples in the selected upload order.
        combined_pcm = b"".join(pcm_chunks)

        if not combined_pcm:
            return jsonify(error="No audio data was decoded"), 400

        # Encode the combined PCM stream to the requested output format.
        encode_command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "s16le",
            "-ar", "44100",
            "-ac", "2",
            "-i", "pipe:0",
            "-map", "0:a:0",
            "-vn",
            "-c:a", codec,
        ]

        if output_format == "mp3":
            encode_command.extend(["-q:a", "2"])
        elif output_format == "ogg":
            encode_command.extend(["-q:a", "5"])
        elif output_format == "m4a":
            encode_command.extend(["-movflags", "+faststart"])

        encode_command.extend([
            "-f", muxer,
            "pipe:1",
        ])

        output = run_ffmpeg(
            encode_command,
            combined_pcm,
        )

        response = Response(
            output,
            status=200,
            mimetype="application/octet-stream",
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="joined_audio.{output_format}"'
        )
        response.headers["Content-Length"] = str(len(output))
        return response

    except subprocess.TimeoutExpired:
        return jsonify(error="Audio joining timed out"), 504

    except RuntimeError:
        return jsonify(error="Unable to join the audio files"), 500

    except OSError:
        app.logger.exception("Unable to execute FFmpeg")
        return jsonify(error="Unable to join the audio files"), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5002")),
        debug=False,
    )
