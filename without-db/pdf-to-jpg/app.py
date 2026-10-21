import io
import os
import zipfile
import logging

import fitz
from PIL import Image
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file,
)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024
app.config["MAX_FORM_MEMORY_SIZE"] = 16 * 1024 * 1024
app.config["MAX_FORM_PARTS"] = 10000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf"}
MAX_DPI = 300


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify(service="pdf-to-jpg", status="healthy"), 200


def get_pdf():
    uploaded = request.files.get("file") or request.files.get("pdf")

    if not uploaded or not uploaded.filename:
        return None, (jsonify(error="Please upload a PDF file."), 400)

    filename = secure_filename(uploaded.filename)

    if os.path.splitext(filename)[1].lower() not in ALLOWED_EXTENSIONS:
        return None, (jsonify(error="Only PDF files are supported."), 400)

    try:
        pdf_bytes = uploaded.read()

        if not pdf_bytes:
            return None, (jsonify(error="The uploaded PDF is empty."), 400)

        document = fitz.open(stream=pdf_bytes, filetype="pdf")

        if document.needs_pass:
            document.close()
            return None, (
                jsonify(error="Password-protected PDFs are not supported."),
                400,
            )

        return (document, filename), None

    except Exception:
        logger.exception("Unable to open uploaded PDF")
        return None, (jsonify(error="Unable to read this PDF file."), 400)


def render_page(document, page_number, dpi, quality):
    page = document.load_page(page_number)
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(dpi / 72, dpi / 72),
        colorspace=fitz.csRGB,
        alpha=False,
    )

    image = Image.frombytes(
        "RGB",
        (pixmap.width, pixmap.height),
        pixmap.samples,
    )

    output = io.BytesIO()
    image.save(
        output,
        format="JPEG",
        quality=quality,
        optimize=True,
    )
    image.close()
    output.seek(0)
    return output


def extract_page_images(document, page_number, quality):
    page = document.load_page(page_number)
    extracted = []

    for image_index, image_info in enumerate(page.get_images(full=True), 1):
        xref = image_info[0]

        try:
            image_data = document.extract_image(xref)
            image_bytes = image_data["image"]

            with Image.open(io.BytesIO(image_bytes)) as source:
                source.load()
                converted = source.convert("RGB")
                output = io.BytesIO()
                converted.save(
                    output,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                )
                converted.close()
                output.seek(0)

            extracted.append({
                "name": f"page-{page_number + 1}-image-{image_index}.jpg",
                "data": output.getvalue(),
            })

        except Exception:
            logger.warning(
                "Unable to extract image %s from page %s",
                image_index,
                page_number + 1,
                exc_info=True,
            )

    return extracted


@app.post("/inspect")
def inspect_pdf():
    result, error = get_pdf()
    if error:
        return error

    document, filename = result

    try:
        pages = []

        for page_number in range(len(document)):
            page = document.load_page(page_number)
            pages.append({
                "page": page_number + 1,
                "width": round(page.rect.width),
                "height": round(page.rect.height),
                "images": len(page.get_images(full=True)),
            })

        return jsonify({
            "filename": filename,
            "page_count": len(document),
            "pages": pages,
        })

    finally:
        document.close()


@app.post("/preview")
def preview_page():
    result, error = get_pdf()
    if error:
        return error

    document, _filename = result

    try:
        page_number = int(request.form.get("page", "0"))
        dpi = int(request.form.get("dpi", "100"))

        if not 0 <= page_number < len(document):
            return jsonify(error="Invalid page number."), 400

        dpi = max(36, min(dpi, MAX_DPI))
        output = render_page(document, page_number, dpi, 75)

        return send_file(
            output,
            mimetype="image/jpeg",
            download_name=f"page-{page_number + 1}.jpg",
            max_age=0,
        )

    except (ValueError, TypeError):
        return jsonify(error="Invalid page or DPI value."), 400

    finally:
        document.close()


@app.post("/convert")
def convert_pdf():
    result, error = get_pdf()
    if error:
        return error

    document, filename = result

    try:
        mode = request.form.get("mode", "pages")
        quality_name = request.form.get("quality", "normal")
        dpi = int(request.form.get("dpi", "150"))
        selected_raw = request.form.get("selected", "")

        if quality_name not in {"normal", "high"}:
            return jsonify(error="Invalid image quality."), 400

        quality = 85 if quality_name == "normal" else 95
        dpi = max(36, min(dpi, MAX_DPI))

        if mode not in {"pages", "extract"}:
            return jsonify(error="Invalid conversion mode."), 400

        try:
            selected_pages = {
                int(value)
                for value in selected_raw.split(",")
                if value.strip()
            }
        except ValueError:
            return jsonify(error="Invalid selected page numbers."), 400

        if any(
            page < 1 or page > len(document)
            for page in selected_pages
        ):
            return jsonify(error="A selected page number is invalid."), 400

        if not selected_pages:
            selected_pages = set(range(1, len(document) + 1))

        output_files = []

        for page_number in sorted(selected_pages):
            if mode == "pages":
                image = render_page(
                    document,
                    page_number - 1,
                    dpi,
                    quality,
                )
                output_files.append((
                    f"page-{page_number}.jpg",
                    image.getvalue(),
                ))
                image.close()

            else:
                extracted = extract_page_images(
                    document,
                    page_number - 1,
                    quality,
                )
                output_files.extend(
                    (item["name"], item["data"])
                    for item in extracted
                )

        if not output_files:
            return jsonify(
                error=(
                    "No embedded images were found in the selected pages. "
                    "Choose 'Page to JPG' to convert the pages themselves."
                )
            ), 400

        if len(output_files) == 1:
            image_name, image_data = output_files[0]
            return send_file(
                io.BytesIO(image_data),
                mimetype="image/jpeg",
                as_attachment=True,
                download_name=image_name,
                max_age=0,
            )

        archive = io.BytesIO()

        with zipfile.ZipFile(
            archive,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zip_file:
            for image_name, image_data in output_files:
                zip_file.writestr(image_name, image_data)

        archive.seek(0)

        base_name = os.path.splitext(filename)[0]
        return send_file(
            archive,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{base_name}-images.zip",
            max_age=0,
        )

    except Exception:
        logger.exception("PDF-to-JPG conversion failed")
        return jsonify(error="Conversion failed. Check the PDF and try again."), 500

    finally:
        document.close()


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify(error="The upload exceeds the 2 GB limit."), 413


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5009")),
        debug=False,
        threaded=True,
    )