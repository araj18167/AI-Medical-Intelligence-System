"""
AI Doctor Tools Registry (2026-09-02)
======================================
Selective patient-data retrieval tools. Each tool fetches ONLY the data
its specific intent requires — never the entire patient database.

Tools are called ONLY when the intent router determines they're needed.
For NORMAL_CHAT or GENERAL_MEDICAL, NO tools are called — zero patient
data is retrieved.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session
import models

logger = logging.getLogger("ai_doctor_tools")


# ===========================================================================
# Tool: Get Patient History
# ===========================================================================

def get_patient_history(db: Session, patient_id: int, query: str = "") -> dict:
    """Retrieve patient's medical history. Only called when the user
    explicitly asks about their history/records."""
    result = {"tool": "get_patient_history", "data": None}

    # Recent medical history (last 10 entries)
    histories = (
        db.query(models.MedicalHistoryEntry)
        .filter(models.MedicalHistoryEntry.patient_id == patient_id)
        .order_by(models.MedicalHistoryEntry.created_at.desc())
        .limit(10)
        .all()
    )

    entries = []
    for h in histories:
        entries.append({
            "title": h.title or "",
            "details": h.details or "",
            "category": h.category or "",
            "is_ongoing": getattr(h, "is_ongoing", False),
            "date": h.created_at.isoformat() if h.created_at else None,
        })

    # Recent prescriptions
    prescriptions = (
        db.query(models.Prescription)
        .filter(models.Prescription.patient_id == patient_id)
        .order_by(models.Prescription.created_at.desc())
        .limit(10)
        .all()
    )
    rx_list = []
    for rx in prescriptions:
        rx_list.append({
            "medicine": rx.medicine_name or "",
            "dosage": rx.dosage or "",
            "instructions": rx.instructions or "",
            "date": rx.created_at.isoformat() if rx.created_at else None,
        })

    # Recent admissions
    admissions = (
        db.query(models.Admission)
        .filter(models.Admission.patient_id == patient_id)
        .order_by(models.Admission.admitted_at.desc())
        .limit(5)
        .all()
    )
    adm_list = []
    for adm in admissions:
        adm_list.append({
            "status": "active" if adm.discharged_at is None else "discharged",
            "notes": adm.notes or "",
            "date": adm.admitted_at.isoformat() if adm.admitted_at else None,
        })

    result["data"] = {
        "medical_history": entries,
        "prescriptions": rx_list,
        "admissions": adm_list,
    }
    return result


# ===========================================================================
# Tool: Get Latest Report
# ===========================================================================

def get_latest_report(db: Session, patient_id: int) -> dict:
    """Retrieve the patient's latest lab report / test results."""
    result = {"tool": "get_latest_report", "data": None}

    # Latest MediScan lab report
    scan = (
        db.query(models.DocumentScan)
        .filter(
            models.DocumentScan.patient_id == patient_id,
            models.DocumentScan.doc_type == "lab_report",
        )
        .order_by(models.DocumentScan.created_at.desc())
        .first()
    )

    lab_values = []
    if scan:
        values = (
            db.query(models.LabValue)
            .filter(models.LabValue.scan_id == scan.id)
            .all()
        )
        for lv in values:
            lab_values.append({
                "test_name": lv.test_name or "",
                "value": lv.value_numeric if lv.value_numeric is not None else (lv.value_text or ""),
                "unit": lv.unit or "",
                "reference_range": lv.reference_range or "",
                "flag": lv.flag or "unknown",
            })

    # Also check hospital lab system
    lab_results = (
        db.query(models.LabResult)
        .join(models.LabOrder, models.LabOrder.id == models.LabResult.order_id)
        .filter(models.LabOrder.patient_id == patient_id)
        .order_by(models.LabResult.created_at.desc())
        .limit(20)
        .all()
    )
    for lr in lab_results:
        lab_values.append({
            "test_name": lr.test_name or "",
            "value": lr.result_value or "",
            "unit": lr.unit or "",
            "reference_range": lr.reference_range or "",
            "flag": lr.flag or "unknown",
            "source": "hospital_lab",
        })

    result["data"] = {
        "scan_file": scan.file_name if scan else None,
        "scan_date": scan.created_at.isoformat() if scan else None,
        "scan_summary": scan.summary if scan else None,
        "lab_values": lab_values,
    }
    return result


