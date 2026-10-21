
import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_DURATION = 3600

ALLOWED_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",
    ".mpeg", ".mpg", ".3gp", ".ts"
}

jobs = {}
jobs_lock = threading.Lock()


@app.after_request
def add_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Video exceeds the 500 MB upload limit."}), 413


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def run_command(command, timeout=60):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "Command failed").strip()[-3000:]
        )
    return result.stdout.strip()


def probe_duration(path):
    result = run_command([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(path)
    ])
    duration = float(json.loads(result)["format"]["duration"])
    if duration <= 0:
        raise ValueError("Unable to determine the video duration.")
    return duration


def number(value, default, minimum=None, maximum=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default

    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def build_command(source, destination, settings, duration):
    start = number(settings.get("start"), 0, 0, duration)
    end = number(settings.get("end"), duration, 0, duration)

    if end <= start:
        raise ValueError("End time must be greater than start time.")

    clip_duration = end - start
    if clip_duration > MAX_DURATION:
        raise ValueError("The selected section cannot exceed 1 hour.")

    speed = number(settings.get("speed"), 1, 0.25, 4)
    volume = number(settings.get("volume"), 1, 0, 3)
    rotation = str(settings.get("rotation", "0"))
    selected_filter = str(settings.get("filter", "none"))

    video_filters = []
    audio_filters = []

    if rotation == "90":
        video_filters.append("transpose=1")
    elif rotation == "180":
        video_filters.extend(["hflip", "vflip"])
    elif rotation == "270":
        video_filters.append("transpose=2")

    filter_map = {
        "none": None,
        "grayscale": "hue=s=0",
        "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
        "blur": "boxblur=2:1",
        "sharpen": "unsharp=5:5:0.8:3:3:0.4"
    }

    video_filter = filter_map.get(selected_filter)
    if video_filter:
        video_filters.append(video_filter)

    if speed != 1:
        video_filters.append(f"setpts={1 / speed:.8f}*PTS")
        remaining_speed = speed
        while remaining_speed > 2:
            audio_filters.append("atempo=2.0")
            remaining_speed /= 2
        while remaining_speed < 0.5:
            audio_filters.append("atempo=0.5")
            remaining_speed /= 0.5
        if abs(remaining_speed - 1) > 0.0001:
            audio_filters.append(f"atempo={remaining_speed:.8f}")

    if volume != 1:
        audio_filters.append(f"volume={volume}")

    overlay_text = str(settings.get("overlay_text", "")).strip()[:200]
    if overlay_text:
        safe_text = (
            overlay_text.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
            .replace("%", "\\%")
            .replace(",", "\\,")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )
        video_filters.append(
            f"drawtext=text='{safe_text}':"
            "x=(w-text_w)/2:y=h-text_h-30:"
            "fontsize=28:fontcolor=white:"
            "borderw=2:bordercolor=black"
        )

    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-ss", str(start),
        "-i", str(source),
        "-t", str(clip_duration),
        "-map", "0:v:0",
        "-map", "0:a?"
    ]

    if video_filters:
        command.extend(["-vf", ",".join(video_filters)])

    if audio_filters:
        command.extend(["-af", ",".join(audio_filters)])

    command.extend([
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        str(destination)
    ])

    return command, clip_duration


def update_job(job_id, **values):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(values)


def process_job(job_id, source, destination, work_dir, settings, duration):
    started = time.monotonic()
    process = None

    try:
        command, clip_duration = build_command(
            source, destination, settings, duration
        )

        update_job(
            job_id,
            state="processing",
            progress=0,
            message="Preparing video...",
            elapsed_seconds=0,
            estimated_seconds=max(5, int(clip_duration * 0.5))
        )

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        stderr_lines = []

        def collect_stderr():
            for line in process.stderr:
                stderr_lines.append(line)
                if len(stderr_lines) > 100:
                    del stderr_lines[:20]

        stderr_thread = threading.Thread(target=collect_stderr, daemon=True)
        stderr_thread.start()

        output_seconds = 0.0
        last_update = 0.0

        for line in process.stdout:
            line = line.strip()

            if line.startswith("out_time_ms="):
                try:
                    output_seconds = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    pass
            elif line.startswith("out_time="):
                try:
                    parts = line.split("=", 1)[1].split(":")
                    output_seconds = (
                        float(parts[0]) * 3600
                        + float(parts[1]) * 60
                        + float(parts[2])
                    )
                except (ValueError, IndexError):
                    pass
            elif line == "progress=end":
                output_seconds = clip_duration

            now = time.monotonic()
            elapsed = now - started

            if now - last_update >= 0.5:
                last_update = now
                progress = min(99, int(output_seconds / clip_duration * 100))

                if output_seconds > 0:
                    rate = output_seconds / max(elapsed, 0.1)
                    remaining = max(
                        1,
                        int((clip_duration - output_seconds) / max(rate, 0.01))
                    )
                else:
                    remaining = max(5, int(clip_duration * 0.5))

                update_job(
                    job_id,
                    progress=progress,
                    message="Processing video...",
                    elapsed_seconds=int(elapsed),
                    estimated_seconds=remaining
                )

        return_code = process.wait()
        stderr_thread.join(timeout=2)

        if return_code != 0:
            details = "".join(stderr_lines).strip()
            raise RuntimeError(
                details[-2500:] or "FFmpeg failed to process the video."
            )

        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not produce a valid video.")

        update_job(
            job_id,
            state="completed",
            progress=100,
            message="Video ready to download.",
            elapsed_seconds=int(time.monotonic() - started),
            estimated_seconds=0,
            download_url=f"/download/{job_id}"
        )

    except Exception as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()

        shutil.rmtree(work_dir, ignore_errors=True)

        update_job(
            job_id,
            state="failed",
            message="Video processing failed.",
            error=str(exc),
            estimated_seconds=0
        )


