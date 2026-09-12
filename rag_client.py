"""RAG client for the AI Doctor (2026-08-24).

Wraps Qdrant + fastembed so the rest of the codebase can call:

  - get_rag_client()             — lazy-init the Qdrant client (None if down)
  - ensure_collection(client)    — create the collection if missing
  - chunk(text, max_chars, ovr)  — chunk text with overlap
  - index_patient_corpus(db, id) — bulk-index one patient's history + reports
  - index_chat_message(msg)      — index one chat message
  - retrieve(patient_id, query)  — top-k chunks for the given patient

Design notes
------------
* Single Qdrant collection `medical_rag` (cosine, 384-dim). Payload carries
  patient_id + source + doc_id + role + session_id + created_at.
* Patient isolation: every `retrieve()` call filters by `patient_id`. We
  NEVER query without that filter — the assistant can't see another
  patient's records.
* Graceful degradation: if Qdrant is unreachable, every public function
  short-circuits and returns nothing. Chat still works, just without
  retrieval. No HTTP 500 from chat endpoints when Qdrant is down.
* Embedding model is pinned via fastembed's TextEmbedding(model_name=...).
  Don't change RAG_MODEL_NAME without a migration plan.
"""
from __future__ import annotations

import logging
import threading
from typing import Iterable, Optional

import config
from sqlalchemy.orm import Session

logger = logging.getLogger("rag_client")

# Lazy / once-initialized handles. We don't import qdrant_client or
# fastembed at module top so a missing dependency doesn't break the rest
# of the app — the `import_rag_deps()` helper pulls them in lazily.
_client = None
_embedder = None
_init_lock = threading.Lock()
_UNAVAILABLE = False  # set to True after a connection failure so we don't
                       # keep retrying on every chat turn.


# ---------------------------------------------------------------------------
# Dependency loader (lazy)
# ---------------------------------------------------------------------------
def _import_qdrant():
    try:
        from qdrant_client import QdrantClient  # type: ignore
        from qdrant_client.http import models as qmodels  # type: ignore
        return QdrantClient, qmodels
    except Exception as exc:
        logger.warning("qdrant_client not available (%s) — RAG disabled.", exc)
        return None, None


def _import_fastembed():
    try:
        from fastembed import TextEmbedding  # type: ignore
        return TextEmbedding
    except Exception as exc:
        logger.warning("fastembed not available (%s) — RAG disabled.", exc)
        return None


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------
def get_rag_client():
    """Lazy-init the Qdrant client. Returns None if disabled or unreachable."""
    global _client, _UNAVAILABLE
    if _UNAVAILABLE:
        return None
    if _client is not None:
        return _client
    if not config.QDRANT_URL:
        _UNAVAILABLE = True
        return None
    with _init_lock:
        if _client is not None:
            return _client
        QdrantClient, _ = _import_qdrant()
        if QdrantClient is None:
            _UNAVAILABLE = True
            return None
        try:
            c = QdrantClient(
                url=config.QDRANT_URL,
                api_key=config.QDRANT_API_KEY,
                timeout=5.0,
            )
            # Probe the connection once so we know it's actually up.
            c.get_collections()
            _client = c
            logger.info("Qdrant client connected at %s", config.QDRANT_URL)
            return _client
        except Exception as exc:
            logger.warning("Qdrant unreachable at %s — RAG disabled (%s)",
                           config.QDRANT_URL, exc)
            _UNAVAILABLE = True
            return None


def get_embedder():
    """Lazy-init the fastembed TextEmbedding. Returns None if missing."""
    global _embedder, _UNAVAILABLE
    if _UNAVAILABLE:
        return None
    if _embedder is not None:
        return _embedder
    with _init_lock:
        if _embedder is not None:
            return _embedder
        TextEmbedding = _import_fastembed()
        if TextEmbedding is None:
            _UNAVAILABLE = True
            return None
        try:
            _embedder = TextEmbedding(model_name=config.RAG_MODEL_NAME)
            logger.info("fastembed model loaded: %s", config.RAG_MODEL_NAME)
            return _embedder
        except Exception as exc:
            logger.warning("fastembed failed to initialise (%s) — RAG disabled", exc)
            _UNAVAILABLE = True
            return None