# ===========================================================================
# Tool: Get Previous Reports (for comparison)
# ===========================================================================

def get_previous_reports(db: Session, patient_id: int, test_name: str = "") -> dict:
    """Retrieve multiple past lab reports for comparison/trend analysis."""
    result = {"tool": "get_previous_reports", "data": None}

    scans = (
        db.query(models.DocumentScan)
        .filter(
            models.DocumentScan.patient_id == patient_id,
            models.DocumentScan.doc_type == "lab_report",
        )
        .order_by(models.DocumentScan.created_at.desc())
        .limit(5)
        .all()
    )

    reports = []
    for scan in scans:
        values = (
            db.query(models.LabValue)
            .filter(models.LabValue.scan_id == scan.id)
            .all()
        )
        test_data = []
        for lv in values:
            if test_name and test_name.lower() not in (lv.test_name or "").lower():
                continue
            test_data.append({
                "test_name": lv.test_name or "",
                "value": lv.value_numeric if lv.value_numeric is not None else (lv.value_text or ""),
                "unit": lv.unit or "",
                "flag": lv.flag or "unknown",
            })
        reports.append({
            "date": scan.created_at.isoformat() if scan.created_at else None,
            "file_name": scan.file_name or "",
            "summary": scan.summary or "",
            "tests": test_data,
        })

    result["data"] = {"reports": reports}
    return result


# ===========================================================================
# Tool: Get MediScan Results
# ===========================================================================

def get_mediscan_results(db: Session, patient_id: int, doc_type: str = "imaging") -> dict:
    """Retrieve stored MediScan analysis results for imaging/radiology."""
    result = {"tool": "get_mediscan_results", "data": None}

    scans = (
        db.query(models.DocumentScan)
        .filter(
            models.DocumentScan.patient_id == patient_id,
            models.DocumentScan.doc_type == doc_type,
        )
        .order_by(models.DocumentScan.created_at.desc())
        .limit(5)
        .all()
    )

    scan_data = []
    for scan in scans:
        extracted = None
        if scan.extracted_json:
            try:
                extracted = json.loads(scan.extracted_json)
            except Exception:
                extracted = None
        scan_data.append({
            "id": scan.id,
            "file_name": scan.file_name or "",
            "date": scan.created_at.isoformat() if scan.created_at else None,
            "summary": scan.summary or "",
            "extracted": extracted,
        })

    # Also check hospital radiology
    radiology = (
        db.query(models.RadiologyReport)
        .join(models.RadiologyOrder, models.RadiologyOrder.id == models.RadiologyReport.order_id)
        .filter(models.RadiologyOrder.patient_id == patient_id)
        .order_by(models.RadiologyReport.reported_at.desc().nullslast())
        .limit(5)
        .all()
    )
    for rr in radiology:
        scan_data.append({
            "findings": rr.findings or "",
            "impression": rr.impression or "",
            "source": "hospital_radiology",
            "date": rr.reported_at.isoformat() if rr.reported_at else None,
        })

    result["data"] = {"scans": scan_data}
    return result


# ===========================================================================
# Tool: Get Symptom Checker Results
# ===========================================================================

def get_symptom_checker_results(db: Session, patient_id: int) -> dict:
    """Retrieve stored Symptom Checker results."""
    result = {"tool": "get_symptom_checker_results", "data": None}

    # Check for symptom checker related documents
    scans = (
        db.query(models.DocumentScan)
        .filter(
            models.DocumentScan.patient_id == patient_id,
            models.DocumentScan.doc_type == "symptom_check",
        )
        .order_by(models.DocumentScan.created_at.desc())
        .limit(5)
        .all()
    )

    results_list = []
    for scan in scans:
        extracted = None
        if scan.extracted_json:
            try:
                extracted = json.loads(scan.extracted_json)
            except Exception:
                extracted = None
        results_list.append({
            "date": scan.created_at.isoformat() if scan.created_at else None,
            "summary": scan.summary or "",
            "data": extracted,
        })

    result["data"] = {"symptom_checks": results_list}
    return result


# ===========================================================================
# Tool: Get Current Medicines
# ===========================================================================

