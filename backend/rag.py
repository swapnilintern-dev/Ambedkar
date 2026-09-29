"""
Archive core for the Dr. B. R. Ambedkar Digital Heritage Archive.

- Chunks documents page-by-page and keeps (title, page) with every chunk,
  so every AI answer can cite its exact source.
- Hybrid search: multilingual vector search (ChromaDB) + BM25 keyword search,
  fused with Reciprocal Rank Fusion.
- Pluggable LLM (Gemini / Anthropic / local Ollama). If no LLM is configured
  or the call fails, the assistant falls back to showing the most relevant
  passages, so the kiosk never breaks during a demo.
"""
import os
import re
import json
import threading
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
for sub in ["files", "texts", "scans", "pages", "tts", "compilations", "media", "chroma"]:
    (DATA / sub).mkdir(parents=True, exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
except Exception:
    pass

LANG_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi"}
EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")
TOKEN_RE = re.compile(r"[\w\u0900-\u097F]+")


def tokenize(text):
    return TOKEN_RE.findall(text.lower())


def normalise(text):
    text = text.replace("\r", "")
    text = re.sub(r"-\n(\w)", r"\1", text)          # join hyphenated line breaks
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)     # single newlines -> spaces
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def chunk_text(text, size=900, overlap=150):
    text = normalise(text)
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 1 <= size:
            cur = (cur + "\n" + p).strip()
            continue
        if cur:
            chunks.append(cur)
        while len(p) > size:
            cut = p.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            chunks.append(p[:cut].strip())
            p = p[max(cut - overlap, 1):].strip()
        cur = p
    if cur:
        chunks.append(cur)
    return chunks


class Archive:
    def __init__(self):
        import chromadb
        from sentence_transformers import SentenceTransformer

        self.client = chromadb.PersistentClient(path=str(DATA / "chroma"))
        self.col = self.client.get_or_create_collection("archive", metadata={"hnsw:space": "cosine"})
        print(f"[archive] loading embedding model {EMBED_MODEL} (first run downloads it)...")
        self.model = SentenceTransformer(EMBED_MODEL)
        self.catalog_path = DATA / "catalog.json"
        self.catalog = self._load(self.catalog_path, {})
        self.lock = threading.RLock()
        self._rebuild_bm25()
        print(f"[archive] ready: {len(self.catalog)} documents, {self.col.count()} chunks")

    @staticmethod
    def _load(path, default):
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            return default

    def save_catalog(self):
        self.catalog_path.write_text(json.dumps(self.catalog, ensure_ascii=False, indent=2), "utf-8")

    def _embed(self, texts, kind):
        prefix = "query: " if kind == "query" else "passage: "   # e5 models expect these prefixes
        return self.model.encode(
            [prefix + t for t in texts], normalize_embeddings=True,
            batch_size=32, show_progress_bar=len(texts) > 64,
        ).tolist()

    def _rebuild_bm25(self):
        from rank_bm25 import BM25Okapi
        if self.col.count():
            got = self.col.get(include=["documents", "metadatas"])
        else:
            got = {"ids": [], "documents": [], "metadatas": []}
        self.bm_ids, self.bm_docs, self.bm_meta = got["ids"], got["documents"], got["metadatas"]
        self.bm25 = BM25Okapi([tokenize(d) or ["_"] for d in self.bm_docs]) if self.bm_docs else None

    def add_document(self, meta, pages):
        """meta: catalog record (must include id, title). pages: list of (page_no, text)."""
        doc_id = meta["id"]
        with self.lock:
            try:
                self.col.delete(where={"doc_id": doc_id})
            except Exception:
                pass
            ids, docs, metas = [], [], []
            for page_no, text in pages:
                for i, ch in enumerate(chunk_text(text)):
                    ids.append(f"{doc_id}:{page_no}:{i}")
                    docs.append(ch)
                    metas.append({"doc_id": doc_id, "page": int(page_no), "title": meta["title"], "chunk": i})
            for s in range(0, len(ids), 128):
                self.col.upsert(
                    ids=ids[s:s + 128], documents=docs[s:s + 128], metadatas=metas[s:s + 128],
                    embeddings=self._embed(docs[s:s + 128], "passage"),
                )
            (DATA / "texts" / f"{doc_id}.json").write_text(
                json.dumps({str(p): t for p, t in pages}, ensure_ascii=False), "utf-8")
            page_nums = [int(p) for p, _ in pages] or [1]
            meta.update({"first_page": min(page_nums), "last_page": max(page_nums), "chunks": len(ids)})
            self.catalog[doc_id] = meta
            self.save_catalog()
            self._rebuild_bm25()
        return meta

    def page_text(self, doc_id, page):
        texts = self._load(DATA / "texts" / f"{doc_id}.json", {})
        return texts.get(str(page), "")

    def search(self, query, k=6):
        with self.lock:
            n = self.col.count()
            if n == 0 or not query.strip():
                return []
            depth = min(25, n)
            qv = self._embed([query], "query")[0]
            vec = self.col.query(query_embeddings=[qv], n_results=depth, include=["documents", "metadatas"])
            scores, store = {}, {}
            for rank, (cid, doc, m) in enumerate(zip(vec["ids"][0], vec["documents"][0], vec["metadatas"][0])):
                scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
                store[cid] = (doc, m)
            if self.bm25:
                bs = self.bm25.get_scores(tokenize(query) or ["_"])
                top = sorted(range(len(bs)), key=lambda i: bs[i], reverse=True)[:depth]
                for rank, i in enumerate(top):
                    if bs[i] <= 0:
                        break
                    cid = self.bm_ids[i]
                    scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
                    store[cid] = (self.bm_docs[i], self.bm_meta[i])
            best = sorted(scores, key=scores.get, reverse=True)[:k]
            return [{
                "id": c, "text": store[c][0], "doc_id": store[c][1]["doc_id"],
                "title": store[c][1]["title"], "page": store[c][1]["page"],
            } for c in best]