def ensure_collection(client) -> bool:
    """Create the `medical_rag` collection if it doesn't exist. Returns
    True on success, False otherwise (incl. when disabled)."""
    if client is None:
        return False
    QdrantClient, qmodels = _import_qdrant()
    if QdrantClient is None or qmodels is None:
        return False
    try:
        existing = {c.name for c in client.get_collections().collections}
        if config.RAG_COLLECTION in existing:
            return True
        client.create_collection(
            collection_name=config.RAG_COLLECTION,
            vectors_config=qmodels.VectorParams(
                size=config.RAG_EMBED_DIM,
                distance=qmodels.Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant collection %s", config.RAG_COLLECTION)
        return True
    except Exception as exc:
        logger.warning("Could not ensure collection: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def chunk(text: str, max_chars: int = None, overlap: int = None) -> list[str]:
    """Split a long string into ~max_chars chunks with `overlap` chars of
    overlap. Whitespace-aware: prefers splitting on sentence boundaries
    (". " / "! " / "? " / "\n") so each chunk stays readable for the
    embedder."""
    text = (text or "").strip()
    if not text:
        return []
    max_chars = max_chars or config.RAG_CHUNK_CHARS
    overlap = overlap if overlap is not None else config.RAG_CHUNK_OVERLAP
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        # Try to back off to a sentence boundary if we can.
        if end < n:
            for sep in (". ", "! ", "? ", "\n"):
                idx = text.rfind(sep, start + max_chars // 2, end)
                if idx != -1:
                    end = idx + len(sep)
                    break
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(chunk_text)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------
def _embed(texts: list[str]) -> Optional[list[list[float]]]:
    embedder = get_embedder()
    if embedder is None or not texts:
        return None
    try:
        # fastembed returns a generator of numpy arrays; convert to lists.
        return [vec.tolist() if hasattr(vec, "tolist") else list(vec)
                for vec in embedder.embed(texts)]
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return None


def _upsert_points(client, points: list[dict]) -> int:
    """points: [{id, vector, payload}, ...]. Returns the number upserted."""
    if not points:
        return 0
    QdrantClient, qmodels = _import_qdrant()
    if QdrantClient is None or qmodels is None:
        return 0
    try:
        structs = [
            qmodels.PointStruct(
                id=p["id"],
                vector=p["vector"],
                payload=p["payload"],
            )
            for p in points
        ]
        client.upsert(collection_name=config.RAG_COLLECTION, points=structs)
        return len(structs)
    except Exception as exc:
        logger.warning("Qdrant upsert failed: %s", exc)
        return 0


def index_patient_corpus(db: Session, patient_id: int) -> int:
    """Bulk-index the patient's recent medical history + reports into
    Qdrant. Safe to call repeatedly — points are keyed by stable doc_ids
    so re-runs just overwrite. Returns the number of chunks indexed, or
    0 if RAG is disabled."""
    client = get_rag_client()
    if client is None:
        return 0
    if not ensure_collection(client):
        return 0
    # Lazy import models so rag_client can be imported standalone.
    import models

    points: list[dict] = []
    # 1. Medical history (last 20).
    histories = (
        db.query(models.MedicalHistoryEntry)
        .filter(models.MedicalHistoryEntry.patient_id == patient_id)
        .order_by(models.MedicalHistoryEntry.created_at.desc())
        .limit(20)
        .all()
    )
    for h in histories:
        body = (h.details or "").strip()
        if not body:
            continue
        for idx, ch in enumerate(chunk(body)):
            label = (h.title or "Medical history note").strip()
            text = f"[{label}] {ch}"
            points.append({
                "id": _stable_id(f"history:{h.id}#{idx}"),
                "vector": None,  # filled below
                "payload": {
                    "patient_id": patient_id,
                    "source": "history",
                    "doc_id": f"history:{h.id}",
                    "created_at": (h.created_at or _now()).isoformat(),
                    "text": text,
                },
            })
    # 2. Patient reports (last 10). Pull whatever free-text fields the
    # model exposes; `notes` is the human-written annotation, the title
    # alone is too short to embed but we use it as the chunk label.
    reports = (
        db.query(models.PatientReport)
        .filter(models.PatientReport.patient_id == patient_id)
        .order_by(models.PatientReport.uploaded_at.desc())
        .limit(10)
        .all()
    )
    for r in reports:
        body = (r.notes or "").strip()
        if not body:
            continue
        for idx, ch in enumerate(chunk(body)):
            label = (r.title or r.category or "Patient report").strip()
            text = f"[{label}] {ch}"
            points.append({
                "id": _stable_id(f"report:{r.id}#{idx}"),
                "vector": None,
                "payload": {
                    "patient_id": patient_id,
                    "source": "report",
                    "doc_id": f"report:{r.id}",
                    "created_at": (r.uploaded_at or _now()).isoformat(),
                    "text": text,
                },
            })
    return _finalize_and_upsert(points)


def index_chat_message(msg) -> int:
    """Index a single AIDoctorMessage row. msg must have id, session_id,
    patient_id (via session), role, content. Returns chunk count."""
    client = get_rag_client()
    if client is None:
        return 0
    if not ensure_collection(client):
        return 0
    content = (msg.content or "").strip()
    if not content or msg.role not in ("user", "assistant"):
        return 0
    patient_id = msg.session.patient_id if msg.session and msg.session.patient_id else None
    if not patient_id:
        return 0
    points: list[dict] = []
    for idx, ch in enumerate(chunk(content)):
        prefix = "Patient" if msg.role == "user" else "Assistant"
        text = f"[{prefix}] {ch}"
        points.append({
            "id": _stable_id(f"chat:msg:{msg.id}#{idx}"),
            "vector": None,
            "payload": {
                "patient_id": patient_id,
                "source": "chat",
                "doc_id": f"chat:msg:{msg.id}",
                "session_id": msg.session_id,
                "role": msg.role,
                "created_at": (msg.created_at or _now()).isoformat(),
                "text": text,
            },
        })
    return _finalize_and_upsert(points)


def _finalize_and_upsert(points: list[dict]) -> int:
    """Embed the `text` of each point and bulk-upsert."""
    if not points:
        return 0
    texts = [p["payload"]["text"] for p in points]
    vectors = _embed(texts)
    if not vectors or len(vectors) != len(points):
        return 0
    for p, v in zip(points, vectors):
        p["vector"] = v
    return _upsert_points(get_rag_client(), points)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def retrieve(patient_id: int, query: str, k: int = None) -> list[dict]:
    """Return up to k retrieved dicts for the given patient. Each dict
    has: {"text": str, "source": str, "doc_id": str, "score": float}.
    Always filtered by patient_id. Returns [] if RAG is disabled or no
    hits."""
    client = get_rag_client()
    if client is None:
        return []
    if not ensure_collection(client):
        return []
    if not query or not query.strip():
        return []
    k = k or config.RAG_TOP_K
    QdrantClient, qmodels = _import_qdrant()
    if QdrantClient is None or qmodels is None:
        return []
    vectors = _embed([query.strip()])
    if not vectors:
        return []
    try:
        results = client.search(
            collection_name=config.RAG_COLLECTION,
            query_vector=vectors[0],
            query_filter=qmodels.Filter(must=[
                qmodels.FieldCondition(
                    key="patient_id",
                    match=qmodels.MatchValue(value=patient_id),
                ),
            ]),
            limit=k,
            with_payload=True,
        )
    except Exception as exc:
        logger.warning("Qdrant search failed: %s", exc)
        return []
    out: list[dict] = []
    seen_docs: set[str] = set()
    for r in results:
        payload = r.payload or {}
        doc_id = payload.get("doc_id") or ""
        text = payload.get("text") or ""
        if doc_id in seen_docs or not text:
            continue
        seen_docs.add(doc_id)
        out.append({
            "text": text,
            "source": payload.get("source") or "unknown",
            "doc_id": doc_id,
            "score": float(r.score),
        })
    return out


def format_for_prompt(chunks: list[dict], max_chars: int = None) -> str:
    """Format retrieved chunks into a prompt-friendly block, capped at
    max_chars total. Each chunk is prefixed with [source:doc_id] so the
    model can quote it."""
    max_chars = max_chars or config.RAG_MAX_CONTEXT_CHARS
    if not chunks:
        return ""
    out: list[str] = []
    total = 0
    for c in chunks:
        label = f"[{c.get('source','unknown')}:{c.get('doc_id','')}]"
        line = f"{label} {c['text']}"
        if total + len(line) > max_chars and out:
            break
        out.append(line)
        total += len(line)
    if not out:
        return ""
    return "--- RELEVANT CONTEXT (from your medical records and past conversations) ---\n" + "\n...\n".join(out)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _stable_id(s: str) -> int:
    """Convert a string doc_id like 'history:123#0' to a stable 63-bit int
    suitable for Qdrant point ids. Uses Python's built-in hash with a
    fixed seed for stability across processes (Python salts it per
    process by default — we override PYTHONHASHSEED? No, we just use
    blake2b)."""
    import hashlib
    digest = hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False) & 0x7FFFFFFFFFFFFFFF


def _now():
    from datetime import datetime
    return datetime.utcnow()


def is_available() -> bool:
    """Quick health-check used by callers that want to log a single
    'RAG unavailable' warning at startup."""
    return get_rag_client() is not None and get_embedder() is not None