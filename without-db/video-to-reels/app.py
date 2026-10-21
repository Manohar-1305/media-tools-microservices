
import io
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import imageio_ffmpeg
from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".mpeg", ".mpg", ".m4v",
}

ASPECT_RATIOS = {
    "9:16": (9, 16),
    "4:5": (4, 5),
    "1:1": (1, 1),
    "16:9": (16, 9),
}

PLATFORMS = {
    "youtube": "youtube-shorts",
    "instagram": "instagram-reels",
}

def get_output_dimensions(resolution, ratio):
    base = int(resolution)
    ratio_width, ratio_height = ASPECT_RATIOS[ratio]

    if ratio_width >= ratio_height:
        width = base
        height = round(base * ratio_height / ratio_width)
    else:
        height = base
        width = round(base * ratio_width / ratio_height)

    width = max(2, width - width % 2)
    height = max(2, height - height % 2)
    return width, height


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "video-to-reels"})


@app.route("/convert", methods=["POST"])
def convert():
    uploaded_file = request.files.get("video")

    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Please select a video."}), 400

    extension = Path(uploaded_file.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({
            "error": "Unsupported format. Upload MP4, MOV, AVI, MKV, WEBM, MPEG, MPG, or M4V."
        }), 400

    platform = request.form.get("platform", "youtube")
    if platform not in PLATFORMS:
        return jsonify({"error": "Select a valid platform."}), 400

    resolution = request.form.get("resolution", "1080")
    if resolution not in {"720", "1080"}:
        return jsonify({"error": "Select a valid resolution."}), 400

    ratio = request.form.get("aspect_ratio", "9:16")
    if ratio not in ASPECT_RATIOS:
        return jsonify({"error": "Select a valid aspect ratio."}), 400

    try:
        start_time = float(request.form.get("start_time", "0"))
        duration = float(request.form.get("duration", "30"))

        crop_x = float(request.form.get("crop_x", "0"))
        crop_y = float(request.form.get("crop_y", "0"))
        crop_w = float(request.form.get("crop_w", "1"))
        crop_h = float(request.form.get("crop_h", "1"))
    except (TypeError, ValueError):
        return jsonify({"error": "Enter valid numeric values."}), 400

    if start_time < 0:
        return jsonify({"error": "Start time cannot be negative."}), 400

    if duration <= 0 or duration > 180:
        return jsonify({
            "error": "Duration must be greater than 0 and no more than 180 seconds."
        }), 400

    if (
        crop_x < 0 or crop_y < 0
        or crop_w <= 0 or crop_h <= 0
        or crop_x + crop_w > 1.0001
        or crop_y + crop_h > 1.0001
    ):
        return jsonify({"error": "Invalid crop selection."}), 400

    width, height = get_output_dimensions(resolution, ratio)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    download_name = f"{PLATFORMS[platform]}-{uuid.uuid4().hex[:8]}.mp4"

    # Crop coordinates are normalized against the original video frame.
    crop_filter = (
        f"crop="
        f"trunc(iw*{crop_w:.8f}/2)*2:"
        f"trunc(ih*{crop_h:.8f}/2)*2:"
        f"trunc(iw*{crop_x:.8f}/2)*2:"
        f"trunc(ih*{crop_y:.8f}/2)*2,"
        f"scale={width}:{height}:flags=lanczos,"
        "setsar=1,fps=30"
    )

    try:
        with tempfile.TemporaryDirectory(prefix="video_to_reels_") as temp_dir:
            input_path = os.path.join(temp_dir, f"input{extension}")
            output_path = os.path.join(temp_dir, "output.mp4")

            uploaded_file.save(input_path)

            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-ss", str(start_time),
                "-i", input_path,
                "-t", str(duration),
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-vf", crop_filter,
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "44100",
                "-movflags", "+faststart",
                "-sn",
                output_path,
            ]

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )

            if result.returncode != 0 or not os.path.isfile(output_path):
                app.logger.error("FFmpeg error: %s", result.stderr)
                return jsonify({
                    "error": "Could not convert this video. Check that the file is valid and try again."
                }), 422

            with open(output_path, "rb") as output_file:
                video_data = io.BytesIO(output_file.read())

            video_data.seek(0)

        return send_file(
            video_data,
            mimetype="video/mp4",
            as_attachment=True,
            download_name=download_name,
            max_age=0,
        )

    except subprocess.TimeoutExpired:
        return jsonify({
            "error": "Conversion timed out. Try a shorter video."
        }), 408
    except Exception:
        app.logger.exception("Video-to-reels conversion failed")
        return jsonify({
            "error": "An unexpected error occurred during conversion."
        }), 500


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({
        "error": "File is too large. Maximum upload size is 500 MB."
    }), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5015, debug=False)
