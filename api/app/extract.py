"""PDF classification, text extraction, and OCR/vision preprocessing.

Digital PDFs are parsed from their embedded text layer.  Scanned statements are
rendered to images for the vision model, with optional deskew/denoise steps for
low-confidence cases.
"""
import io
import pdfplumber
import base64
import fitz  # pymupdf
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


def classify_and_extract(pdf_bytes: bytes) -> dict:
    """Detect digital vs scanned, and pull text + word boxes from a PDF."""
    result = {"kind": None, "pages": []}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        total_chars = 0
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            words = page.extract_words()
            total_chars += len(text)
            result["pages"].append({
                "page": i + 1,
                "text": text,
                "words": [
                    {"text": w["text"], "x0": w["x0"], "top": w["top"],
                     "x1": w["x1"], "bottom": w["bottom"]}
                    for w in words
                ],
            })
    # Heuristic: a digital PDF has an extractable text layer; a scan has ~none
    result["kind"] = "digital" if total_chars > 20 else "scanned"
    return result


def deskew_image(image: Image.Image) -> Image.Image:
    """Pick the small rotation that creates the strongest horizontal text rows."""
    gray = ImageOps.grayscale(image)
    best_angle = 0
    best_score = None
    for angle in (-2, -1, 0, 1, 2):
        rotated = gray.rotate(angle, expand=True, fillcolor=255)
        rows = []
        pixels = rotated.load()
        width, height = rotated.size
        for y in range(0, height, 4):
            dark = 0
            for x in range(0, width, 4):
                if pixels[x, y] < 200:
                    dark += 1
            rows.append(dark)
        mean = sum(rows) / len(rows) if rows else 0
        score = sum((row - mean) ** 2 for row in rows)
        if best_score is None or score > best_score:
            best_score = score
            best_angle = angle
    return image.rotate(best_angle, expand=True, fillcolor="white")


def preprocess_page_image(image: Image.Image) -> Image.Image:
    """Normalize, denoise, and sharpen a page before OCR or vision fallback."""
    image = deskew_image(image.convert("RGB"))
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.5)
    gray = gray.filter(ImageFilter.MedianFilter(size=3))
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray.convert("RGB")


def render_pages_to_images(pdf_bytes: bytes, dpi: int = 200, preprocess: bool = False) -> list[str]:
    """Render every PDF page to base64 PNGs accepted by the LLM client."""
    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        zoom = dpi / 72  # 72 dpi is the PDF default
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            if preprocess:
                image = Image.open(io.BytesIO(pix.tobytes("png")))
                output = io.BytesIO()
                preprocess_page_image(image).save(output, format="PNG")
                images.append(base64.b64encode(output.getvalue()).decode())
            else:
                images.append(base64.b64encode(pix.tobytes("png")).decode())
    finally:
        doc.close()
    return images


def extract_text_with_tesseract(pdf_bytes: bytes, dpi: int = 200) -> str | None:
    """Optional OCR fallback. Returns None when pytesseract/tesseract is unavailable."""
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return None

    chunks = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(preprocess_page_image(image))
            if text.strip():
                chunks.append(text)
    except Exception:
        return None
    finally:
        doc.close()

    return "\n\n".join(chunks) if chunks else None
