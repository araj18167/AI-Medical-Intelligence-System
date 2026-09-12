"""AI Doctor tool declarations (2026-08-24, Phase 4).

Four Gemini function declarations the AI Doctor can call when the user
asks for things like "save that to my record", "remind me at 8pm",
"side effects of amoxicillin", or "I'm having chest pain". Each tool
returns a JSON dict that gets fed back to Gemini as a function
response. The dispatcher table also runs the actual side effect (DB
write, etc.) so the WS handler can render audit rows.

Defence in depth: every dispatcher ignores model-passed `patient_id`
and pins to the caller's own patient_id. Hospital IDs are validated
against the Hospital table before any preview is built.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable

import gemini_client
import models
from sqlalchemy.orm import Session

logger = logging.getLogger("medical_ai")

MAX_TOOL_ROUNDS = 3  # enforced by run_with_tools()


# ---------------------------------------------------------------------------
# Tool declarations (sent to Gemini via GenerateContentConfig(tools=[...])).
# ---------------------------------------------------------------------------

TOOL_DECLARATIONS = [
    {
        "name": "save_to_patient_record",
        "description": (
            "Append a clinical note (visit summary, prescription list, "
            "or general medical history entry) to the calling patient's "
            "MedicalHistory table. Use this when the user explicitly asks "
            "the assistant to save something to their record, or when a "
            "visit summary should be persisted."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short label for the entry (e.g. 'Metformin 500mg', 'Visit summary 2026-08-24').",
                },
                "details": {
                    "type": "string",
                    "description": "Free-text body the doctor or patient would see in their history.",
                },
                "category": {
                    "type": "string",
                    "enum": ["condition", "allergy", "surgery", "medication", "family_history", "immunization", "other"],
                    "description": "Optional. Defaults to 'other'.",
                },
                "is_ongoing": {
                    "type": "boolean",
                    "description": "True for chronic / ongoing entries (e.g. diabetes diagnosis).",
                },
            },
            "required": ["title", "details"],
        },
    },
    {
        "name": "create_reminder",
        "description": (
            "Schedule a medicine reminder for the calling patient. Use this "
            "when the user wants to be reminded to take medication, attend "
            "an appointment, or do any recurring health task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "medicine_name": {
                    "type": "string",
                    "description": "Name of the medicine (or short label like 'Metformin').",
                },
                "dosage": {
                    "type": "string",
                    "description": "Free-text dose, e.g. '500mg'.",
                },
                "times": {
                    "type": "string",
                    "description": "Comma-separated HH:MM times, e.g. '08:00,20:00'.",
                },
            },
            "required": ["medicine_name", "times"],
        },
    },
    {
        "name": "lookup_medicine",
        "description": (
            "Look up information about a medicine: typical uses, common "
            "side effects, notable interactions, and any safety notes. "
            "Use this when the patient asks what a medication does or "
            "whether two drugs can be taken together."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The medicine name (generic or brand).",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "trigger_emergency_admit",
        "description": (
            "PREPARE an emergency admission. This tool NEVER auto-submits "
            "the admission — it returns a preview the user must explicitly "
            "confirm by clicking an 'Admit me' card in the chat. Use this "
            "when the patient describes an emergency (chest pain, severe "
            "bleeding, stroke symptoms, severe allergic reaction, etc.) "
            "or explicitly asks to be admitted."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hospital_id": {
                    "type": "integer",
                    "description": "Optional target hospital. If omitted, the system picks the closest or first available.",
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason (1-2 sentences) for the admission, suitable for the confirmation card.",
                },
            },
            "required": ["reason"],
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatcher — the actual side-effecting logic for each tool.
# ---------------------------------------------------------------------------

def _save_to_patient_record(db: Session, user: models.User, args: dict) -> dict:
    """Append a MedicalHistoryEntry. patient_id is forced to the caller's
    own record regardless of what the model sends — defence in depth."""
    if not user.patient_id:
        return {"ok": False, "error": "Your account isn't linked to a patient record."}
    title = (args.get("title") or "").strip()[:200]
    details = (args.get("details") or "").strip()
    if not title or not details:
        return {"ok": False, "error": "title and details are required"}
    category = (args.get("category") or "other").strip().lower()
    allowed = {"condition", "allergy", "surgery", "medication", "family_history", "immunization", "other"}
    if category not in allowed:
        category = "other"
    is_ongoing = bool(args.get("is_ongoing"))
    try:
        entry = models.MedicalHistoryEntry(
            patient_id=user.patient_id,
            category=category,
            title=title,
            details=details,
            is_ongoing=is_ongoing,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
    except Exception as exc:
        logger.warning("save_to_patient_record failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"db_error: {exc}"}
    return {"ok": True, "history_entry_id": entry.id, "category": category}


def _create_reminder(db: Session, user: models.User, args: dict) -> dict:
    """Insert a Reminder row for the calling patient."""
    if not user.patient_id:
        return {"ok": False, "error": "Your account isn't linked to a patient record."}
    medicine_name = (args.get("medicine_name") or "").strip()[:200]
    times_raw = (args.get("times") or "").strip()
    dosage = (args.get("dosage") or "").strip() or None
    if not medicine_name or not times_raw:
        return {"ok": False, "error": "medicine_name and times are required"}
    # Light validation on times — accept HH:MM,HH:MM or single HH:MM.
    parts = [t.strip() for t in times_raw.split(",") if t.strip()]
    if not parts:
        return {"ok": False, "error": "times must be at least one HH:MM"}
    clean_parts: list[str] = []
    for t in parts:
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError:
            return {"ok": False, "error": f"Invalid time format: {t!r}. Use HH:MM (24-hour)."}
        clean_parts.append(t)
    times_str = ",".join(clean_parts)
    try:
        reminder = models.Reminder(
            patient_id=user.patient_id,
            medicine_name=medicine_name,
            dosage=dosage,
            times=times_str,
            is_active=True,
        )
        db.add(reminder)
        db.commit()
        db.refresh(reminder)
    except Exception as exc:
        logger.warning("create_reminder failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"db_error: {exc}"}
    return {
        "ok": True,
        "reminder_id": reminder.id,
        "medicine_name": reminder.medicine_name,
        "dosage": reminder.dosage,
        "times": reminder.times,
    }


def _lookup_medicine(db: Session, user: models.User, args: dict) -> dict:
    """Look up medicine info via Gemini. The DB doesn't have a medicine
    info table — the existing /medicine-info endpoint also goes to Gemini,
    so this is just a thin wrapper that includes the caller's name for
    tone."""
    name = (args.get("name") or "").strip()
    if not name:
        return {"found": False, "error": "name is required"}
    try:
        info = gemini_client.get_medicine_info(name) or {}
    except Exception as exc:
        logger.warning("lookup_medicine gemini failed: %s", exc)
        return {"found": False, "name": name, "error": f"lookup_failed: {exc}"}
    return {
        "found": bool(info),
        "name": name,
        "info": info if isinstance(info, dict) else {"raw": str(info)},
    }


def _trigger_emergency_admit(db: Session, user: models.User, args: dict) -> dict:
    """Build a preview payload for the confirmation card. NEVER auto-submits.
    The user must click the in-chat confirmation card which then hits
    POST /emergency/admit separately."""
    if not user.patient_id:
        return {
            "requires_user_confirmation": True,
            "ok": False,
            "error": "Your account isn't linked to a patient record.",
        }
    reason = (args.get("reason") or "").strip()[:400]
    if not reason:
        return {
            "requires_user_confirmation": True,
            "ok": False,
            "error": "reason is required",
        }
    hospital_id = args.get("hospital_id")
    hospital_name = None
    if hospital_id is not None:
        try:
            h = db.query(models.Hospital).filter(models.Hospital.id == int(hospital_id)).first()
        except Exception:
            h = None
        if not h:
            return {
                "requires_user_confirmation": True,
                "ok": False,
                "error": f"hospital_id={hospital_id} not found",
            }
        hospital_id = h.id
        hospital_name = h.name
    return {
        "requires_user_confirmation": True,
        "ok": True,
        "hospital_id": hospital_id,
        "hospital_name": hospital_name,
        "patient_id": user.patient_id,
        "reason": reason,
        "prepared_at": datetime.utcnow().isoformat() + "Z",
        "message": (
            "Emergency admission prepared. The user MUST click the confirmation card "
            "in the chat before anything is submitted."
        ),
    }


DISPATCH: dict[str, Callable[[Session, models.User, dict], dict]] = {
    "save_to_patient_record": _save_to_patient_record,
    "create_reminder": _create_reminder,
    "lookup_medicine": _lookup_medicine,
    "trigger_emergency_admit": _trigger_emergency_admit,
}


# ---------------------------------------------------------------------------
# Tool-result → audit row helper.
# ---------------------------------------------------------------------------
def tool_audit_text(name: str, args: dict, result: dict) -> str:
    """Build the JSON string we drop into AIDoctorMessage(role='system') as
    the audit trail for one tool call."""
    try:
        return json.dumps({"tool": name, "args": args, "result": result}, default=str)
    except Exception:
        return json.dumps({"tool": name, "args": str(args), "result": str(result)})
