"""
Dr. B. R. Ambedkar Digital Heritage Archive - backend server.

Run:   python app.py
Kiosk: http://<laptop-ip>:8000/          Smart display: /?mode=wall
Admin: http://<laptop-ip>:8000/admin
"""
import os
import io
import re
import json
import time
import html
import uuid
import base64
import socket
import asyncio
import hashlib
import datetime
import tempfile
from pathlib import Path
from typing import Optional, List

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import rag
from rag import DATA, BASE, LANG_NAMES

WEB = BASE.parent / "web"
PORT = int(os.getenv("PORT", "8000"))
ADMIN_PIN = os.getenv("ADMIN_PIN", "1234")
SENSOR_TOKEN = os.getenv("SENSOR_TOKEN", "")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


PUBLIC_BASE = (os.getenv("PUBLIC_BASE_URL") or f"http://{lan_ip()}:{PORT}").rstrip("/")
state = {"cam_url": os.getenv("ESP32CAM_URL", ""), "case": "unknown", "case_seen": None,
         "last_visitor": None, "alerts": []}

archive = rag.Archive()
app = FastAPI(title="Dr. B. R. Ambedkar Digital Heritage Archive")


# ---------------------------------------------------------------- live event bus (Server-Sent Events)

class Bus:
    def __init__(self):
        self.subs, self.loop = set(), None

    def publish(self, ev):
        if not self.loop:
            return
        for q in list(self.subs):
            self.loop.call_soon_threadsafe(q.put_nowait, ev)


bus = Bus()


@app.on_event("startup")
async def _startup():
    bus.loop = asyncio.get_running_loop()


@app.get("/api/events")
async def events(request: Request):
    q = asyncio.Queue()
    bus.subs.add(q)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.subs.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- helpers

