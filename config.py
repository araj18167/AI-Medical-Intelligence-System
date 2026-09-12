
NVIDIA_OCR_KEY = "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"
NVIDIA_OMNI_KEY = "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"

NVIDIA_API_KEY = "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"

GEMINI_API_KEY = "AQ.Ab8RN6J3bfPm0WjcCC0FZ5SieUATEIVsfAO3DIEicjic6-CSsA"


SECRET_KEY = "180918"


# Folder where uploaded patient reports (PDFs, images) are stored.
# Created automatically at startup if it doesn't exist.
UPLOAD_DIR = "./uploads"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

# Google OAuth 2.0 Client ID for "Sign in with Google".
# Create one in Google Cloud Console → APIs & Services → Credentials
# (OAuth client ID, application type: Web application). Then add the
# dev origins (e.g. http://127.0.0.1:5500) under "Authorized JavaScript origins".
# Leave blank to disable Google sign-in (the button will show a friendly notice).
GOOGLE_CLIENT_ID = ""

# ---- 2026-08-24: AI Doctor v2 (Qdrant RAG + streaming + tools) -----------
# All optional. If QDRANT_URL is unreachable, the AI Doctor falls back to
# no-RAG mode and a single warning is logged. Set QDRANT_URL to "" to
# disable RAG entirely.
import os
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None

# Vector DB collection name + embedding model version. Both are pinned so
# fastembed upgrades don't silently invalidate stored vectors.
RAG_COLLECTION = "medical_rag"
RAG_MODEL_NAME = "BAAI/bge-small-en-v1.5"
RAG_EMBED_DIM = 384

# How many top chunks to pull per turn, and the chunk size used at index
# time. ~500 chars matches ~100 tokens which is a good tradeoff for
# clinical notes (short sentences with medical terms).
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "6"))
RAG_CHUNK_CHARS = int(os.getenv("RAG_CHUNK_CHARS", "500"))
RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "50"))
RAG_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "1500"))

# Optional one-shot: index every patient's medical history on startup. Off
# by default — flip to "true" for a single boot, then back to "false".
RAG_BACKFILL_ON_START = os.getenv("RAG_BACKFILL_ON_START", "false").lower() == "true"

# Streaming + tool caps. Both default to ON / 3 rounds so the upgrade is
# opt-out for operators who want the old behaviour.
AI_DOCTOR_STREAMING_ENABLED = os.getenv("AI_DOCTOR_STREAMING_ENABLED", "true").lower() == "true"
AI_DOCTOR_MAX_TOOL_ROUNDS = int(os.getenv("AI_DOCTOR_MAX_TOOL_ROUNDS", "3"))
# ---- end AI Doctor v2 config ---------------------------------------------
# ---- 2026-09-02: Evidence-Based AI Doctor ---------------------------------
# Enable/disable external medical knowledge fetching (MedlinePlus, RxNorm, etc.)
# When disabled, AI Doctor still works with patient context + RAG only.
EVIDENCE_SERVICE_ENABLED = os.getenv("EVIDENCE_SERVICE_ENABLED", "true").lower() == "true"
# Max characters for the combined patient evidence block in the prompt.
EVIDENCE_MAX_CONTEXT_CHARS = int(os.getenv("EVIDENCE_MAX_CONTEXT_CHARS", "4000"))
# Max characters for external medical evidence block.
MEDICAL_KNOWLEDGE_MAX_CHARS = int(os.getenv("MEDICAL_KNOWLEDGE_MAX_CHARS", "2000"))
# ---- end Evidence-Based AI Doctor config ----------------------------------
