import os
import re
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

MAX_VIDEO_SIZE = 100 * 1024 * 1024
MAX_SUBTITLE_SIZE = 5 * 1024 * 1024

app.config["MAX_CONTENT_LENGTH"] = (
    MAX_VIDEO_SIZE + MAX_SUBTITLE_SIZE + 2 * 1024 * 1024
)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
SUBTITLE_EXTENSIONS = {".srt", ".txt"}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify(
        status="healthy",
        ffmpeg=shutil.which("ffmpeg") is not None,
        ffprobe=shutil.which("ffprobe") is not None,
    )


def timestamp_to_seconds(value):
    match = re.fullmatch(
        r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{1,3})",
        value.strip(),
    )

    if not match:
        raise ValueError("Invalid subtitle timestamp: " + value)

    hours, minutes, seconds, milliseconds = match.groups()

    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds.ljust(3, "0")) / 1000
    )


def validate_srt(content):
    content = content.decode("utf-8-sig", errors="replace")
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()

    if not content:
        raise ValueError("Subtitle file is empty.")

    timestamp_pattern = re.compile(
        r"(?m)^\s*(\d{2,}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
        r"(\d{2,}:\d{2}:\d{2}[,.]\d{1,3})(?:\s+.*)?$"
    )

    matches = list(timestamp_pattern.finditer(content))

    if not matches:
        raise ValueError(
            "No valid SRT timestamps found. Import subtitles with valid SRT timestamps."
        )

    for match in matches:
        start = timestamp_to_seconds(match.group(1))
        end = timestamp_to_seconds(match.group(2))

        if end <= start:
            raise ValueError("Subtitle end time must be after start time.")

    return content + "\n"


def get_video_duration(video_path):
    ffprobe = shutil.which("ffprobe")

    if not ffprobe:
        app.logger.warning("ffprobe is not installed; skipping duration validation")
        return None

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        if result.returncode != 0:
            app.logger.error("ffprobe failed: %s", result.stderr[-4000:])
            return None

        return float(result.stdout.strip())

    except (ValueError, TypeError, subprocess.TimeoutExpired):
        app.logger.exception("Could not determine video duration")
        return None


def build_ffmpeg_subtitle_filter(subtitle_path):
    path = str(subtitle_path.resolve()).replace("\\", "/")
    path = path.replace(":", r"\:")
    path = path.replace("'", r"\'")
    path = path.replace("[", r"\[").replace("]", r"\]")
    return f"subtitles=filename='{path}'"


@app.post("/convert")
def convert():
    app.logger.info("Received video subtitle export request")

    ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        app.logger.error("FFmpeg is not installed or is not in PATH")
        return jsonify(error="FFmpeg is not installed or is not in PATH."), 500

    video_file = request.files.get("video") or request.files.get("file")
    subtitle_file = request.files.get("subtitle") or request.files.get("srt")

    if not video_file or not video_file.filename:
        return jsonify(error="Upload a video file."), 400

    if not subtitle_file or not subtitle_file.filename:
        return jsonify(error="Upload an SRT subtitle file."), 400

    video_ext = Path(video_file.filename).suffix.lower()
    subtitle_ext = Path(subtitle_file.filename).suffix.lower()

    if video_ext not in VIDEO_EXTENSIONS:
        return jsonify(error="Unsupported video format."), 400

    if subtitle_ext not in SUBTITLE_EXTENSIONS:
        return jsonify(error="Subtitle must be an SRT or TXT file."), 400

    output_format = request.form.get("output_format", "mp4").lower()

    if output_format not in {"mp4", "mkv"}:
        return jsonify(error="Output format must be MP4 or MKV."), 400

    video_data = video_file.read(MAX_VIDEO_SIZE + 1)

    if len(video_data) > MAX_VIDEO_SIZE:
        return jsonify(error="Video cannot exceed 100 MB."), 413

    subtitle_data = subtitle_file.read(MAX_SUBTITLE_SIZE + 1)

    if len(subtitle_data) > MAX_SUBTITLE_SIZE:
        return jsonify(error="Subtitle file cannot exceed 5 MB."), 413

    try:
        subtitle_text = validate_srt(subtitle_data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    temp_dir = tempfile.TemporaryDirectory(prefix="video_subtitle_")

    try:
        temp_path = Path(temp_dir.name)
        input_path = temp_path / ("input" + video_ext)
        subtitle_path = temp_path / "subtitles.srt"
        output_path = temp_path / ("subtitled." + output_format)

        input_path.write_bytes(video_data)
        subtitle_path.write_text(subtitle_text, encoding="utf-8")

        duration = get_video_duration(input_path)

        if duration is not None:
            if duration <= 0:
                temp_dir.cleanup()
                return jsonify(
                    error="The uploaded video has an invalid duration."
                ), 400

            timestamp_pattern = re.compile(
                r"(?m)^\s*(\d{2,}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
                r"(\d{2,}:\d{2}:\d{2}[,.]\d{1,3})"
            )

            for match in timestamp_pattern.finditer(subtitle_text):
                start = timestamp_to_seconds(match.group(1))
                end = timestamp_to_seconds(match.group(2))

                if start >= duration + 1 or end > duration + 1:
                    temp_dir.cleanup()
                    return jsonify(
                        error="A subtitle timestamp extends beyond the video duration."
                    ), 400

        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(input_path),
            "-vf", build_ffmpeg_subtitle_filter(subtitle_path),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
        ]

        if output_format == "mp4":
            command.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart"])
        else:
            command.extend(["-f", "matroska"])

        command.append(str(output_path))

        app.logger.info("Starting FFmpeg video subtitle conversion")

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )

        if result.returncode != 0 or not output_path.exists():
            app.logger.error("FFmpeg failed: %s", result.stderr[-10000:])
            temp_dir.cleanup()

            detail = result.stderr.strip().splitlines()
            reason = detail[-1] if detail else "Unknown FFmpeg error."

            return jsonify(error="Video conversion failed: " + reason), 500

        if output_path.stat().st_size == 0:
            temp_dir.cleanup()
            return jsonify(
                error="FFmpeg generated an empty output file."
            ), 500

        response = send_file(
            output_path,
            mimetype=(
                "video/mp4"
                if output_format == "mp4"
                else "video/x-matroska"
            ),
            as_attachment=True,
            download_name="subtitled." + output_format,
        )

        response.call_on_close(temp_dir.cleanup)

        app.logger.info(
            "Video subtitle conversion succeeded; output size=%s bytes",
            output_path.stat().st_size,
        )

        return response

    except subprocess.TimeoutExpired:
        app.logger.exception("FFmpeg conversion timed out")
        temp_dir.cleanup()
        return jsonify(
            error="Video conversion timed out. Try a shorter video."
        ), 504

    except Exception:
        app.logger.exception("Video subtitle conversion failed")
        temp_dir.cleanup()
        return jsonify(
            error="Server failed while converting the video. Check service logs."
        ), 500


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(
        error="Request is too large. Video limit is 100 MB and subtitle limit is 5 MB."
    ), 413


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5010")),
        debug=False,
        threaded=True,
    )
