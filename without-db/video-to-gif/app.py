import io
import os
import subprocess
import tempfile

import imageio_ffmpeg
from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".mpeg",
    ".mpg",
}

ALLOWED_FPS = {5, 8, 10, 12, 15}
ALLOWED_WIDTHS = {240, 320, 480, 640}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "healthy"}), 200


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({"error": "The uploaded video exceeds 100 MB."}), 413


@app.post("/convert")
def convert():
    uploaded_file = request.files.get("file")

    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Please select a video file."}), 400

    original_name = os.path.basename(uploaded_file.filename)
    extension = os.path.splitext(original_name)[1].lower()

    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Unsupported video format."}), 400

    try:
        start = float(request.form.get("start", "0"))
        duration = float(request.form.get("duration", "5"))
        fps = int(request.form.get("fps", "10"))
        width = int(request.form.get("width", "480"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid conversion settings."}), 400

    if start < 0 or start > 36000:
        return jsonify({"error": "Start time must be between 0 and 36000 seconds."}), 400

    if duration < 0.1 or duration > 30:
        return jsonify({"error": "Duration must be between 0.1 and 30 seconds."}), 400

    if fps not in ALLOWED_FPS:
        return jsonify({"error": "Select a supported frame rate."}), 400

    if width not in ALLOWED_WIDTHS:
        return jsonify({"error": "Select a supported GIF width."}), 400

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    try:
        with tempfile.TemporaryDirectory(prefix="video_to_gif_") as temp_dir:
            input_path = os.path.join(temp_dir, f"input{extension}")
            output_path = os.path.join(temp_dir, "output.gif")
            palette_path = os.path.join(temp_dir, "palette.png")

            uploaded_file.save(input_path)

            palette_filter = (
                f"fps={fps},scale={width}:-1:flags=lanczos,"
                "palettegen=stats_mode=diff"
            )

            palette_command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-ss", str(start),
                "-t", str(duration),
                "-i", input_path,
                "-vf", palette_filter,
                "-frames:v", "1",
                palette_path,
            ]

            palette_result = subprocess.run(
                palette_command,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )

            if palette_result.returncode != 0 or not os.path.isfile(palette_path):
                app.logger.error("FFmpeg palette generation failed: %s", palette_result.stderr)
                return jsonify({
                    "error": "Could not process this video. Check that the video is valid and try again."
                }), 422

            gif_filter = (
                f"fps={fps},scale={width}:-1:flags=lanczos"
                "[video];[video][1:v]paletteuse=dither=bayer:bayer_scale=5"
            )

            conversion_command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-ss", str(start),
                "-t", str(duration),
                "-i", input_path,
                "-i", palette_path,
                "-filter_complex", gif_filter,
                "-loop", "0",
                output_path,
            ]

            conversion_result = subprocess.run(
                conversion_command,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )

            if conversion_result.returncode != 0 or not os.path.isfile(output_path):
                app.logger.error("FFmpeg GIF conversion failed: %s", conversion_result.stderr)
                return jsonify({
                    "error": "GIF conversion failed. Try another video or lower the output width."
                }), 422

            # Read the GIF into memory before the temporary directory is removed.
            with open(output_path, "rb") as gif_file:
                gif_data = io.BytesIO(gif_file.read())

        # Temporary files are cleaned up here. The response uses memory, not the file.
        gif_data.seek(0)

        download_name = os.path.splitext(original_name)[0] + ".gif"

        response = send_file(
            gif_data,
            mimetype="image/gif",
            as_attachment=True,
            download_name=download_name,
            max_age=0,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Conversion timed out. Try a shorter clip or smaller output width."}), 408
    except Exception:
        app.logger.exception("Unexpected error during video-to-GIF conversion")
        return jsonify({"error": "An unexpected error occurred during conversion."}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5014, debug=False)