# ---------------------------------------------------------------- LLM

def llm(prompt, system="", max_tokens=1500):
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    key = os.getenv("LLM_API_KEY", "").strip()
    if not provider or (provider != "ollama" and not key):
        return None
    try:
        if provider == "gemini":
            model = os.getenv("LLM_MODEL", "gemini-2.5-flash")
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens * 2},
                },
                timeout=60,
            )
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip() or None
        if provider == "anthropic":
            model = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text").strip() or None
        if provider == "ollama":
            model = os.getenv("LLM_MODEL", "llama3.1:8b")
            r = requests.post(
                os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
                json={"model": model, "stream": False, "options": {"temperature": 0.2},
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
                timeout=240,
            )
            r.raise_for_status()
            return r.json()["message"]["content"].strip() or None
    except Exception as e:
        print(f"[llm] {provider} call failed: {e}")
    return None


# ---------------------------------------------------------------- translation

_tcache = {}


def translate(text, lang):
    if not text or lang not in LANG_NAMES:
        return text
    key = (lang, text)
    if key in _tcache:
        return _tcache[key]
    out = None
    try:
        from deep_translator import GoogleTranslator
        tr = GoogleTranslator(source="auto", target=lang)
        parts, cur = [], ""
        for para in text.split("\n\n"):
            if len(cur) + len(para) > 4000 and cur:
                parts.append(cur)
                cur = ""
            cur = (cur + "\n\n" + para).strip()
        if cur:
            parts.append(cur)
        out = "\n\n".join((tr.translate(p[:4900]) or "") for p in parts)
    except Exception as e:
        print(f"[translate] web translator failed ({e}); trying LLM")
        out = llm(f"Translate the following text into {LANG_NAMES[lang]}. Return only the translation.\n\n{text}")
    out = out or text
    _tcache[key] = out
    return out


# ---------------------------------------------------------------- assistant

SYSTEM_PROMPT = """You are the research assistant of the Dr. B. R. Ambedkar Digital Heritage Archive, used on public kiosks by students, researchers and visitors.

Rules:
1. Answer ONLY from the numbered archive passages provided. Do not use outside knowledge.
2. Cite every claim with its passage number in square brackets, for example [2].
3. If the passages do not contain the answer, say plainly that the archive collection does not yet hold enough material on this, and suggest a related topic the visitor could explore.
4. Never speak as Dr. Ambedkar and never invent quotations. Quote only words that appear in the passages, inside quotation marks.
5. Be factual and respectful. Do not give opinions about present-day political parties or candidates.
6. Write for a general visitor: clear language, 120-220 words, short paragraphs, no markdown headings."""

NOT_FOUND = {
    "en": "The archive does not have material on this yet. Try asking about his writings, his speeches or the Constituent Assembly debates.",
    "hi": "अभिलेखागार में अभी इस विषय पर सामग्री नहीं है। उनके लेखन, भाषणों या संविधान सभा की बहसों के बारे में पूछकर देखें।",
    "mr": "अभिलेखागारात अद्याप या विषयावर साहित्य नाही. त्यांचे लेखन, भाषणे किंवा संविधान सभेतील चर्चांबद्दल विचारून पहा.",
}
EXTRACTIVE_HEADER = {
    "en": "The AI assistant is offline, so here are the most relevant passages from the archive:",
    "hi": "AI सहायक अभी ऑफ़लाइन है, इसलिए अभिलेखागार के सबसे प्रासंगिक अंश यहाँ हैं:",
    "mr": "AI सहाय्यक सध्या ऑफलाइन आहे, म्हणून अभिलेखागारातील सर्वात संबंधित उतारे येथे आहेत:",
}


def answer(archive, question, lang="en"):
    lang = lang if lang in LANG_NAMES else "en"
    hits = archive.search(question, k=6)
    if not hits:
        return {"answer": NOT_FOUND[lang], "sources": [], "mode": "none"}
    context = "\n\n".join(f"[{i + 1}] {h['title']}, page {h['page']}:\n{h['text']}" for i, h in enumerate(hits))
    prompt = (f"Archive passages:\n\n{context}\n\n---\nVisitor's question: {question}\n\n"
              f"Write the answer in {LANG_NAMES[lang]}. Keep the [n] citation markers as digits.")
    sources = [{"n": i + 1, "doc_id": h["doc_id"], "title": h["title"], "page": h["page"], "text": h["text"]}
               for i, h in enumerate(hits)]
    out = llm(prompt, SYSTEM_PROMPT)
    if out:
        return {"answer": out, "sources": sources, "mode": "ai"}
    lines = [EXTRACTIVE_HEADER[lang]]
    for i, h in enumerate(hits[:3]):
        snippet = h["text"][:500] + ("..." if len(h["text"]) > 500 else "")
        lines.append(f"[{i + 1}] " + (translate(snippet, lang) if lang != "en" else snippet))
    return {"answer": "\n\n".join(lines), "sources": sources, "mode": "extractive"}


def summarize(text, lang="en"):
    lang = lang if lang in LANG_NAMES else "en"
    if not text.strip():
        return ""
    out = llm(
        f"Summarize this page from the archive for a museum visitor in {LANG_NAMES[lang]}, "
        f"in 4-6 short sentences. Use only the text given and do not add facts.\n\n{text[:8000]}",
        "You summarize archival documents accurately and neutrally.",
    )
    if out:
        return out
    sentences = re.split(r"(?<=[.!?।])\s+", normalise(text))
    fallback = " ".join(sentences[:4])[:700]
    return translate(fallback, lang) if lang != "en" else fallback


def tag_metadata(text):
    """LLM-assisted metadata tagging for newly digitized pages. Returns dict (may be empty)."""
    out = llm(
        "Read this OCR text from an archival document and return ONLY a JSON object with keys: "
        '"title" (short descriptive title), "subjects" (up to 5 topic keywords), '
        '"date" (year or date mentioned as the document date, or ""), "summary" (one sentence).\n\n'
        + text[:5000],
        "You are an archivist creating Dublin Core metadata. Output valid JSON only.",
        max_tokens=400,
    )
    if not out:
        return {}
    m = re.search(r"\{.*\}", out, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}
