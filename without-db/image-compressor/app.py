import io
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_DIMENSION = 12000
MAX_IMAGE_PIXELS = 40_000_000


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "image-compressor"
    })


@app.errorhandler(RequestEntityTooLarge)
def file_too_large(_error):
    return jsonify({"error": "Maximum upload size is 25 MB."}), 413


@app.post("/compress")
def compress_image():
    uploaded_file = request.files.get("file")

    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Please select an image."}), 400

    original_name = secure_filename(uploaded_file.filename)
    extension = Path(original_name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({
            "error": "Only JPG, JPEG, PNG and WebP images are supported."
        }), 400

    try:
        quality = int(request.form.get("quality", "75"))
    except (ValueError, TypeError):
        return jsonify({"error": "Quality must be between 10 and 95."}), 400

    if not 10 <= quality <= 95:
        return jsonify({"error": "Quality must be between 10 and 95."}), 400

    try:
        uploaded_data = uploaded_file.read()

        if not uploaded_data:
            return jsonify({"error": "The uploaded file is empty."}), 400

        with Image.open(io.BytesIO(uploaded_data)) as source:
            if source.format not in ALLOWED_FORMATS:
                return jsonify({"error": "Unsupported image format."}), 400

            if (
                source.width > MAX_IMAGE_DIMENSION
                or source.height > MAX_IMAGE_DIMENSION
                or source.width * source.height > MAX_IMAGE_PIXELS
            ):
                return jsonify({
                    "error": "Image dimensions exceed the supported limit."
                }), 400

            source.verify()

        with Image.open(io.BytesIO(uploaded_data)) as source:
            image = ImageOps.exif_transpose(source)
            image.load()

            output_format = source.format

            if output_format == "JPEG":
                if image.mode in ("RGBA", "LA") or (
                    image.mode == "P" and "transparency" in image.info
                ):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")

            elif output_format == "PNG":
                image = image.convert(
                    "RGBA" if "A" in image.getbands() else "RGB"
                )

            elif output_format == "WEBP":
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA")

            output = io.BytesIO()

            save_options = {}

            if output_format == "JPEG":
                save_options = {
                    "quality": quality,
                    "optimize": True,
                    "progressive": True,
                }
            elif output_format == "WEBP":
                save_options = {
                    "quality": quality,
                    "method": 6,
                }
            elif output_format == "PNG":
                save_options = {
                    "optimize": True,
                    "compress_level": 9,
                }

            image.save(output, format=output_format, **save_options)
            output.seek(0)

            compressed_size = len(output.getbuffer())
            original_size = len(uploaded_data)

            output_extension = {
                "JPEG": ".jpg",
                "PNG": ".png",
                "WEBP": ".webp",
            }[output_format]

            download_name = (
                Path(original_name).stem + "-compressed" + output_extension
            )

            response = send_file(
                output,
                mimetype=Image.MIME.get(output_format, "application/octet-stream"),
                as_attachment=True,
                download_name=download_name,
                max_age=0,
            )

            response.headers["X-Original-Size"] = str(original_size)
            response.headers["X-Compressed-Size"] = str(compressed_size)
            response.headers["X-Compression-Saved"] = str(
                max(0, original_size - compressed_size)
            )

            return response

    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return jsonify({
            "error": "Unable to process this image. Please upload a valid image."
        }), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5013, debug=False)