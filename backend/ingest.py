"""
Ingest a PDF or text file into the archive.

Examples
  python ingest.py "sources/BAWS_Vol1.pdf" --title "Annihilation of Caste" --pages 23-96 \
      --date 1936 --type Book --source "Dr. Ambedkar Foundation, Writings and Speeches Vol. 1"
  python ingest.py "sources/cad_25nov1949.txt" --title "Closing speech, Constituent Assembly" \
      --date 1949-11-25 --type Speech --source "Constituent Assembly Debates, Vol. XI"

Pages with no embedded text are OCR'd automatically (scanned PDFs).
"""
import argparse
import hashlib
import re
import shutil
import datetime
from pathlib import Path

from rag import Archive, BASE, DATA


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "doc"


def ocr_page(page, lang):
    import pytesseract
    from PIL import Image
    import io, os
    if os.getenv("TESSERACT_CMD"):
        pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_CMD")
    pix = page.get_pixmap(dpi=250)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    try:
        return pytesseract.image_to_string(img, lang=lang)
    except pytesseract.TesseractError:
        return pytesseract.image_to_string(img, lang="eng")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--title", required=True)
    ap.add_argument("--pages", help="PDF page range to ingest, e.g. 23-96 (keeps real PDF page numbers)")
    ap.add_argument("--date", default="")
    ap.add_argument("--type", default="Book")
    ap.add_argument("--creator", default="Dr. B. R. Ambedkar")
    ap.add_argument("--language", default="English")
    ap.add_argument("--source", default="")
    ap.add_argument("--subjects", default="", help="comma separated")
    ap.add_argument("--ocr-lang", default="eng", help="tesseract languages for scanned pages, e.g. eng+hin")
    a = ap.parse_args()

    src = Path(a.path)
    raw = src.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    doc_id = f"{slug(a.title)}-{sha[:6]}"
    pages = []

    if src.suffix.lower() == ".pdf":
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        dest = DATA / "files" / f"{doc_id}.pdf"
        shutil.copyfile(src, dest)
        with fitz.open(dest) as pdf:
            start, end = 1, pdf.page_count
            if a.pages:
                start, end = [int(x) for x in a.pages.split("-")]
                end = min(end, pdf.page_count)
            for n in range(start, end + 1):
                text = pdf[n - 1].get_text()
                if len(text.strip()) < 30:
                    print(f"  page {n}: no text layer, running OCR")
                    text = ocr_page(pdf[n - 1], a.ocr_lang)
                pages.append((n, text))
        fmt, file_rel = "application/pdf", str(dest.relative_to(BASE))
    else:
        text = raw.decode("utf-8", errors="ignore")
        block, n, cur = 2500, 1, ""
        for para in re.split(r"\n\s*\n", text):
            if len(cur) + len(para) > block and cur:
                pages.append((n, cur))
                n, cur = n + 1, ""
            cur = (cur + "\n\n" + para).strip()
        if cur:
            pages.append((n, cur))
        fmt, file_rel = "text/plain", ""

    meta = {
        "id": doc_id, "title": a.title, "creator": a.creator, "date": a.date, "type": a.type,
        "language": a.language, "format": fmt, "source": a.source,
        "subjects": [s.strip() for s in a.subjects.split(",") if s.strip()],
        "description": "", "rights": "Public domain (India)", "file": file_rel,
        "sha256": sha, "added_at": datetime.datetime.now().isoformat(timespec="seconds"), "origin": "ingest",
    }
    print(f"Indexing {len(pages)} pages of '{a.title}' ...")
    meta = Archive().add_document(meta, pages)
    print(f"Done: {doc_id}  ({meta['chunks']} chunks, pages {meta['first_page']}-{meta['last_page']})")


if __name__ == "__main__":
    main()
