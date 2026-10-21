
import io
import os
import logging

from flask import Flask, render_template, request, send_file, jsonify
from PIL import Image, ImageFile, UnidentifiedImageError
from reportlab.lib.pagesizes import (
    A4, A3, A5, LETTER, LEGAL, landscape, portrait
)
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from werkzeug.utils import secure_filename

app = Flask(__name__)

MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE
app.config["MAX_FORM_MEMORY_SIZE"] = 16 * 1024 * 1024
app.config["MAX_FORM_PARTS"] = 10000

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"
}

PAGE_SIZES = {
    "A3": A3,
    "A4": A4,
    "A5": A5,
    "LETTER": LETTER,
    "LEGAL": LEGAL,
}

MARGINS = {"none": 0, "small": 18, "big": 36}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return {"status": "healthy"}, 200


@app.post("/convert")
def convert():
    images = []

    try:
        uploaded_files = request.files.getlist("images")

        if not uploaded_files or not any(f.filename for f in uploaded_files):
            logger.error(
                "No images received. Content-Type=%s Content-Length=%s "
                "Form=%s Files=%s",
                request.content_type,
                request.content_length,
                list(request.form.keys()),
                list(request.files.keys()),
            )
            return jsonify(
                error="No images received. Verify the upload form and gateway."
            ), 400

        orientation = request.form.get("orientation", "portrait").lower()
        page_size_name = request.form.get("page_size", "A4").upper()
        margin_name = request.form.get("margin", "none").lower()

        if orientation not in {"portrait", "landscape"}:
            return jsonify(error="Invalid page orientation."), 400

        if page_size_name not in PAGE_SIZES:
            return jsonify(error="Invalid page size."), 400

        if margin_name not in MARGINS:
            return jsonify(error="Invalid margin option."), 400

        for uploaded_file in uploaded_files:
            if not uploaded_file.filename:
                continue

            filename = secure_filename(uploaded_file.filename)
            extension = os.path.splitext(filename)[1].lower()

            if extension not in ALLOWED_EXTENSIONS:
                return jsonify(
                    error=f"Unsupported image format: {filename}"
                ), 400

            try:
                with Image.open(uploaded_file.stream) as source:
                    source.load()
                    images.append(source.convert("RGB"))
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                logger.exception("Unable to process image %s", filename)
                return jsonify(
                    error=f"Unable to process image {filename}: {exc}"
                ), 400

        if not images:
            return jsonify(error="No valid images received."), 400

        base_size = PAGE_SIZES[page_size_name]
        page_dimensions = (
            landscape(base_size)
            if orientation == "landscape"
            else portrait(base_size)
        )

        page_width, page_height = page_dimensions
        margin = MARGINS[margin_name]
        available_width = page_width - 2 * margin
        available_height = page_height - 2 * margin

        if available_width <= 0 or available_height <= 0:
            return jsonify(
                error="Margins leave no usable page area."
            ), 400

        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=page_dimensions)

        for image in images:
            image_width, image_height = image.size

            scale = min(
                available_width / image_width,
                available_height / image_height,
            )

            rendered_width = image_width * scale
            rendered_height = image_height * scale
            x = (page_width - rendered_width) / 2
            y = (page_height - rendered_height) / 2

            pdf.drawImage(
                ImageReader(image),
                x,
                y,
                width=rendered_width,
                height=rendered_height,
                preserveAspectRatio=True,
                mask="auto",
            )
            pdf.showPage()

        pdf.save()
        output.seek(0)

        return send_file(
            output,
            mimetype="application/pdf",
            as_attachment=True,
            download_name="converted-images.pdf",
            max_age=0,
        )

    except Exception as exc:
        logger.exception("Image-to-PDF conversion failed")
        return jsonify(error=f"Conversion failed: {exc}"), 500

    finally:
        for image in images:
            try:
                image.close()
            except Exception:
                logger.exception("Failed to close image")


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify(error="Upload exceeds the 2 GB service limit."), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5008, debug=False)