@app.route("/cut", methods=["POST", "OPTIONS"])
def cut():
    if request.method == "OPTIONS":
        return "", 204

    uploaded = request.files.get("file") or request.files.get("video")

    if not uploaded or not uploaded.filename:
        return jsonify({"error": "No video file was uploaded."}), 400

    extension = Path(uploaded.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Unsupported video format."}), 400

    job_id = uuid.uuid4().hex
    safe_name = secure_filename(uploaded.filename) or "video"

    work_dir = tempfile.mkdtemp(prefix="video-cutter-")
    work_path = Path(work_dir)
    source = work_path / safe_name
    destination = work_path / "cut_video.mp4"

    try:
        uploaded.save(source)
        duration = probe_duration(source)

        if duration > MAX_DURATION:
            shutil.rmtree(work_dir, ignore_errors=True)
            return jsonify({
                "error": "This video is longer than 1 hour. Please upload a video that is 1 hour or shorter."
            }), 400

        settings = {
            "start": request.form.get("start", "0"),
            "end": request.form.get("end", str(duration)),
            "speed": request.form.get("speed", "1"),
            "volume": request.form.get("volume", "1"),
            "rotation": request.form.get("rotation", "0"),
            "filter": request.form.get("filter", "none"),
            "overlay_text": request.form.get("overlay_text", "")
        }

        start = number(settings["start"], 0, 0, duration)
        end = number(settings["end"], duration, 0, duration)

        if end <= start or end - start > MAX_DURATION:
            shutil.rmtree(work_dir, ignore_errors=True)
            return jsonify({
                "error": "Select a valid section that is no longer than 1 hour."
            }), 400

        with jobs_lock:
            jobs[job_id] = {
                "state": "queued",
                "progress": 0,
                "message": "Upload complete. Starting processing...",
                "elapsed_seconds": 0,
                "estimated_seconds": max(5, int((end - start) * 0.5)),
                "error": None,
                "download_url": None,
                "work_dir": work_dir
            }

        threading.Thread(
            target=process_job,
            args=(job_id, source, destination, work_dir, settings, duration),
            daemon=True
        ).start()

        return jsonify({
            "job_id": job_id,
            "state": "queued",
            "status_url": f"/status/{job_id}"
        }), 202

    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({"error": f"Unable to start video processing: {exc}"}), 400


@app.route("/status/<job_id>", methods=["GET"])
def job_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({"error": "Job not found or expired."}), 404

    result = {key: value for key, value in job.items() if key != "work_dir"}
    return jsonify(result)


@app.route("/download/<job_id>", methods=["GET"])
def download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job or job.get("state") != "completed":
        return jsonify({"error": "The video is not ready to download."}), 404

    work_dir = job.get("work_dir")
    output_path = Path(work_dir) / "cut_video.mp4"

    if not output_path.is_file():
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({"error": "The temporary output video no longer exists."}), 404

    response = send_file(
        output_path,
        as_attachment=True,
        download_name="cut_video.mp4",
        mimetype="video/mp4"
    )

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        with jobs_lock:
            jobs.pop(job_id, None)

    response.call_on_close(cleanup)
    return response


if __name__ == "__main__":
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("FFmpeg and ffprobe must be installed and available on PATH.")

    app.run(host="0.0.0.0", port=5003, threaded=True)