def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def audit(action, **detail):
    line = {"time": now(), "action": action, **detail}
    with open(DATA / "audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    bus.publish({"type": "audit", **line})


def require_admin(pin):
    if pin != ADMIN_PIN:
        raise HTTPException(401, "Enter the admin PIN to use this feature.")


def lang_or_en(lang):
    return lang if lang in LANG_NAMES else "en"


class AskIn(BaseModel):
    question: str
    lang: str = "en"


class TextIn(BaseModel):
    text: str
    lang: str = "en"


class PageIn(BaseModel):
    doc_id: str
    page: int
    lang: str = "en"


class CompileIn(BaseModel):
    items: List[dict]
    lang: str = "en"


class SensorIn(BaseModel):
    type: str
    source: str = "node"
    value: Optional[str] = None


class CamIn(BaseModel):
    title: str = ""
    lang: str = "en"


class CamCfg(BaseModel):
    url: str


# ---------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def kiosk():
    return FileResponse(WEB / "kiosk.html")


@app.get("/admin", response_class=HTMLResponse)
def admin():
    return FileResponse(WEB / "admin.html")


app.mount("/media", StaticFiles(directory=str(DATA / "media")), name="media")


@app.get("/api/status")
def status():
    return {**state, "documents": len(archive.catalog), "chunks": archive.col.count(),
            "llm": os.getenv("LLM_PROVIDER", "") or "offline", "public_base": PUBLIC_BASE}


# ---------------------------------------------------------------- search and AI

@app.post("/api/ask")
def ask(body: AskIn):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Type or say a question first.")
    res = rag.answer(archive, q, lang_or_en(body.lang))
    audit("question", lang=body.lang, question=q[:200], mode=res["mode"])
    return res


@app.post("/api/search")
def search(body: AskIn):
    return {"results": archive.search(body.question, k=10)}


@app.post("/api/summarize")
def summarize(body: PageIn):
    text = archive.page_text(body.doc_id, body.page)
    if not text:
        raise HTTPException(404, "This page has no text to summarize.")
    return {"summary": rag.summarize(text, lang_or_en(body.lang))}


@app.post("/api/translate")
def translate(body: TextIn):
    return {"text": rag.translate(body.text[:8000], lang_or_en(body.lang))}


# ---------------------------------------------------------------- library

@app.get("/api/docs")
def docs():
    items = sorted(archive.catalog.values(), key=lambda d: (d.get("origin") != "ingest", d.get("title", "")))
    return {"documents": items}


@app.get("/api/docs/{doc_id}/page/{n}")
def doc_page(doc_id: str, n: int):
    meta = archive.catalog.get(doc_id)
    if not meta:
        raise HTTPException(404, "Document not found.")
    has_image = bool(meta.get("file")) and meta.get("format") in ("application/pdf", "image/jpeg", "image/png")
    return {"doc": meta, "page": n, "text": archive.page_text(doc_id, n),
            "image": f"/api/docs/{doc_id}/page/{n}/image" if has_image else None}


@app.get("/api/docs/{doc_id}/page/{n}/image")
def page_image(doc_id: str, n: int):
    meta = archive.catalog.get(doc_id)
    if not meta or not meta.get("file"):
        raise HTTPException(404, "No original image for this page.")
    path = BASE / meta["file"]
    if meta.get("format") == "application/pdf":
        cache = DATA / "pages" / f"{doc_id}-{n}.png"
        if not cache.exists():
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz
            with fitz.open(path) as pdf:
                if not 1 <= n <= pdf.page_count:
                    raise HTTPException(404, "Page out of range.")
                pdf[n - 1].get_pixmap(dpi=110).save(str(cache))
        return FileResponse(cache, media_type="image/png")
    return FileResponse(path)


@app.get("/api/timeline")
def timeline():
    return json.loads((DATA / "timeline.json").read_text("utf-8"))


@app.get("/api/graph")
def graph():
    return json.loads((DATA / "graph.json").read_text("utf-8"))


@app.get("/api/media")
def media():
    folder = DATA / "media"
    listing = folder / "media.json"
    if listing.exists():
        items = json.loads(listing.read_text("utf-8"))
    else:
        items = [{"file": p.name, "title": p.stem.replace("_", " ").replace("-", " ")}
                 for p in sorted(folder.iterdir()) if p.suffix.lower() in (".mp4", ".webm", ".mp3", ".jpg", ".jpeg", ".png")]
    for it in items:
        ext = Path(it["file"]).suffix.lower()
        it["kind"] = "video" if ext in (".mp4", ".webm") else "audio" if ext == ".mp3" else "image"
        it["url"] = f"/media/{it['file']}"
    return {"items": items}


# ---------------------------------------------------------------- speech

_whisper = None


@app.post("/api/stt")
def stt(audio: UploadFile = File(...), lang: str = Form("en")):
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(os.getenv("WHISPER_MODEL", "small"), device="cpu", compute_type="int8")
    suffix = Path(audio.filename or "q.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio.file.read())
        path = tmp.name
    try:
        segments, _ = _whisper.transcribe(path, language=lang if lang in LANG_NAMES else None,
                                          beam_size=1, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
    finally:
        os.unlink(path)
    return {"text": text}


@app.post("/api/tts")
def tts(body: TextIn):
    lang = lang_or_en(body.lang)
    text = re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", body.text).replace("*", "").strip()[:2500]
    if not text:
        raise HTTPException(400, "Nothing to read aloud.")
    out = DATA / "tts" / (hashlib.sha1(f"{lang}|{text}".encode()).hexdigest() + ".mp3")
    if not out.exists():
        try:
            from gtts import gTTS
            gTTS(text=text, lang=lang).save(str(out))
        except Exception as e:
            raise HTTPException(502, f"Speech service is unreachable: {e}")
    return FileResponse(out, media_type="audio/mpeg")


# ---------------------------------------------------------------- digitization (OCR pipeline)

def digitize(img_bytes, title, lang, origin):
    import cv2
    import numpy as np
    import pytesseract
    if os.getenv("TESSERACT_CMD"):
        pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_CMD")

    lang = lang_or_en(lang)
    bus.publish({"type": "digitize_status", "stage": "processing"})
    sha = hashlib.sha256(img_bytes).hexdigest()
    doc_id = "scan-" + time.strftime("%Y%m%d-%H%M%S")
    master = DATA / "scans" / f"{doc_id}.jpg"
    master.write_bytes(img_bytes)                      # untouched preservation master

    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "The file is not a readable image. Use JPG or PNG.")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    if max(gray.shape) < 2000:
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cv2.imwrite(str(DATA / "scans" / f"{doc_id}-processed.png"), bw)

    tess = {"en": "eng", "hi": "hin+eng", "mr": "mar+eng"}[lang]
    try:
        text = pytesseract.image_to_string(bw, lang=tess)
    except pytesseract.TesseractError:
        text = pytesseract.image_to_string(bw, lang="eng")   # language pack missing
    text = text.strip()
    if len(text) < 20:
        raise HTTPException(422, "Almost no text was recognised. Fill the frame with the page, add light and capture again.")

    tags = rag.tag_metadata(text)
    meta = {
        "id": doc_id,
        "title": title.strip() or tags.get("title") or f"Digitized page {time.strftime('%d %b %Y %H:%M')}",
        "creator": "", "date": tags.get("date", ""), "type": "Manuscript page (digitized)",
        "language": LANG_NAMES[lang], "format": "image/jpeg", "source": f"Digitization station ({origin})",
        "subjects": tags.get("subjects", []), "description": tags.get("summary", ""),
        "rights": "Institutional archive", "file": str(master.relative_to(BASE)),
        "sha256": sha, "added_at": now(), "origin": origin,
    }
    archive.add_document(meta, [(1, text)])
    audit("digitized", doc_id=doc_id, title=meta["title"], origin=origin, sha256=sha, chars=len(text))
    bus.publish({"type": "digitized", "doc_id": doc_id, "title": meta["title"]})
    return {"doc": meta, "text": text[:1500]}


@app.post("/api/ocr/upload")
def ocr_upload(file: UploadFile = File(...), title: str = Form(""), lang: str = Form("en"),
               x_admin_pin: Optional[str] = Header(None)):
    require_admin(x_admin_pin)
    return digitize(file.file.read(), title, lang, origin="upload")


@app.post("/api/cam/config")
def cam_config(body: CamCfg, x_admin_pin: Optional[str] = Header(None)):
    require_admin(x_admin_pin)
    url = body.url.strip().rstrip("/")
    if url and not url.startswith("http"):
        url = "http://" + url
    state["cam_url"] = url
    audit("camera_configured", url=url)
    return {"cam_url": url}


@app.post("/api/cam/capture")
def cam_capture(body: CamIn, x_admin_pin: Optional[str] = Header(None)):
    require_admin(x_admin_pin)
    if not state["cam_url"]:
        raise HTTPException(400, "Set the ESP32-CAM address first (shown on its serial monitor).")
    bus.publish({"type": "digitize_status", "stage": "capturing"})
    try:
        r = requests.get(state["cam_url"] + "/capture", timeout=25)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(502, f"Could not reach the ESP32-CAM at {state['cam_url']}: {e}")
    if not r.headers.get("content-type", "").startswith("image/"):
        raise HTTPException(502, "The ESP32-CAM did not return an image.")
    return digitize(r.content, body.title, body.lang, origin="esp32-cam")


# ---------------------------------------------------------------- sensors (ESP8266, webcam, Arduino)

@app.post("/api/sensor/event")
def sensor_event(ev: SensorIn, x_sensor_token: Optional[str] = Header(None)):
    if SENSOR_TOKEN and x_sensor_token != SENSOR_TOKEN:
        raise HTTPException(401, "Invalid sensor token.")
    e = {"kind": ev.type, "source": ev.source, "value": ev.value, "time": now()}
    if ev.type == "visitor":
        state["last_visitor"] = e["time"]
    elif ev.type in ("tamper_open", "tamper_closed"):
        state["case"] = "open" if ev.type == "tamper_open" else "closed"
        state["case_seen"] = e["time"]
        if ev.type == "tamper_open":
            state["alerts"] = ([{**e, "message": f"Display case opened ({ev.source})"}] + state["alerts"])[:50]
        audit("display_case_" + state["case"], source=ev.source)
    elif ev.type == "heartbeat":
        state["case"] = ev.value or state["case"]
        state["case_seen"] = e["time"]
    bus.publish({"type": "sensor", **e})
    return {"ok": True}


@app.get("/api/audit")
def audit_log(limit: int = 40, x_admin_pin: Optional[str] = Header(None)):
    require_admin(x_admin_pin)
    path = DATA / "audit.jsonl"
    lines = path.read_text("utf-8").splitlines()[-limit:] if path.exists() else []
    return {"entries": [json.loads(l) for l in reversed(lines) if l.strip()]}


# ---------------------------------------------------------------- take-home compilation

@app.post("/api/compile")
def compile_items(body: CompileIn):
    items = [{"title": str(i.get("title", ""))[:200], "source": str(i.get("source", ""))[:300],
              "text": str(i.get("text", ""))[:5000]} for i in body.items[:30] if i.get("text")]
    if not items:
        raise HTTPException(400, "Add at least one passage to your compilation first.")
    cid = uuid.uuid4().hex[:10]
    (DATA / "compilations" / f"{cid}.json").write_text(
        json.dumps({"created": now(), "lang": body.lang, "items": items}, ensure_ascii=False), "utf-8")
    url = f"{PUBLIC_BASE}/c/{cid}"
    import qrcode
    buf = io.BytesIO()
    qrcode.make(url, box_size=10, border=2).save(buf, format="PNG")
    audit("compilation_created", id=cid, items=len(items))
    return {"id": cid, "url": url, "qr": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}


@app.get("/c/{cid}", response_class=HTMLResponse)
def compilation_page(cid: str):
    if not re.fullmatch(r"[0-9a-f]{10}", cid):
        raise HTTPException(404)
    path = DATA / "compilations" / f"{cid}.json"
    if not path.exists():
        raise HTTPException(404, "This compilation has expired or does not exist.")
    data = json.loads(path.read_text("utf-8"))
    parts = []
    for it in data["items"]:
        body = "".join(f"<p>{html.escape(p)}</p>" for p in it["text"].split("\n") if p.strip())
        src = f'<p class="src">{html.escape(it["source"])}</p>' if it["source"] else ""
        parts.append(f"<article><h2>{html.escape(it['title'])}</h2>{body}{src}</article>")
    return f"""<!doctype html><html lang="{html.escape(data.get('lang', 'en'))}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>My compilation - Dr. B. R. Ambedkar Digital Heritage Archive</title>
<link href="https://fonts.googleapis.com/css2?family=Tiro+Devanagari+Hindi&family=Mukta:wght@400;600&display=swap" rel="stylesheet">
<style>body{{margin:0;background:#eef0f6;color:#141a33;font:17px/1.65 Mukta,system-ui,sans-serif}}
header{{background:#1a2466;color:#fff;padding:28px 20px}}header h1{{font:28px/1.2 'Tiro Devanagari Hindi',Georgia,serif;margin:0 0 6px}}
main{{max-width:720px;margin:0 auto;padding:16px 20px 40px}}article{{background:#fbfcff;border-left:4px solid #2743c9;padding:16px 20px;margin:16px 0}}
h2{{font:21px/1.3 'Tiro Devanagari Hindi',Georgia,serif;margin:0 0 8px}}.src{{color:#5a6180;font-size:15px}}
button{{font:inherit;background:#2743c9;color:#fff;border:0;padding:12px 20px;border-radius:6px}}@media print{{button{{display:none}}}}</style></head>
<body><header><h1>My compilation</h1><div>Dr. B. R. Ambedkar Digital Heritage Archive &middot; {html.escape(data['created'][:10])}</div></header>
<main><button onclick="print()">Save as PDF</button>{''.join(parts)}</main></body></html>"""


if __name__ == "__main__":
    import uvicorn
    ssl = {}
    if os.getenv("SSL_CERT") and os.getenv("SSL_KEY"):
        ssl = {"ssl_certfile": os.getenv("SSL_CERT"), "ssl_keyfile": os.getenv("SSL_KEY")}
    print("\n" + "=" * 60)
    print(f"  Kiosk           {PUBLIC_BASE}/")
    print(f"  Smart display   {PUBLIC_BASE}/?mode=wall")
    print(f"  Admin           {PUBLIC_BASE}/admin   (PIN {ADMIN_PIN})")
    print(f"  Sensor endpoint {PUBLIC_BASE}/api/sensor/event")
    print("=" * 60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, **ssl)