def get_current_medicines(db: Session, patient_id: int) -> dict:
    """Retrieve the patient's current medications — ONLY when explicitly asked."""
    result = {"tool": "get_current_medicines", "data": None}

    # From medical history (medication category)
    histories = (
        db.query(models.MedicalHistoryEntry)
        .filter(
            models.MedicalHistoryEntry.patient_id == patient_id,
            models.MedicalHistoryEntry.category == "medication",
        )
        .order_by(models.MedicalHistoryEntry.created_at.desc())
        .limit(10)
        .all()
    )

    meds = []
    for h in histories:
        meds.append({
            "name": h.title or "",
            "details": h.details or "",
            "is_ongoing": getattr(h, "is_ongoing", False),
            "date": h.created_at.isoformat() if h.created_at else None,
        })

    # From prescriptions
    prescriptions = (
        db.query(models.Prescription)
        .filter(models.Prescription.patient_id == patient_id)
        .order_by(models.Prescription.created_at.desc())
        .limit(10)
        .all()
    )
    for rx in prescriptions:
        meds.append({
            "medicine_name": rx.medicine_name or "",
            "dosage": rx.dosage or "",
            "instructions": rx.instructions or "",
            "date": rx.created_at.isoformat() if rx.created_at else None,
            "source": "prescription",
        })

    result["data"] = {"current_medicines": meds}
    return result


# ===========================================================================
# Tool: Get Patient Context (demographics only)
# ===========================================================================

def get_patient_context(db: Session, patient_id: int) -> dict:
    """Retrieve minimal patient demographics — only when patient-specific
    questions are asked."""
    result = {"tool": "get_patient_context", "data": None}

    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        result["data"] = {"error": "Patient not found"}
        return result

    profile = (
        db.query(models.PatientProfile)
        .filter(models.PatientProfile.patient_id == patient_id)
        .first()
    )

    ctx = {
        "age": patient.age,
        "gender": patient.gender or "",
    }
    if profile:
        for attr in ("blood_group", "allergies", "chronic_conditions"):
            val = getattr(profile, attr, None)
            if val:
                ctx[attr] = val

    # Allergies from history
    allergies = (
        db.query(models.MedicalHistoryEntry)
        .filter(
            models.MedicalHistoryEntry.patient_id == patient_id,
            models.MedicalHistoryEntry.category == "allergy",
        )
        .all()
    )
    if allergies:
        ctx["allergies"] = [a.title for a in allergies if a.title]

    result["data"] = ctx
    return result


# ===========================================================================
# Tool: Search Medicine (general — no patient data)
# ===========================================================================

def search_medicine(query: str, db: Session = None) -> dict:
    """Search for medicine information from authoritative sources.
    This is a general tool — does NOT use patient data."""
    result = {"tool": "search_medicine", "data": None}
    try:
        import medical_knowledge_service as mks
        search_result = mks.search_medicine(query, db)
        result["data"] = search_result
    except Exception as exc:
        logger.debug("Medicine search failed: %s", exc)
        result["data"] = {"error": str(exc)}
    return result


# ===========================================================================
# Tool: Check Interactions
# ===========================================================================

def check_interactions(medicine: str, other_medicine: str = "", food: str = "", db: Session = None) -> dict:
    """Check drug-drug or food-drug interactions. General tool — no patient data."""
    result = {"tool": "check_interactions", "data": None}
    try:
        import medical_knowledge_service as mks
        if food:
            interaction = mks.check_food_drug_interaction(medicine, food, db)
        else:
            interaction = mks.search_medicine(medicine, db)
        result["data"] = interaction
    except Exception as exc:
        logger.debug("Interaction check failed: %s", exc)
        result["data"] = {"error": str(exc)}
    return result


# ===========================================================================
# Tool Dispatcher
# ===========================================================================

def execute_tools(
    tool_names: list[str],
    db: Session,
    patient_id: int,
    user_query: str = "",
) -> dict:
    """Execute the requested tools and return combined results.
    
    Returns: {"tools_called": [...], "results": {tool_name: result_data}, "context_text": "..."}
    """
    results = {}
    context_parts = []

    for tool_name in tool_names:
        try:
            if tool_name == "get_patient_history":
                r = get_patient_history(db, patient_id, user_query)
                results[tool_name] = r.get("data")
                if r.get("data"):
                    context_parts.append(_format_history_for_prompt(r["data"]))

            elif tool_name == "get_latest_report":
                r = get_latest_report(db, patient_id)
                results[tool_name] = r.get("data")
                if r.get("data"):
                    context_parts.append(_format_report_for_prompt(r["data"]))

            elif tool_name == "get_previous_reports":
                # Extract test name from query if present
                test_name = _extract_test_name(user_query)
                r = get_previous_reports(db, patient_id, test_name)
                results[tool_name] = r.get("data")
                if r.get("data"):
                    context_parts.append(_format_reports_for_prompt(r["data"]))

            elif tool_name == "get_mediscan_results":
                r = get_mediscan_results(db, patient_id)
                results[tool_name] = r.get("data")
                if r.get("data"):
                    context_parts.append(_format_mediscan_for_prompt(r["data"]))

            elif tool_name == "get_symptom_checker_results":
                r = get_symptom_checker_results(db, patient_id)
                results[tool_name] = r.get("data")
                if r.get("data"):
                    context_parts.append(_format_symptom_checker_for_prompt(r["data"]))

            elif tool_name == "get_current_medicines":
                r = get_current_medicines(db, patient_id)
                results[tool_name] = r.get("data")
                if r.get("data"):
                    context_parts.append(_format_medicines_for_prompt(r["data"]))

            elif tool_name == "get_patient_context":
                r = get_patient_context(db, patient_id)
                results[tool_name] = r.get("data")
                if r.get("data"):
                    context_parts.append(_format_patient_context_for_prompt(r["data"]))

            elif tool_name == "search_medicine":
                r = search_medicine(user_query, db)
                results[tool_name] = r.get("data")
                if r.get("data"):
                    context_parts.append(_format_medicine_search_for_prompt(r["data"]))

            elif tool_name == "check_interactions":
                r = check_interactions(user_query, db=db)
                results[tool_name] = r.get("data")
                if r.get("data"):
                    context_parts.append(_format_interactions_for_prompt(r["data"]))

            else:
                logger.warning("Unknown tool: %s", tool_name)

        except Exception as exc:
            logger.warning("Tool %s failed: %s", tool_name, exc)
            results[tool_name] = {"error": str(exc)}

    return {
        "tools_called": list(results.keys()),
        "results": results,
        "context_text": "\n\n".join(context_parts) if context_parts else "",
    }


# ===========================================================================
# Formatting helpers for LLM prompt injection
# ===========================================================================

def _format_history_for_prompt(data: dict) -> str:
    parts = ["--- PATIENT MEDICAL HISTORY ---"]
    for h in data.get("medical_history", [])[:8]:
        parts.append(f"- {h['title']} ({h.get('category', '')}): {h['details'][:200]}")
    if data.get("prescriptions"):
        parts.append("\nRecent Prescriptions:")
        for rx in data["prescriptions"][:5]:
            parts.append(f"- {rx['medicine']} {rx.get('dosage', '')}: {rx.get('instructions', '')[:100]}")
    if data.get("admissions"):
        parts.append("\nAdmissions:")
        for adm in data["admissions"][:3]:
            parts.append(f"- {adm['status']}: {adm.get('notes', '')[:100]}")
    return "\n".join(parts)


def _format_report_for_prompt(data: dict) -> str:
    parts = ["--- LATEST LAB REPORT ---"]
    if data.get("scan_file"):
        parts.append(f"File: {data['scan_file']}")
    if data.get("scan_date"):
        parts.append(f"Date: {data['scan_date']}")
    if data.get("scan_summary"):
        parts.append(f"Summary: {data['scan_summary']}")
    tests = data.get("lab_values", [])
    if tests:
        parts.append("\nTest Results:")
        for t in tests[:15]:
            flag = f" ({t['flag'].upper()})" if t.get("flag") and t["flag"] != "normal" else ""
            parts.append(f"- {t['test_name']}: {t['value']} {t.get('unit', '')} [ref: {t.get('reference_range', '')}]{flag}")
    return "\n".join(parts)


def _format_reports_for_prompt(data: dict) -> str:
    parts = ["--- PREVIOUS LAB REPORTS ---"]
    for r in data.get("reports", [])[:5]:
        parts.append(f"\nReport ({r.get('date', 'unknown date')}): {r.get('file_name', '')}")
        for t in r.get("tests", [])[:10]:
            flag = f" ({t['flag'].upper()})" if t.get("flag") and t["flag"] != "normal" else ""
            parts.append(f"  - {t['test_name']}: {t['value']} {t.get('unit', '')}{flag}")
    return "\n".join(parts)


def _format_mediscan_for_prompt(data: dict) -> str:
    parts = ["--- MEDISCAN / IMAGING RESULTS ---"]
    for s in data.get("scans", [])[:5]:
        parts.append(f"\nScan: {s.get('file_name', 'Unknown')} ({s.get('date', '')})")
        if s.get("summary"):
            parts.append(f"  Summary: {s['summary'][:500]}")
        if s.get("findings"):
            parts.append(f"  Findings: {s['findings'][:500]}")
        if s.get("impression"):
            parts.append(f"  Impression: {s['impression'][:300]}")
    return "\n".join(parts)


def _format_symptom_checker_for_prompt(data: dict) -> str:
    parts = ["--- SYMPTOM CHECKER RESULTS ---"]
    for sc in data.get("symptom_checks", [])[:3]:
        parts.append(f"\nAssessment ({sc.get('date', '')}):")
        if sc.get("summary"):
            parts.append(f"  {sc['summary'][:500]}")
        if sc.get("data"):
            parts.append(f"  Details: {json.dumps(sc['data'], default=str)[:500]}")
    return "\n".join(parts)


def _format_medicines_for_prompt(data: dict) -> str:
    parts = ["--- CURRENT MEDICATIONS ---"]
    for m in data.get("current_medicines", [])[:10]:
        name = m.get("name") or m.get("medicine_name", "")
        dosage = m.get("dosage", "")
        details = m.get("details") or m.get("instructions", "")
        parts.append(f"- {name} {dosage}: {details[:200]}")
    return "\n".join(parts)


def _format_patient_context_for_prompt(data: dict) -> str:
    parts = ["--- PATIENT INFORMATION ---"]
    if data.get("age"):
        parts.append(f"Age: {data['age']}")
    if data.get("gender"):
        parts.append(f"Gender: {data['gender']}")
    if data.get("blood_group"):
        parts.append(f"Blood Group: {data['blood_group']}")
    if data.get("allergies"):
        parts.append(f"Allergies: {', '.join(data['allergies'])}")
    if data.get("chronic_conditions"):
        parts.append(f"Chronic Conditions: {data['chronic_conditions']}")
    return "\n".join(parts)


def _format_medicine_search_for_prompt(data: dict) -> str:
    parts = [f"--- MEDICINE INFORMATION: {data.get('query', '')} ---"]
    for source in data.get("sources", []):
        source_name = source.get("source", "")
        results = source.get("results", [])[:2]
        for r in results:
            parts.append(f"\n[{source_name}] {r.get('name', r.get('brand_name', ''))}")
            for key in ("indications_and_usage", "warnings", "contraindications", "dosage_and_administration"):
                val = r.get(key)
                if val:
                    parts.append(f"  {key}: {str(val)[:300]}")
            if r.get("interactions"):
                parts.append(f"  Interactions: {json.dumps(r['interactions'][:3], default=str)[:300]}")
    combined = data.get("combined", {})
    if combined.get("interactions"):
        parts.append("\nKnown Interactions:")
        for i in combined["interactions"][:5]:
            parts.append(f"  - {i.get('drug', '')}: {i.get('severity', '')} — {i.get('description', '')[:200]}")
    return "\n".join(parts)


def _format_interactions_for_prompt(data: dict) -> str:
    parts = [f"--- INTERACTION CHECK: {data.get('medicine', '')} ---"]
    if data.get("interactions_found"):
        parts.append("Interactions FOUND:")
        for i in data.get("interactions", []):
            parts.append(f"  - [{i.get('source', '')}] {i.get('detail', '')[:300]}")
    else:
        parts.append(f"No interactions found in available sources.")
        if data.get("detail"):
            parts.append(f"  {data['detail'][:300]}")
    return "\n".join(parts)


def _extract_test_name(query: str) -> str:
    """Try to extract a specific test name from the query."""
    test_names = ["hemoglobin", "hemoglobin", "glucose", "cholesterol", "creatinine",
                   "thyroid", "tsh", "cbc", "lipid", "liver", "kidney", "blood",
                   "urine", "sugar", "hba1c", "vitamin", "iron", "calcium"]
    query_lower = query.lower()
    for name in test_names:
        if name in query_lower:
            return name
    return ""
