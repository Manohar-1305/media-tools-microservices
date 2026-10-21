
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

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac", ".wma", ".opus"
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

DURATIONS = {
    "default": None,
    "1": 60,
    "5": 300,
    "10": 600,
    "30": 1800,
    "60": 3600,
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
def file_too_large(_error):
    return jsonify({"error": "Files must not exceed 500 MB."}), 413


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


def run_command(command, timeout=60):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "Command failed").strip()[-2500:]
        )
    return result.stdout.strip()


def probe_duration(path):
    output = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ])
    duration = float(json.loads(output)["format"]["duration"])
    if duration <= 0:
        raise ValueError("Could not determine the audio duration.")
    return duration


def update_job(job_id, **values):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(values)


def process_job(job_id, work_dir, audio_path, image_path, output_path,
                requested_duration, audio_duration):
    process = None
    started = time.monotonic()

    try:
        if requested_duration is None:
            output_duration = audio_duration
        else:
            output_duration = requested_duration

        if output_duration <= 0 or output_duration > 3600:
            raise ValueError("Output duration must be between 1 second and 1 hour.")

        if image_path:
            command = [
                "ffmpeg", "-hide_banner", "-nostdin", "-y",
                "-loop", "1",
                "-framerate", "25",
                "-i", str(image_path),
            ]
        else:
            command = [
                "ffmpeg", "-hide_banner", "-nostdin", "-y",
                "-f", "lavfi",
                "-i", "color=c=black:s=1280x720:r=25",
            ]

        if output_duration > audio_duration:
            command.extend(["-stream_loop", "-1"])

        command.extend(["-i", str(audio_path)])

        command.extend([
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-t", str(output_duration),
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                   "pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-r", "25",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-progress", "pipe:1",
            "-nostats",
            str(output_path),
        ])

        update_job(
            job_id,
            state="processing",
            message="Converting audio to video...",
            progress=0,
            elapsed_seconds=0,
            estimated_seconds=max(5, int(output_duration * 0.3)),
            output_duration=output_duration,
        )

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
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
                output_seconds = output_duration

            now = time.monotonic()
            elapsed = now - started

            if now - last_update >= 0.5:
                last_update = now
                progress = min(
                    99,
                    int(output_seconds / output_duration * 100),
                )

                if output_seconds > 0:
                    rate = output_seconds / max(elapsed, 0.1)
                    remaining = max(
                        1,
                        int(
                            (output_duration - output_seconds)
                            / max(rate, 0.01)
                        ),
                    )
                else:
                    remaining = max(5, int(output_duration * 0.3))

                update_job(
                    job_id,
                    progress=progress,
                    message="Converting audio to video...",
                    elapsed_seconds=int(elapsed),
                    estimated_seconds=remaining,
                )

        return_code = process.wait()
        stderr_thread.join(timeout=2)

        if return_code != 0:
            raise RuntimeError(
                "".join(stderr_lines).strip()[-2500:]
                or "FFmpeg conversion failed."
            )

        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("The output video was not created.")

        update_job(
            job_id,
            state="completed",
            message="Video is ready to download.",
            progress=100,
            elapsed_seconds=int(time.monotonic() - started),
            estimated_seconds=0,
            download_url=f"/download/{job_id}",
        )

    except Exception as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()

        shutil.rmtree(work_dir, ignore_errors=True)

        update_job(
            job_id,
            state="failed",
            message="Conversion failed.",
            error=str(exc),
            estimated_seconds=0,
        )


@app.route("/convert", methods=["POST", "OPTIONS"])
def convert():
    if request.method == "OPTIONS":
        return "", 204

    audio = request.files.get("audio")
    image = request.files.get("image")
    duration_choice = request.form.get("duration", "default")

    if not audio or not audio.filename:
        return jsonify({"error": "Choose an audio file first."}), 400

    audio_extension = Path(audio.filename).suffix.lower()
    if audio_extension not in AUDIO_EXTENSIONS:
        return jsonify({
            "error": "Unsupported audio format. Use MP3, WAV, AAC, M4A, OGG, FLAC, WMA, or OPUS."
        }), 400

    if duration_choice not in DURATIONS:
        return jsonify({"error": "Invalid duration selection."}), 400

    if image and image.filename:
        image_extension = Path(image.filename).suffix.lower()
        if image_extension not in IMAGE_EXTENSIONS:
            return jsonify({
                "error": "Unsupported image format. Use PNG, JPG, JPEG, WEBP, or BMP."
            }), 400

    work_dir = tempfile.mkdtemp(prefix="audio-to-video-")
    work_path = Path(work_dir)
    audio_path = work_path / (
        "audio" + audio_extension
    )
    image_path = None
    output_path = work_path / "audio_video.mp4"

    try:
        audio.save(audio_path)

        if image and image.filename:
            image_path = work_path / (
                "background" + Path(image.filename).suffix.lower()
            )
            image.save(image_path)

        audio_duration = probe_duration(audio_path)

        if audio_duration > 3600:
            shutil.rmtree(work_dir, ignore_errors=True)
            return jsonify({
                "error": "Audio is longer than 1 hour. Choose an audio file that is 1 hour or shorter."
            }), 400

        requested_duration = DURATIONS[duration_choice]

        if requested_duration is not None and requested_duration > 3600:
            shutil.rmtree(work_dir, ignore_errors=True)
            return jsonify({"error": "Maximum duration is 1 hour."}), 400

        job_id = uuid.uuid4().hex

        with jobs_lock:
            jobs[job_id] = {
                "state": "queued",
                "progress": 0,
                "message": "Upload complete. Starting conversion...",
                "elapsed_seconds": 0,
                "estimated_seconds": max(
                    5, int((requested_duration or audio_duration) * 0.3)
                ),
                "error": None,
                "download_url": None,
                "work_dir": work_dir,
            }

        threading.Thread(
            target=process_job,
            args=(
                job_id,
                work_dir,
                audio_path,
                image_path,
                output_path,
                requested_duration,
                audio_duration,
            ),
            daemon=True,
        ).start()

        return jsonify({
            "job_id": job_id,
            "state": "queued",
            "status_url": f"/status/{job_id}",
        }), 202

    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({"error": f"Unable to start conversion: {exc}"}), 400


@app.get("/status/<job_id>")
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({"error": "Job not found or expired."}), 404

    return jsonify({
        key: value for key, value in job.items() if key != "work_dir"
    })


@app.get("/download/<job_id>")
def download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job or job.get("state") != "completed":
        return jsonify({"error": "The video is not ready yet."}), 404

    work_dir = job.get("work_dir")
    output_path = Path(work_dir) / "audio_video.mp4"

    if not output_path.is_file():
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({"error": "The temporary output file no longer exists."}), 404

    response = send_file(
        output_path,
        as_attachment=True,
        download_name="audio_to_video.mp4",
        mimetype="video/mp4",
    )

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        with jobs_lock:
            jobs.pop(job_id, None)

    response.call_on_close(cleanup)
    return response


if __name__ == "__main__":
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("Install FFmpeg and ensure ffmpeg and ffprobe are on PATH.")

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="0.0.0.0", port=5007, threaded=True)
