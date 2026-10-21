import os
import logging
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, Response, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)

app = Flask(__name__, static_folder=None)

app.logger.setLevel(logging.INFO)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024
app.config["MAX_FORM_MEMORY_SIZE"] = 16 * 1024 * 1024
app.config["MAX_FORM_PARTS"] = 10000

from config import SERVICES

@app.before_request
def log_request():
    app.logger.info(
        "GATEWAY REQUEST method=%s path=%s remote=%s content_type=%s content_length=%s",
        request.method,
        request.full_path,
        request.remote_addr,
        request.content_type,
        request.content_length,
    )


@app.after_request
def log_response(response):
    app.logger.info(
        "GATEWAY RESPONSE method=%s path=%s status=%s",
        request.method,
        request.path,
        response.status_code,
    )
    return response


@app.errorhandler(413)
def file_too_large(_error):
    app.logger.error("Request rejected: uploaded file exceeds 2 GB gateway limit")
    return jsonify(error="Uploaded file exceeds the 2 GB gateway limit"), 413


def proxy_response(upstream):
    excluded_headers = {
        "content-encoding",
        "content-length",
        "transfer-encoding",
        "connection",
    }

    headers = [
        (key, value)
        for key, value in upstream.headers.items()
        if key.lower() not in excluded_headers
    ]

    return Response(
        upstream.content,
        status=upstream.status_code,
        headers=headers,
    )


def forward_request(service_name, path=""):
    if service_name not in SERVICES:
        app.logger.error("Unknown service requested: %s", service_name)
        return jsonify(error="Service not found", service=service_name), 404

    url = f"{SERVICES[service_name].rstrip('/')}/{path.lstrip('/')}"

    if request.query_string:
        url += "?" + request.query_string.decode("latin-1")

    app.logger.info(
        "GATEWAY UPSTREAM REQUEST service=%s method=%s url=%s",
        service_name,
        request.method,
        url,
    )

    try:
        body = request.get_data(cache=True)

        headers = {
            key: value
            for key, value in request.headers
            if key.lower() not in {
                "host",
                "content-length",
                "connection",
                "transfer-encoding",
            }
        }

        upstream = requests.request(
            method=request.method,
            url=url,
            data=body if body else None,
            headers=headers,
            allow_redirects=False,
            timeout=(10, 600),
        )

        app.logger.info(
            "GATEWAY UPSTREAM RESPONSE service=%s status=%s url=%s",
            service_name,
            upstream.status_code,
            url,
        )

        if upstream.status_code >= 400:
            app.logger.error(
                "UPSTREAM ERROR service=%s status=%s body=%s",
                service_name,
                upstream.status_code,
                upstream.text[:2000],
            )

        return proxy_response(upstream)

    except requests.Timeout:
        app.logger.exception(
            "UPSTREAM TIMEOUT service=%s url=%s",
            service_name,
            url,
        )
        return jsonify(
            error="The upstream service timed out",
            service=service_name,
        ), 504

    except requests.RequestException:
        app.logger.exception(
            "UPSTREAM CONNECTION ERROR service=%s url=%s",
            service_name,
            url,
        )
        return jsonify(
            error="Microservice unavailable",
            service=service_name,
        ), 502


# Video subtitle
@app.route("/video-subtitle", methods=["GET"])
@app.route("/video-subtitle/", methods=["GET"])
def video_subtitle_page():
    app.logger.info("VIDEO SUBTITLE: requesting service page")

    try:
        upstream = requests.get(
            f"{SERVICES['video-subtitle'].rstrip('/')}/",
            timeout=(10, 30),
        )

        app.logger.info(
            "VIDEO SUBTITLE: page response status=%s",
            upstream.status_code,
        )

        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get(
                "Content-Type",
                "text/html; charset=utf-8",
            ),
        )

    except requests.RequestException:
        app.logger.exception("VIDEO SUBTITLE: page request failed")
        return jsonify(error="Video Subtitle service unavailable"), 502


@app.route("/video-subtitle/convert", methods=["POST"])
def video_subtitle_convert():
    app.logger.info("VIDEO SUBTITLE: export request received")
    return forward_request("video-subtitle", "convert")


