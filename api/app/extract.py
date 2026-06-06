import io
import pdfplumber


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
