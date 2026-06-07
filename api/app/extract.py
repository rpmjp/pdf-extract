import io
import pdfplumber
import base64
import fitz  # pymupdf


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


def render_pages_to_images(pdf_bytes: bytes, dpi: int = 200) -> list[str]:
    """Render every PDF page to a base64-encoded PNG."""
    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        zoom = dpi / 72  # 72 dpi is the PDF default
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
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
            text = pytesseract.image_to_string(image)
            if text.strip():
                chunks.append(text)
    except Exception:
        return None
    finally:
        doc.close()

    return "\n\n".join(chunks) if chunks else None