@app.route(
    "/video-subtitle/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def video_subtitle_proxy(path):
    app.logger.info(
        "VIDEO SUBTITLE: proxy request path=%s method=%s",
        path,
        request.method,
    )
    return forward_request("video-subtitle", path)

# PDF to Word Converter
@app.route("/word-to-pdf", methods=["GET"])
@app.route("/word-to-pdf/", methods=["GET"])
def word_to_pdf_page():
    return forward_request("word-to-pdf")


@app.route("/word-to-pdf/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def word_to_pdf_proxy(path):
    return forward_request("word-to-pdf", path)

@app.route("/", methods=["GET"])
def index():
    return send_from_directory(STATIC_DIR, "index.html")

#PDF to Word Converter
@app.route("/pdf-to-word", methods=["GET"])
@app.route("/pdf-to-word/", methods=["GET"])
def pdf_to_word_page():
    return forward_request("pdf-to-word")


@app.route(
    "/pdf-to-word/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def pdf_to_word_proxy(path):
    return forward_request("pdf-to-word", path)

@app.route("/pdf-to-word/static/<path:filename>")
def pdf_to_word_static(filename):
    return forward_request("pdf-to-word", f"static/{filename}")

@app.route("/static/<path:filename>", methods=["GET"])
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

# Image Compressor
@app.route("/image-compressor", methods=["GET"])
@app.route("/image-compressor/", methods=["GET"])
def image_compressor_page():
    return forward_request("image-compressor")


@app.route(
    "/image-compressor/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
)
def image_compressor_proxy(path):
    return forward_request("image-compressor", path)

@app.route("/image-compressor/static/<path:filename>")
def image_compressor_static(filename):
    return forward_request("image-compressor", f"static/{filename}")

# Video to Gif Converter
@app.route("/video-to-gif", methods=["GET"])
@app.route("/video-to-gif/", methods=["GET"])
def video_to_gif_page():
    return forward_request("video-to-gif")


@app.route(
    "/video-to-gif/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def video_to_gif_proxy(path):
    return forward_request("video-to-gif", path)

# Video To reel
@app.route("/video-to-reels", methods=["GET"])
@app.route("/video-to-reels/", methods=["GET"])
def video_to_reels_page():
    return forward_request("video-to-reels")


@app.route(
    "/video-to-reels/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def video_to_reels_proxy(path):
    return forward_request("video-to-reels", path)

# Audio Cutter
@app.route("/audio-cutter/cut", methods=["POST"])
def audio_cutter_cut():
    uploaded_file = request.files.get("file") or request.files.get("audio")

    if not uploaded_file or not uploaded_file.filename:
        return jsonify(error="Audio file is required"), 400

    try:
        upstream = requests.post(
            f"{SERVICES['audio-cutter'].rstrip('/')}/cut",
            files={
                "file": (
                    uploaded_file.filename,
                    uploaded_file.stream,
                    uploaded_file.mimetype or "application/octet-stream",
                )
            },
            data={
                "start": request.form.get("start", ""),
                "end": request.form.get("end", ""),
            },
            timeout=(10, 600),
        )
        return proxy_response(upstream)

    except requests.RequestException:
        app.logger.exception("Request to audio-cutter failed")
        return jsonify(
            error="Microservice unavailable",
            service="audio-cutter",
        ), 502


@app.route("/cut", methods=["POST"], strict_slashes=False)
def cut_video():
    video = request.files.get("video") or request.files.get("file")

    if not video or not video.filename:
        return jsonify(error="Missing video upload"), 400

    try:
        upstream = requests.post(
            f"{SERVICES['video-cutter'].rstrip('/')}/cut",
            files={
                "file": (
                    video.filename,
                    video.stream,
                    video.mimetype or "video/mp4",
                )
            },
            data={
                "start": request.form.get("start", "0"),
                "end": request.form.get("end", "0"),
                "speed": request.form.get("speed", "1"),
                "volume": request.form.get("volume", "1"),
                "rotation": request.form.get("rotation", "0"),
                "filter": request.form.get("filter", "none"),
                "overlay_text": request.form.get("overlay_text", ""),
            },
            timeout=(10, 600),
        )
        return proxy_response(upstream)

    except requests.RequestException:
        app.logger.exception("Request to video-cutter failed")
        return jsonify(
            error="Microservice unavailable",
            service="video-cutter",
        ), 502


@app.route("/audio-to-video/convert", methods=["POST"], strict_slashes=False)
def audio_to_video_convert():
    audio = request.files.get("audio")
    image = request.files.get("image")

    if not audio or not audio.filename:
        return jsonify(error="Audio file is required"), 400

    files = {
        "audio": (
            audio.filename,
            audio.stream,
            audio.mimetype or "application/octet-stream",
        )
    }

    if image and image.filename:
        files["image"] = (
            image.filename,
            image.stream,
            image.mimetype or "application/octet-stream",
        )

    try:
        upstream = requests.post(
            f"{SERVICES['audio-to-video'].rstrip('/')}/convert",
            files=files,
            data={"duration": request.form.get("duration", "default")},
            timeout=(10, 600),
        )
        return proxy_response(upstream)

    except requests.RequestException:
        app.logger.exception("Request to audio-to-video failed")
        return jsonify(
            error="Microservice unavailable",
            service="audio-to-video",
        ), 502


@app.route("/image-to-pdf", methods=["GET"])
@app.route("/image-to-pdf/", methods=["GET"])
def image_to_pdf_page():
    try:
        upstream = requests.get(
            f"{SERVICES['image-to-pdf'].rstrip('/')}/",
            timeout=(10, 30),
        )
        upstream.raise_for_status()

        html = upstream.text.replace(
            'href="/static/',
            'href="/image-to-pdf/static/',
        ).replace(
            'src="/static/',
            'src="/image-to-pdf/static/',
        ).replace(
            'action="/convert"',
            'action="/image-to-pdf/convert"',
        )

        return Response(
            html,
            status=upstream.status_code,
            content_type="text/html; charset=utf-8",
        )

    except requests.RequestException:
        app.logger.exception("Request to image-to-pdf failed")
        return jsonify(error="Image-to-PDF service unavailable"), 502


@app.route("/image-to-pdf/static/<path:filename>", methods=["GET"])
def image_to_pdf_static(filename):
    return forward_request("image-to-pdf", f"static/{filename}")


@app.route("/image-to-pdf/convert", methods=["POST"])
def image_to_pdf_convert():
    return forward_request("image-to-pdf", "convert")


@app.route("/health", methods=["GET"])
def health():
    return jsonify(service="gateway", status="ok"), 200


@app.route("/api/services", methods=["GET"])
def services():
    results = {}

    for name, url in SERVICES.items():
        try:
            response = requests.get(f"{url.rstrip('/')}/health", timeout=5)
            results[name] = "healthy" if response.ok else "unhealthy"
        except requests.RequestException:
            results[name] = "unreachable"

    return jsonify(results), 200


@app.route(
    "/<service>/",
    defaults={"path": ""},
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@app.route(
    "/<service>/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def route_service(service, path):
    if service not in SERVICES:
        app.logger.warning("Unknown service route: %s", service)
        return jsonify(error="Service not found", service=service), 404

    return forward_request(service, path)


@app.route("/pdf-to-jpg", methods=["GET"])
@app.route("/pdf-to-jpg/", methods=["GET"])
def pdf_to_jpg_page():
    try:
        upstream = requests.get(
            f"{SERVICES['pdf-to-jpg'].rstrip('/')}/",
            timeout=(10, 30),
        )
        upstream.raise_for_status()

        return Response(
            upstream.text,
            status=upstream.status_code,
            content_type="text/html; charset=utf-8",
        )

    except requests.RequestException:
        app.logger.exception("Request to pdf-to-jpg failed")
        return jsonify(error="PDF-to-JPG service unavailable"), 502


@app.route("/pdf-to-jpg/<path:path>", methods=["GET", "POST"])
def pdf_to_jpg_proxy(path):
    return forward_request("pdf-to-jpg", path)


@app.route("/services/", methods=["GET"])
@app.route("/services", methods=["GET"])
def services_page():
    return send_from_directory(STATIC_DIR, "services.html")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False,
        threaded=True,
    )
