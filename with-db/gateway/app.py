import os
import requests
from flask import Flask, jsonify, request, Response, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=None)

SERVICES = {
    "audio-cutter": "http://audio-cutter:5001",
    "audio-joiner": "http://audio-joiner:5002",
    "video-cutter": "http://video-cutter:5003",
    "pdf-converter": "http://pdf-converter:5004",
    "pdf-combiner": "http://pdf-combiner:5005",
    "user": "http://192.168.0.107:5006",
}


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>", methods=["GET"])
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/health", methods=["GET"])
def health():
    return jsonify(service="gateway", status="ok"), 200


@app.route("/api/services", methods=["GET"])
def services():
    results = {}

    for name, url in SERVICES.items():
        try:
            response = requests.get(f"{url}/health", timeout=5)
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
        return jsonify(error="Service not found"), 404

    url = f"{SERVICES[service]}/{path}"

    if request.query_string:
        url += "?" + request.query_string.decode()

    try:
        upstream = requests.request(
            method=request.method,
            url=url,
            headers={
                key: value
                for key, value in request.headers
                if key.lower()
                not in ("host", "content-length", "connection")
            },
            data=request.get_data(),
            timeout=(10, 600),
            allow_redirects=False,
        )

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

    except requests.RequestException as exc:
        app.logger.error(
            "Request to service %s failed: %s",
            service,
            exc,
        )
        return jsonify(
            error="Microservice unavailable",
            service=service,
        ), 502


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )