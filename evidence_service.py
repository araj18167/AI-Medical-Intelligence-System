"""
Evidence Service (2026-09-02)
=============================
Aggregates all available patient evidence + medical knowledge for the AI Doctor.

Evidence Sources (Priority Order):
1. Current clinician instructions/records
2. Recent verified patient reports/results
3. Current medicines and allergies
4. Relevant MediScan findings
5. Relevant Symptom Checker findings
6. Authoritative medical sources (MedlinePlus, RxNorm, DailyMed, openFDA)
7. General medical knowledge

Each source is weighted by authority and relevance.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import models
from sqlalchemy.orm import Session

logger = logging.getLogger("evidence_service")


# ===========================================================================
# Patient Evidence Builder
# ===========================================================================

def build_patient_evidence(db: Session, patient_id: int) -> dict:
    """Build comprehensive patient evidence from all available sources.
    
    Returns a structured dict with all patient-related evidence:
    - demographics
    - medical_history
    - current_medications
    - prescriptions
    - lab_values
    - scan_results
    - treatments
    - nursing_notes
    - vital_records
    - allergies
    - admissions
    """
    evidence = {
        "patient_id": patient_id,
        "demographics": {},
        "medical_history": [],
        "allergies": [],
        "current_medications": [],
        "prescriptions": [],
        "lab_values": [],
        "scan_results": [],
        "treatments": [],
        "nursing_notes": [],
        "vital_records": [],
        "admissions": [],
        "symptom_checker_results": [],
    }

    # --- Demographics ---
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if patient:
        evidence["demographics"] = {
            "name": patient.name or "",
            "age": patient.age,
            "gender": patient.gender or "",
            "contact": patient.contact or "",
        }

    # --- Patient Profile ---
    profile = db.query(models.PatientProfile).filter(
        models.PatientProfile.patient_id == patient_id
    ).first()
    if profile:
        for attr in ("blood_group", "occupation", "smoking", "alcohol",
                      "diet", "exercise", "allergies", "chronic_conditions"):
            val = getattr(profile, attr, None)
            if val:
                if attr == "allergies":
                    evidence["allergies"] = [a.strip() for a in str(val).split(",") if a.strip()]
                elif attr == "chronic_conditions":
                    evidence["demographics"]["chronic_conditions"] = val
                else:
                    evidence["demographics"][attr] = val

    # --- Medical History (last 20 entries) ---
    histories = (
        db.query(models.MedicalHistoryEntry)
        .filter(models.MedicalHistoryEntry.patient_id == patient_id)
        .order_by(models.MedicalHistoryEntry.created_at.desc())
        .limit(20)
        .all()
    )
    for h in histories:
        entry = {
            "title": h.title or "",
            "details": h.details or "",
            "category": h.category or "",
            "is_ongoing": getattr(h, "is_ongoing", False),
            "date": h.created_at.isoformat() if h.created_at else None,
        }
        evidence["medical_history"].append(entry)
        # Extract allergies from history
        if h.category == "allergy" and h.title:
            evidence["allergies"].append(h.title)

    # --- Current Medications (from MedicalHistoryEntry with category=medication) ---
    med_entries = [h for h in histories if h.category == "medication"]
    for m in med_entries:
        evidence["current_medications"].append({
            "name": m.title or "",
            "details": m.details or "",
            "is_ongoing": getattr(m, "is_ongoing", False),
            "date": m.created_at.isoformat() if m.created_at else None,
        })

    # --- Prescriptions (last 15) ---
    prescriptions = (
        db.query(models.Prescription)
        .filter(models.Prescription.patient_id == patient_id)
        .order_by(models.Prescription.created_at.desc())
        .limit(15)
        .all()
    )
    for rx in prescriptions:
        evidence["prescriptions"].append({
            "medicine": rx.medicine_name or "",
            "dosage": rx.dosage or "",
            "frequency": getattr(rx, "frequency", None) or "",
            "duration": getattr(rx, "duration", None) or "",
            "instructions": rx.instructions or "",
            "date": rx.created_at.isoformat() if rx.created_at else None,
        })

    # --- Lab Values (from DocumentScan lab_reports + LabValue table) ---
    lab_scans = (
        db.query(models.DocumentScan)
        .filter(
            models.DocumentScan.patient_id == patient_id,
            models.DocumentScan.doc_type == "lab_report",
        )
        .order_by(models.DocumentScan.created_at.desc())
        .limit(10)
        .all()
    )
    for scan in lab_scans:
        scan_data = {
            "scan_id": scan.id,
            "file_name": scan.file_name or "",
            "date": scan.created_at.isoformat() if scan.created_at else None,
            "summary": scan.summary or "",
            "tests": [],
        }
        # Get individual lab values
        lab_values = (
            db.query(models.LabValue)
            .filter(models.LabValue.scan_id == scan.id)
            .all()
        )
        for lv in lab_values:
            scan_data["tests"].append({
                "test_name": lv.test_name or "",
                "value": lv.value_numeric if lv.value_numeric is not None else (lv.value_text or ""),
                "unit": lv.unit or "",
                "reference_range": lv.reference_range or "",
                "flag": lv.flag or "unknown",
            })
        evidence["lab_values"].append(scan_data)

    # Also include lab results from the hospital lab system
    lab_results = (
        db.query(models.LabResult)
        .join(models.LabOrder, models.LabOrder.id == models.LabResult.order_id)
        .filter(models.LabOrder.patient_id == patient_id)
        .order_by(models.LabResult.created_at.desc())
        .limit(10)
        .all()
    )
    for lr in lab_results:
        evidence["lab_values"].append({
            "test_name": lr.test_name or "",
            "value": lr.result_value or "",
            "unit": lr.unit or "",
            "reference_range": lr.reference_range or "",
            "flag": lr.flag or "unknown",
            "source": "hospital_lab",
            "date": lr.created_at.isoformat() if lr.created_at else None,
        })

    # --- Scan Results (imaging) ---
    imaging_scans = (
        db.query(models.DocumentScan)
        .filter(
            models.DocumentScan.patient_id == patient_id,
            models.DocumentScan.doc_type == "imaging",
        )
        .order_by(models.DocumentScan.created_at.desc())
        .limit(10)
        .all()
    )
    for scan in imaging_scans:
        evidence["scan_results"].append({
            "scan_id": scan.id,
            "file_name": scan.file_name or "",
            "date": scan.created_at.isoformat() if scan.created_at else None,
            "summary": scan.summary or "",
            "extracted_json": _safe_json_parse(scan.extracted_json),
        })

    # Also include radiology reports from hospital system
    radiology = (
        db.query(models.RadiologyReport)
        .join(models.RadiologyOrder, models.RadiologyOrder.id == models.RadiologyReport.order_id)
        .filter(models.RadiologyOrder.patient_id == patient_id)
        .order_by(models.RadiologyReport.reported_at.desc().nullslast())
        .limit(5)
        .all()
    )
    for rr in radiology:
        evidence["scan_results"].append({
            "findings": rr.findings or "",
            "impression": getattr(rr, "impression", "") or "",
            "source": "hospital_radiology",
            "date": rr.reported_at.isoformat() if rr.reported_at else None,
        })

    # --- Treatments ---
    treatments = (
        db.query(models.Treatment)
        .filter(models.Treatment.patient_id == patient_id)
        .order_by(models.Treatment.created_at.desc())
        .limit(10)
        .all()
    )
    for t in treatments:
        evidence["treatments"].append({
            "description": t.description or "",
            "status": t.status or "",
            "notes": getattr(t, "notes", "") or "",
            "date": t.created_at.isoformat() if t.created_at else None,
        })

    # --- Nursing Notes ---
    nurse_notes = (
        db.query(models.NurseNote)
        .filter(models.NurseNote.patient_id == patient_id)
        .order_by(models.NurseNote.created_at.desc())
        .limit(10)
        .all()
    )
    for note in nurse_notes:
        evidence["nursing_notes"].append({
            "content": note.note or "",
            "category": getattr(note, "category", "") or "",
            "date": note.created_at.isoformat() if note.created_at else None,
        })

    # --- Vital Records ---
    vitals = (
        db.query(models.VitalRecord)
        .filter(models.VitalRecord.patient_id == patient_id)
        .order_by(models.VitalRecord.recorded_at.desc())
        .limit(10)
        .all()
    )
    for v in vitals:
        evidence["vital_records"].append({
            "blood_pressure_systolic": v.blood_pressure_systolic,
            "blood_pressure_diastolic": v.blood_pressure_diastolic,
            "blood_pressure": f"{v.blood_pressure_systolic}/{v.blood_pressure_diastolic}" if v.blood_pressure_systolic and v.blood_pressure_diastolic else "",
            "blood_sugar": v.blood_sugar,
            "weight_kg": v.weight_kg,
            "date": v.recorded_at.isoformat() if v.recorded_at else None,
        })

    # --- Admissions ---
    admissions = (
        db.query(models.Admission)
        .filter(models.Admission.patient_id == patient_id)
        .order_by(models.Admission.admitted_at.desc())
        .limit(5)
        .all()
    )
    for adm in admissions:
        evidence["admissions"].append({
            "status": "active" if adm.discharged_at is None else "discharged",
            "diagnosis": adm.notes or "",
            "date": adm.admitted_at.isoformat() if adm.admitted_at else None,
        })

    return evidence


# ===========================================================================
# Format Evidence for LLM Prompt
# ===========================================================================

def format_patient_evidence_for_prompt(evidence: dict, max_chars: int = 4000) -> str:
    """Format patient evidence into a clean text block for the LLM.
    
    Prioritizes the most relevant information and respects char limits.
    """
    parts = []
    char_count = 0

    def add(text: str):
        nonlocal char_count
        if char_count + len(text) > max_chars:
            return
        parts.append(text)
        char_count += len(text)

    # Demographics
    demo = evidence.get("demographics", {})
    if demo:
        demo_text = "Patient: "
        if demo.get("name"):
            demo_text += demo["name"]
        if demo.get("age"):
            demo_text += f", Age {demo['age']}"
        if demo.get("gender"):
            demo_text += f", {demo['gender']}"
        if demo.get("blood_group"):
            demo_text += f", Blood: {demo['blood_group']}"
        if demo.get("chronic_conditions"):
            demo_text += f", Chronic: {demo['chronic_conditions']}"
        add(demo_text)

    # Allergies
    allergies = evidence.get("allergies", [])
    if allergies:
        add(f"\nAllergies: {'; '.join(allergies[:10])}")

    # Current medications
    meds = evidence.get("current_medications", [])
    if meds:
        med_list = "; ".join(m.get("name", "") for m in meds[:10] if m.get("name"))
        if med_list:
            add(f"\nCurrent Medications: {med_list}")

    # Recent prescriptions
    prescriptions = evidence.get("prescriptions", [])
    if prescriptions:
        add("\nRecent Prescriptions:")
        for rx in prescriptions[:5]:
            med_info = rx.get("medicine", "")
            if rx.get("dosage"):
                med_info += f" {rx['dosage']}"
            if rx.get("frequency"):
                med_info += f" {rx['frequency']}"
            add(f"  - {med_info}")

    # Lab values (only abnormal ones for conciseness)
    lab_values = evidence.get("lab_values", [])
    if lab_values:
        abnormal = []
        for lab_group in lab_values:
            tests = lab_group.get("tests", [])
            if not tests:
                # Direct lab value (from hospital_lab)
                if lab_group.get("flag") and lab_group["flag"] != "normal":
                    abnormal.append(lab_group)
            else:
                for t in tests:
                    if t.get("flag") and t["flag"] != "normal":
                        abnormal.append(t)
        if abnormal:
            add("\nAbnormal Lab Results:")
            for lv in abnormal[:8]:
                test_name = lv.get("test_name", "")
                value = lv.get("value", "")
                unit = lv.get("unit", "")
                ref = lv.get("reference_range", "")
                flag = lv.get("flag", "")
                add(f"  - {test_name}: {value} {unit} [{ref}] ({flag.upper()})")

    # Medical history (ongoing conditions)
    history = evidence.get("medical_history", [])
    ongoing = [h for h in history if h.get("is_ongoing")]
    if ongoing:
        add("\nOngoing Conditions:")
        for h in ongoing[:5]:
            add(f"  - {h.get('title', '')}: {h.get('details', '')[:100]}")

    # Treatments
    treatments = evidence.get("treatments", [])
    if treatments:
        add("\nRecent Treatments:")
        for t in treatments[:5]:
            add(f"  - {t.get('description', '')} ({t.get('status', '')})")

    # Vital records (latest only)
    vitals = evidence.get("vital_records", [])
    if vitals:
        latest = vitals[0]
        vital_parts = []
        if latest.get("temperature"):
            vital_parts.append(f"Temp: {latest['temperature']}°C")
        if latest.get("blood_pressure"):
            vital_parts.append(f"BP: {latest['blood_pressure']}")
        if latest.get("heart_rate"):
            vital_parts.append(f"HR: {latest['heart_rate']} bpm")
        if latest.get("spo2"):
            vital_parts.append(f"SpO2: {latest['spo2']}%")
        if vital_parts:
            add(f"\nLatest Vitals: {', '.join(vital_parts)}")

    # Scan summaries (last 3)
    scans = evidence.get("scan_results", [])
    if scans:
        add("\nRecent Imaging/Scans:")
        for s in scans[:3]:
            summary = s.get("summary", "")[:200]
            if summary:
                add(f"  - {s.get('file_name', 'Scan')}: {summary}")

    return "\n".join(parts) if parts else "No patient evidence available."


# ===========================================================================
# Evidence Ranking
# ===========================================================================

def rank_evidence_for_query(
    patient_evidence: dict,
    medical_knowledge: dict,
    query: str,
) -> dict:
    """Rank and select the most relevant evidence for the current query.
    
    Returns ranked evidence blocks ready for prompt injection.
    """
    query_lower = query.lower()

    # Extract keywords from query
    query_keywords = set(re.findall(r'\b[a-z]{3,}\b', query_lower))

    ranked = {
        "patient_context": "",
        "medical_evidence": "",
        "sources_used": [],
        "confidence_factors": [],
    }

    # Build patient context
    ranked["patient_context"] = format_patient_evidence_for_prompt(patient_evidence)

    # Format medical knowledge evidence
    medical_evidence_text = ""
    if medical_knowledge:
        import medical_knowledge_service as mks
        medical_evidence_text = mks.format_evidence_for_prompt(medical_knowledge)
        # Track which sources were used
        for key in ("medlineplus", "rxnorm", "dailymed", "openfda"):
            data = medical_knowledge.get(key)
            if data and data.get("available"):
                ranked["sources_used"].append({
                    "name": data["source"],
                    "url": data.get("source_url", ""),
                    "results_count": len(data.get("results", [])),
                })

    ranked["medical_evidence"] = medical_evidence_text

    # Confidence factors
    if patient_evidence.get("lab_values"):
        ranked["confidence_factors"].append("Lab data available")
    if patient_evidence.get("prescriptions"):
        ranked["confidence_factors"].append("Prescription history available")
    if patient_evidence.get("medical_history"):
        ranked["confidence_factors"].append("Medical history available")
    if medical_knowledge:
        for key in ("rxnorm", "dailymed", "openfda"):
            if medical_knowledge.get(key, {}).get("available"):
                ranked["confidence_factors"].append(f"{key.upper()} evidence available")
                break

    return ranked


# ===========================================================================
# Helpers
# ===========================================================================

def _safe_json_parse(text: str) -> Optional[dict]:
    """Safely parse a JSON string, returning None on failure."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None
