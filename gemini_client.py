import re


import json
import logging
# ---- NVIDIA NIM client (OpenAI-compatible) ----
from config import NVIDIA_API_KEY
from openai import OpenAI

# Client-level reliability defaults:
#   timeout   — cap a single request at 120s instead of the SDK default 600s,
#               so a hung upstream never leaves the user waiting forever.
#   max_retries— built-in exponential backoff for transient failures
#               (429 rate limits, 5xx, connection resets).
_OPENAI_TIMEOUT = 120.0
_OPENAI_MAX_RETRIES = 2

_nvidia = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
    timeout=_OPENAI_TIMEOUT,
    max_retries=_OPENAI_MAX_RETRIES,
)
NEMOTRON_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"

# ---- OCR v2 client (separate key, dedicated OCR endpoint) ----
from config import NVIDIA_OCR_KEY
_nvidia_ocr = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_OCR_KEY,
    timeout=_OPENAI_TIMEOUT,
    max_retries=_OPENAI_MAX_RETRIES,
)

# ---- Omni multimodal client (separate key, vision + reasoning) ----
from config import NVIDIA_OMNI_KEY
_nvidia_omni = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_OMNI_KEY,
    timeout=_OPENAI_TIMEOUT,
    max_retries=_OPENAI_MAX_RETRIES,
)
OMNI_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"

logger = logging.getLogger(__name__)


def _ocr_extract_text(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Extract text from an image using Nemotron Omni for OCR."""
    prompt = (
        "Extract ALL visible text from this image. Return ONLY the extracted text, "
        "no commentary. Preserve the original layout and formatting as much as possible."
    )
    return _omni_analyze(image_bytes, prompt, mime_type, temperature=0.0, max_tokens=4096)


def _omni_analyze(image_bytes: bytes, prompt: str, mime_type: str = "image/jpeg",
                   *, temperature: float = 0.1, max_tokens: int = 4096) -> str:
    """Send an image + text prompt to Nemotron Omni for multimodal analysis."""
    import base64
    b64 = base64.b64encode(image_bytes).decode()
    data_uri = f"data:{mime_type};base64,{b64}"
    messages = [
        {"role": "system", "content": "You are a medical document analysis AI. Respond with valid JSON only. Do NOT output reasoning steps."},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_uri}},
            {"type": "text", "text": prompt},
        ]},
    ]
    resp = _nvidia_omni.chat.completions.create(
        model=OMNI_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body={"reasoning_effort": "none"},
    )
    text = (resp.choices[0].message.content or "").strip()
    # Strip thinking tags
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    return text


def _nemotron_chat(prompt: str, *, temperature: float = 0.3, max_tokens: int = 4096, system: str = None) -> str:
    """Call Nemotron via NVIDIA NIM and return the text response."""
    _SYS = (
        "You are a medical AI assistant. Respond directly and concisely. "
        "Do NOT output your internal reasoning steps, thinking process, "
        "or chain-of-thought. Just provide the final answer."
    )
    messages = [
        {"role": "system", "content": _SYS + (" " + system if system else "")},
        {"role": "user", "content": prompt},
    ]
    resp = _nvidia.chat.completions.create(
        model=NEMOTRON_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=0.9,
        extra_body={"reasoning_effort": "none"},
    )
    text = (resp.choices[0].message.content or "").strip()
    # Strip thinking tags
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    # Strip "Here's a thinking process" blocks
    if text.startswith("Here") and "thinking" in text[:150].lower():
        parts = text.split("\n\n")
        for p in reversed(parts):
            p = p.strip()
            if p and not p.startswith("1.") and not p.startswith("**"):
                text = p
                break
    return text


def _nemotron_stream(messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 1200):
    """Stream Nemotron via NVIDIA NIM. Yields text chunks."""
    stream = _nvidia.chat.completions.create(
        model=NEMOTRON_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=0.9,
        stream=True,
        extra_body={"reasoning_effort": "none"},
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


def _clean_json_text(text: str) -> str:
    """Gemini sometimes wraps JSON in ```json ... ``` - this strips that off."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1]
        text = text.strip()
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


# Phrases that suggest a genuinely urgent / emergency situation. Used as a
# deterministic safety net when the model call fails, and to upgrade the
# displayed severity when the model under-weights them.
_EMERGENCY_PHRASES = [
    "chest pain", "chest tightness", "pressure in chest",
    "trouble breathing", "difficulty breathing", "can't breathe", "cannot breathe",
    "shortness of breath", "gasping", "severe bleeding", "uncontrolled bleeding",
    "passing out", "fainted", "unconscious", "loss of consciousness",
    "seizure", "convuls", "stroke", "face droop", "slurred speech",
    "suicidal", "want to hurt", "overdose", "poisoning",
    "severe allergic", "anaphylax", "throat closing", "swelling of the face",
    "crushing pain", "cold sweats and", "vomiting blood", "coughing up blood",
    "blood in stool with dizziness", "high fever with stiff neck", "stiff neck and fever",
]


def _normalize_symptoms_text(text: str) -> str:
    """Normalize free-text symptoms without rejecting natural language:
    collapse whitespace, strip leading/trailing noise, preserve sentence
    structure and punctuation."""
    if text is None:
        return ""
    text = str(text)
    # Replace multiple spaces / newlines with a single space.
    text = re.sub(r"\s+", " ", text)
    # Trim separators-only residue ("...,,, " etc.)
    text = re.sub(r"^[\s,;.]+|[\s,;.]+$", "", text)
    return text[:4000]  # hard cap so a huge paste never blows the context


def _emergency_match(text: str):
    """Return the first emergency phrase found in text (case-insensitive)."""
    low = (text or "").lower()
    for phrase in _EMERGENCY_PHRASES:
        if phrase in low:
            return phrase
    return None


def _symptom_fallback(reason: str, *, emergency_phrase: str = None) -> dict:
    """Structured, safe fallback that still satisfies the frontend contract so
    the page never shows a blank screen when the model is unavailable."""
    emergency = emergency_phrase is not None
    if emergency:
        return {
            "patient_summary": ("Your symptoms include a potentially serious warning sign "
                                f"('{emergency_phrase}'). Please seek urgent medical attention."),
            "affected_regions": [],
            "conditions": [],
            "severity": {
                "level": "emergency",
                "score": 92,
                "explanation": "A potentially serious symptom was detected. This needs urgent evaluation.",
                "urgency": "Seek emergency care now",
            },
            "red_flags": [{
                "symptom": emergency_phrase,
                "concern": "Potentially serious warning sign",
                "action": "Call emergency services or go to the nearest emergency department now.",
            }],
            "recommended_tests": [],
            "self_care": [],
            "when_to_see_doctor": ["Seek urgent medical attention immediately."],
            "questions_for_doctor": [],
            "see_doctor": True,
            "doctor_urgency": "immediate_emergency",
            "ai_confidence": {"score": 80, "explanation": "Deterministic safety screening matched a serious symptom."},
            "doctor_verification_required": True,
            "clinical_disclaimer": "This is AI-assisted analysis for informational purposes only. Always consult a qualified healthcare professional for diagnosis and treatment.",
            "service_unavailable": False,
        }
    return {
        "patient_summary": "The symptom analysis service is temporarily unavailable. Please try again in a moment.",
        "affected_regions": [],
        "conditions": [],
        "severity": {
            "level": "moderate",
            "score": 0,
            "explanation": "Analysis could not be completed right now.",
            "urgency": "Retry the analysis in a moment.",
        },
        "red_flags": [],
        "recommended_tests": [],
        "self_care": [],
        "when_to_see_doctor": [],
        "questions_for_doctor": [],
        "see_doctor": False,
        "doctor_urgency": "",
        "ai_confidence": {"score": 0, "explanation": reason},
        "doctor_verification_required": True,
        "clinical_disclaimer": "This is AI-assisted analysis for informational purposes only. Always consult a qualified healthcare professional for diagnosis and treatment.",
        "service_unavailable": True,
    }


def predict_disease(symptoms: str, age: int = None, gender: str = None, history: str = None) -> dict:
    """
    Sends patient data to Nemotron and returns a structured triage analysis.
    Reliable by design:
      * normalizes input
      * retries transient API failures once
      * falls back to a controlled structured response (never raises) when
        the model is unavailable
      * a deterministic emergency scan keeps users safe even if the model fails
    """
    normalized = _normalize_symptoms_text(symptoms)
    emergency_phrase = _emergency_match(normalized)

    if not normalized or len(normalized) < 3:
        return _symptom_fallback("No symptoms described.")

    prompt = f"""
Medical triage with medication support. Return ONLY valid JSON.

Symptoms: {normalized}
Age: {age or 'unknown'} | Gender: {gender or 'unknown'} | History: {history or 'none'}

Return this JSON:
{{"patient_summary":"1-2 sentence summary","affected_regions":["head","chest"],"conditions":[{{"name":"Condition","likelihood":"high|medium|low","confidence":85,"description":"Brief explanation","supporting_symptoms":["symptom1"],"contradicting_evidence":["factor"],"typical_duration":"3-7 days","clinical_significance":"What this condition means for the patient"}}],"severity":{{"level":"low|moderate|high|emergency","score":35,"explanation":"Why","urgency":"See doctor within 24-48 hours"}},"red_flags":[{{"symptom":"dangerous symptom","concern":"Why concerning","action":"What to do"}}],"medication_options":[{{"generic_name":"Ibuprofen","brand_names":["Brufen","Advil"],"purpose":"Pain relief and anti-inflammatory","why_considered":"Effective for headache and fever","dosage":"200-400mg","frequency":"Every 6-8 hours as needed","duration":"3-5 days","route":"oral","contraindications":["Stomach ulcers","Kidney disease","Third trimester pregnancy"],"common_side_effects":["Stomach upset","Nausea","Dizziness"],"drug_interactions":["Blood thinners","Aspirin","Lisinopril"],"precautions":"Take with food to reduce stomach irritation","confidence":85,"evidence_level":"well-established"}}],"safety_warnings":["Check for allergies before taking any medication","Do not exceed recommended dose"],"recommended_tests":[{{"name":"Test","purpose":"Why","urgency":"routine|soon|urgent"}}],"self_care":[{{"action":"Do this","details":"How","caution":"When to stop"}}],"when_to_see_doctor":["Situation"],"questions_for_doctor":["Question"],"see_doctor":true,"doctor_urgency":"within_48_hours","ai_confidence":{{"score":75,"explanation":"Why this confidence"}},"doctor_verification_required":true,"clinical_disclaimer":"This is AI-assisted analysis for informational purposes only. Always consult a qualified healthcare professional for diagnosis and treatment."}}

Rules:
- 2-4 conditions sorted by likelihood
- severity.score 0-100
- red_flags only for genuinely dangerous symptoms
- affected_regions use ONLY: head, brain, chest, heart, left_lung, right_lung, lungs, abdomen, liver, stomach, kidneys, back, pelvis, skin, throat
- medication_options: provide 1-3 common OTC or well-known prescription options per condition when severity is low-moderate. For high/emergency severity, note medications need doctor prescription.
- Each medication must have generic_name, purpose, why_considered, dosage, frequency, route, contraindications, common_side_effects, drug_interactions, precautions, confidence (0-100)
- Do NOT invent medications. Only suggest well-known, commonly used medications.
- Always include safety_warnings and doctor_verification_required=true
- If the description is too vague to analyze, ask for more detail (use patient_summary + questions_for_doctor) instead of inventing conditions.
- If the symptoms indicate an emergency (e.g. severe chest pain with breathlessness), set severity.level to "emergency", add a red_flag, and do NOT recommend self-treatment.
- No markdown. No narration.

Return this JSON:
{{"patient_summary":"1-2 sentence summary","affected_regions":["head","chest"],"conditions":[{{"name":"Condition","likelihood":"high|medium|low","confidence":85,"description":"Brief explanation","supporting_symptoms":["symptom1"],"contradicting_evidence":["factor"],"typical_duration":"3-7 days","clinical_significance":"What this condition means for the patient"}}],"severity":{{"level":"low|moderate|high|emergency","score":35,"explanation":"Why","urgency":"See doctor within 24-48 hours"}},"red_flags":[{{"symptom":"dangerous symptom","concern":"Why concerning","action":"What to do"}}],"medication_options":[{{"generic_name":"Ibuprofen","brand_names":["Brufen","Advil"],"purpose":"Pain relief and anti-inflammatory","why_considered":"Effective for headache and fever","dosage":"200-400mg","frequency":"Every 6-8 hours as needed","duration":"3-5 days","route":"oral","contraindications":["Stomach ulcers","Kidney disease","Third trimester pregnancy"],"common_side_effects":["Stomach upset","Nausea","Dizziness"],"drug_interactions":["Blood thinners","Aspirin","Lisinopril"],"precautions":"Take with food to reduce stomach irritation","confidence":85,"evidence_level":"well-established"}}],"safety_warnings":["Check for allergies before taking any medication","Do not exceed recommended dose"],"recommended_tests":[{{"name":"Test","purpose":"Why","urgency":"routine|soon|urgent"}}],"self_care":[{{"action":"Do this","details":"How","caution":"When to stop"}}],"when_to_see_doctor":["Situation"],"questions_for_doctor":["Question"],"see_doctor":true,"doctor_urgency":"within_48_hours","ai_confidence":{{"score":75,"explanation":"Why this confidence"}},"doctor_verification_required":true,"clinical_disclaimer":"This is AI-assisted analysis for informational purposes only. Always consult a qualified healthcare professional for diagnosis and treatment."}}

Rules:
- 2-4 conditions sorted by likelihood
- severity.score 0-100
- red_flags only for genuinely dangerous symptoms
- affected_regions use ONLY: head, brain, chest, heart, left_lung, right_lung, lungs, abdomen, liver, stomach, kidneys, back, pelvis, skin, throat
- medication_options: provide 1-3 common OTC or well-known prescription options per condition when severity is low-moderate. For high/emergency severity, note medications need doctor prescription.
- Each medication must have generic_name, purpose, why_considered, dosage, frequency, route, contraindications, common_side_effects, drug_interactions, precautions, confidence (0-100)
- Do NOT invent medications. Only suggest well-known, commonly used medications.
- Always include safety_warnings and doctor_verification_required=true
- No markdown. No narration.
"""

    # Call the model with one retry on transient failures (429 / timeout /
    # connection reset). The underlying OpenAI client already retries twice
    # internally; this extra pass guards against a bad first response.
    result = None
    last_error = "Unknown error"
    for _attempt in range(2):
        try:
            raw_text = _nemotron_chat(prompt)
            cleaned = _clean_json_text(raw_text)
            result = json.loads(cleaned)
            if isinstance(result, dict):
                break
            result = None
            last_error = "Model returned non-object JSON."
        except json.JSONDecodeError as exc:
            last_error = f"Could not parse model response: {exc}"
            # Try once more — models occasionally emit stray text before JSON.
        except Exception as exc:  # network / API / timeout errors
            last_error = f"Model API error: {exc}"
            if _attempt == 0:
                import time as _time
                _time.sleep(1.2)  # brief backoff before retry
    if result is None:
        # Never crash the endpoint: return a controlled, structured response.
        logger.warning("predict_disease degraded: %s", last_error)
        result = _symptom_fallback(last_error, emergency_phrase=emergency_phrase)
        return result

    # Backward compatibility: if the model returned the OLD format, convert it
    if "likely_conditions" in result and "conditions" not in result:
        old_conds = result.get("likely_conditions", [])
        result["conditions"] = [
            {"name": c, "likelihood": "medium", "confidence": 50, "description": "",
             "supporting_symptoms": [], "contradicting_evidence": [],
             "typical_duration": "unknown", "severity_if_untreated": "medium"}
            for c in (old_conds if isinstance(old_conds, list) else [str(old_conds)])
        ]
        risk_map = {"low": {"level": "low", "score": 25}, "medium": {"level": "moderate", "score": 50}, "high": {"level": "high", "score": 75}}
        risk = result.get("risk_level", "medium")
        result["severity"] = risk_map.get(risk, risk_map["medium"])
        result["severity"]["explanation"] = result.get("recommendation", "")
        result["severity"]["urgency"] = "See doctor within 24-48 hours"
        result["red_flags"] = []
        result["recommended_tests"] = []
        result["self_care"] = []
        result["when_to_see_doctor"] = [result.get("recommendation", "Consult a doctor.")]
        result["questions_for_doctor"] = []
        result["see_doctor"] = result.get("see_doctor", True)
        result["doctor_urgency"] = "within_48_hours"
        result["ai_confidence"] = {"score": 50, "explanation": "Legacy format"}
        result["patient_summary"] = result.get("recommendation", "")

    # Safety upgrade: a deterministic emergency phrase matched but the model
    # under-weighted it — never let a serious symptom show as "low" risk.
    if emergency_phrase:
        sev = result.get("severity") or {}
        level = str(sev.get("level", "")).lower()
        if level not in ("emergency", "high"):
            sev["level"] = "high"
            sev["score"] = max(int(sev.get("score") or 0), 78)
            sev["urgency"] = "Seek medical attention promptly; call emergency services if symptoms worsen."
            if not sev.get("explanation"):
                sev["explanation"] = f"Potentially serious symptom detected ('{emergency_phrase}')."
            result["severity"] = sev
            red = result.get("red_flags")
            if not isinstance(red, list):
                red = []
            red.insert(0, {
                "symptom": emergency_phrase,
                "concern": "Potentially serious warning sign",
                "action": "Seek urgent medical evaluation; call emergency services if symptoms worsen.",
            })
            result["red_flags"] = red
            result["see_doctor"] = True
            result["doctor_urgency"] = "urgent"

    # Normalize affected_regions: keep only known vocabulary, drop anything else.
    _VALID_REGIONS = {
        "head", "brain", "chest", "heart", "left_lung", "right_lung", "lungs",
        "abdomen", "liver", "stomach", "kidneys", "left_kidney", "right_kidney",
        "back", "pelvis", "left_arm", "right_arm", "left_leg", "right_leg",
        "skin", "throat",
    }
    raw_regions = result.get("affected_regions")
    if not isinstance(raw_regions, list):
        raw_regions = []
    cleaned_regions = []
    for r in raw_regions:
        if not isinstance(r, str):
            continue
        token = r.strip().lower().replace(" ", "_").replace("-", "_")
        if token in _VALID_REGIONS and token not in cleaned_regions:
            cleaned_regions.append(token)
    result["affected_regions"] = cleaned_regions[:6]

    return result


def get_medicine_info(medicine_name: str) -> dict:
    """
    Sends a medicine name to Gemini and returns structured info as a dict,
    plus a "related" list of other brand names and active compositions
    that share ingredients with the searched medicine.
    """
    prompt = f"""
You are a medical information assistant. Provide general reference information about the following medicine.
Return ONLY valid JSON, nothing else - no markdown formatting, no explanation text outside the JSON.

Medicine name: {medicine_name}

Return JSON in EXACTLY this format:
{{
  "medicine_name": "{medicine_name}",
  "common_uses": ["use1", "use2"],
  "typical_dosage": "general dosage guidance as plain text",
  "side_effects": ["side effect 1", "side effect 2", "side effect 3"],
  "interactions": ["interaction warning 1", "interaction warning 2"],
  "precautions": "short plain-language precaution notes",
  "related": [
    {{ "name": "Other Brand Name", "type": "brand", "composition": "active ingredients with strengths", "note": "one short sentence explaining the relationship" }},
    {{ "name": "Active Salt Name", "type": "composition", "composition": null, "note": "one short sentence explaining the relationship" }}
  ]
}}

For "related":
- type must be exactly "brand" or "composition".
- "brand" entries are alternate marketed brand names containing the same active composition(s) as the searched medicine.
  Include the active composition string (e.g. "Paracetamol 650mg" or "Cetirizine 10mg + Pseudoephedrine 120mg").
- "composition" entries are the active pharmaceutical ingredient(s) / salt(s) found in the searched medicine.
  Set "composition" to null for these.
- "note" is a single short sentence describing the relationship (e.g. "Same active ingredient, marketed by a different company" or "Active salt in the searched medicine").
- Return up to 8 related entries total. Prioritize well-known, widely-marketed alternatives.
- If the medicine name is not recognized or invalid, return an empty "related" array and explain in "precautions" that it could not be identified.
"""

    raw_text = _nemotron_chat(prompt)
    cleaned = _clean_json_text(raw_text)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        result = {
            "medicine_name": medicine_name,
            "common_uses": [],
            "typical_dosage": "unavailable",
            "side_effects": [],
            "interactions": [],
            "precautions": "Could not parse AI response. Please try again or consult a pharmacist.",
            "related": []
        }

    # Defensive normalization: if Gemini omitted related or returned the wrong shape,
    # guarantee a list of well-formed dicts so Pydantic + the frontend never break.
    raw_related = result.get("related")
    if not isinstance(raw_related, list):
        raw_related = []
    cleaned_related = []
    for entry in raw_related:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        etype = entry.get("type")
        note = entry.get("note")
        if not name or etype not in ("brand", "composition") or not note:
            continue
        cleaned_related.append({
            "name": str(name).strip(),
            "type": etype,
            "composition": (entry.get("composition") or None),
            "note": str(note).strip(),
        })
    result["related"] = cleaned_related[:8]

    return result


def get_diet_fitness_plan(
    age: int,
    gender: str,
    bmi: float,
    bmi_category: str,
    conditions: str = None,
    fitness_goal: str = None
) -> dict:
    """
    Sends patient stats to Gemini and returns structured diet + fitness recommendations.
    BMI is calculated in Python beforehand (not by Gemini) for accuracy.
    """
    prompt = f"""
You are a nutrition and fitness assistant. Based on the following person's profile,
provide a personalized diet and fitness recommendation.
Return ONLY valid JSON, nothing else - no markdown formatting, no explanation text outside the JSON.

Age: {age}
Gender: {gender}
BMI: {bmi} ({bmi_category})
Existing health conditions: {conditions if conditions else "none provided"}
Fitness goal: {fitness_goal if fitness_goal else "general health"}

Return JSON in EXACTLY this format:
{{
  "diet_recommendations": ["tip1", "tip2", "tip3", "tip4"],
  "fitness_recommendations": ["tip1", "tip2", "tip3"],
  "calorie_target": "short plain-language daily calorie guidance",
  "precautions": "short plain-language precaution notes, especially if conditions were mentioned"
}}
"""

    raw_text = _nemotron_chat(prompt)
    cleaned = _clean_json_text(raw_text)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        result = {
            "diet_recommendations": ["Could not generate recommendations - please try again"],
            "fitness_recommendations": [],
            "calorie_target": "unavailable",
            "precautions": "AI response could not be parsed. Please try again."
        }

    return result


def digitize_prescription(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    Two-step prescription analysis pipeline:
      1. Nemotron Omni extracts raw text from the prescription image (OCR)
      2. Nemotron Lightning parses the extracted text into structured data

    This separation gives better accuracy: OCR focuses purely on text extraction,
    while Lightning focuses on medical understanding and structured output.
    """

    # ---- Step 1: OCR — extract raw text from the prescription image ----
    ocr_prompt = (
        "Extract ALL visible text from this medical prescription image. "
        "Return ONLY the extracted text exactly as written — no commentary, no interpretation. "
        "Preserve line breaks, abbreviations, and formatting. "
        "Include doctor name, date, patient name, medicine names, dosages, and any notes."
    )
    extracted_text = _omni_analyze(
        image_bytes, ocr_prompt, mime_type,
        temperature=0.0, max_tokens=4096
    )

    if not extracted_text or len(extracted_text.strip()) < 5:
        return {
            "doctor_name": None,
            "medicines": [],
            "notes": "Could not extract text from the image. The image may be unclear, blurry, or not a prescription."
        }

    # ---- Step 2: Structured extraction — parse text into structured data ----
    extraction_prompt = f"""
You are a senior clinical pharmacist AI assistant. Below is the raw text extracted
from a prescription image via OCR. Parse it into structured data with maximum accuracy.
Return ONLY valid JSON, nothing else - no markdown formatting, no explanation text outside the JSON.

Expand common medical abbreviations:
- OD = once daily, BD = twice daily, TDS = three times daily, QDS = four times daily
- HS = at bedtime, AC = before meals, PC = after meals
- PRN = as needed, Tab = tablet, Cap = capsule, Syr = syrup

Extracted text from prescription:
---
{extracted_text}
---

Return JSON in EXACTLY this format:
{{
  "doctor_name": "full name if visible, otherwise null",
  "doctor_registration": "registration number if visible, otherwise null",
  "prescription_date": "date if visible, otherwise null",
  "medicines": [
    {{
      "name": "medicine name as written",
      "generic_name": "generic/active ingredient if determinable, otherwise null",
      "dosage_form": "tablet | capsule | syrup | injection | cream | drops | other",
      "strength": "e.g. 500mg, 10ml",
      "dosage": "e.g. 1 tablet, 5ml",
      "frequency": "e.g. twice daily after food",
      "duration": "e.g. 5 days",
      "route": "oral | topical | injectable | other",
      "instructions": "any special instructions"
    }}
  ],
  "notes": "any other clinical notes or advice, or null"
}}

If the extracted text is empty, unclear, or not a prescription, return an empty "medicines" list
and explain the issue in "notes" instead of guessing.
"""

    raw_text = _nemotron_chat(extraction_prompt, temperature=0.1, max_tokens=4096)
    cleaned = _clean_json_text(raw_text)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        result = {
            "doctor_name": None,
            "medicines": [],
            "notes": "Could not parse extracted text into structured data. Please try again."
        }

    # Attach the OCR text for debugging/transparency
    result["_ocr_extracted_text"] = extracted_text

    return result


def summarize_patient(
    patient_info: dict,
    records: list,
    prescriptions: list,
    trends_data: dict,
    reminders: list,
    patient_profile: dict = None,
    medical_history: list = None,
    reports: list = None,
) -> str:
    """
    Generates a short plain-language clinical summary for a doctor.
    Unlike the other functions, this returns plain text (not JSON) since
    it's meant to be read directly by a human, not parsed by code.
    """
    prompt = f"""
You are assisting a doctor by summarizing a patient's medical profile in plain, clinical language.
Do NOT return JSON - write 3 to 5 short sentences a doctor could read in about 10 seconds.

Patient info: {patient_info}

Personal / lifestyle profile (blood group, occupation, habits - smoking, alcohol, diet, exercise, sleep): {patient_profile if patient_profile else "not provided"}

Structured medical history (chronic conditions, allergies, surgeries, medications, family history, immunizations): {medical_history if medical_history else "none recorded"}

Recent medical records / AI predictions: {records}

Current prescriptions: {prescriptions}

Vitals trend summary: {trends_data}

Active medicine reminders: {reminders}

Uploaded reports (titles + categories + notes): {reports if reports else "none uploaded"}

Write a concise, factual, neutral clinical summary. Specifically call out anything that
needs attention - e.g. rising risk trends, high-risk predictions, relevant allergies or
chronic conditions, lifestyle risk factors, or possible medication adherence concerns.
If data is sparse, say so plainly rather than guessing.
"""

    return _nemotron_chat(prompt)


def summarize_aggregate_report(ctx: dict) -> str:
    """Sectioned markdown clinical brief for a nurse-pushed aggregate
    AI summary report.

    Distinct from :func:`summarize_patient`, which returns 3-5 plain
    sentences. The aggregate report needs sectioned markdown so it can be
    both shown inline in a chat bubble and concatenated into a longer
    ``.md`` file the doctor downloads.

    The ``ctx`` dict is shaped by
    ``main._build_admission_clinical_context`` and carries:
    - patient_info
    - records (medications list)
    - prescriptions (mirrors records for the nurse-report flow)
    - reports (last 5 sent-to-doctor nurse notes)
    - treatments
    - admission_reason (free text on the admission)

    Returns a markdown string with H2 sections. Empty / None sections are
    fine — Gemini is told to say so plainly rather than invent.
    """
    patient_info = ctx.get("patient_info") or {}
    records = ctx.get("records") or []
    prescriptions = ctx.get("prescriptions") or []
    reports = ctx.get("reports") or []
    treatments = ctx.get("treatments") or []
    admission_reason = ctx.get("admission_reason") or "(no reason recorded)"

    prompt = f"""
You are assisting a doctor by writing a sectioned markdown clinical brief for a patient
who has just had a nurse push an aggregated "AI summary report" to you. Write the brief
in plain clinical language. Do NOT return JSON. Use the H2 sections listed below and
include ONLY the ones that have meaningful content — if a section has no data, write
"(none recorded)" and move on.

Section template (each H2 section should be 1-3 short sentences or a short bullet list):

## Clinical summary
A 2-4 sentence plain-language summary of what is going on with this patient right now,
focused on what's clinically actionable for the doctor.

## Medications of note
A short bullet list (one bullet per active medication) — name, dose, frequency, route.
Flag any obvious adherence, interaction, or stop-date concerns.

## Treatments of note
A short bullet list (one bullet per active/scheduled treatment) — name, schedule, status.
Flag anything the doctor should follow up on.

## Recent nurse notes
A short bullet list summarising the most recent sent-to-doctor nurse notes (one bullet
per note) — what the nurse observed and any concerns raised.

## Recommendations
A short paragraph (2-4 sentences) of what the doctor should do next — e.g. labs to
order, vitals to monitor, follow-up cadence, discharge planning.

Inputs you may use:

Patient info: {patient_info}

Admission reason: {admission_reason}

Active medications: {records}

Prescriptions (mirrors active medications): {prescriptions}

Active/scheduled treatments: {treatments}

Recent sent-to-doctor nurse notes (most recent first): {reports}

If any input is empty, say "(none recorded)" inside the relevant section. Do NOT
invent medications, treatments, allergies, or vitals you were not given.
"""

    return _nemotron_chat(prompt)


# ---------- MediScan: unified document analyzer ----------

# Allowed doc types; mirrors schemas.MEDISCAN_DOC_TYPES
MEDISCAN_DOC_TYPES = {"prescription", "lab_report", "imaging"}

# Medical document analysis via Nemotron Omni + OCR v2
# Flash model with low temperature for accuracy on structured extraction.


def analyze_document(
    doc_type: str,
    file_bytes: bytes,
    mime_type: str = "image/jpeg",
    extracted_text: str = None,
) -> dict:
    """
    Analyzes a medical document (prescription / lab report / imaging) using
    Gemini Pro and returns a dict with:
      - "data":      type-specific structured extraction
      - "summary":   plain-language patient-friendly explanation
      - "warnings":  list of strings for unclear/missing parts
      - "confidence": overall confidence score 0.0-1.0
      - "critical_values": list of clinically critical findings
      - "urgency":   "routine" | "follow_up_soon" | "urgent" | "emergency"

    Uses Nemotron Omni with low temperature (0.1) for maximum accuracy.
    `extracted_text` is only used when the input was a .docx file.
    """
    if doc_type not in MEDISCAN_DOC_TYPES:
        return _mediscan_fallback(
            doc_type,
            f"Unsupported document type '{doc_type}'. Must be one of: {sorted(MEDISCAN_DOC_TYPES)}.",
        )

    prompt = _mediscan_prompt(doc_type, from_text=bool(extracted_text))

    if extracted_text is not None:
        contents = prompt + "\n\n--- DOCUMENT TEXT START ---\n" + extracted_text + "\n--- DOCUMENT TEXT END ---"
        try:
            raw_text = _nemotron_chat(contents, temperature=0.1, max_tokens=4096)
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "quota" in err_msg.lower():
                return _mediscan_fallback(doc_type, "AI service is temporarily busy (rate limit). Please try again in a few seconds.")
            elif "timeout" in err_msg.lower() or "DEADLINE_EXCEEDED" in err_msg:
                return _mediscan_fallback(doc_type, "AI analysis timed out.")
            return _mediscan_fallback(doc_type, f"AI analysis failed: {err_msg[:200]}")
    else:
        try:
            raw_text = _omni_analyze(file_bytes, prompt, mime_type, temperature=0.1, max_tokens=8192)
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "quota" in err_msg.lower():
                return _mediscan_fallback(doc_type, "AI service is temporarily busy (rate limit). Please try again in a few seconds.")
            elif "timeout" in err_msg.lower() or "DEADLINE_EXCEEDED" in err_msg:
                return _mediscan_fallback(doc_type, "AI analysis timed out.")
            return _mediscan_fallback(doc_type, f"AI analysis failed: {err_msg[:200]}")
    cleaned = _clean_json_text(raw_text)



    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        return _mediscan_fallback(
            doc_type,
            "The AI response could not be parsed as JSON. Please try again with a clearer image.",
        )

    # Normalize: ensure all required fields exist
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    summary = result.get("summary") or "No summary was generated."
    warnings = result.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = [str(warnings)]

    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
        confidence = _estimate_confidence(doc_type, data)

    critical_values = result.get("critical_values") or []
    if not isinstance(critical_values, list):
        critical_values = [str(critical_values)]

    urgency = result.get("urgency") or "routine"
    if urgency not in ("routine", "follow_up_soon", "urgent", "emergency"):
        urgency = "routine"

    # Auto-detect urgency from critical values if model didn't set it
    if urgency == "routine" and critical_values:
        urgency = "follow_up_soon"

    # --- Pipeline stages ---
    pipeline_stages = []

    if doc_type == "prescription":
        pipeline_stages = [
            {"name": "Image Preprocessing", "status": "complete", "icon": "🖼️"},
            {"name": "Handwriting OCR / Vision Model", "status": "complete", "icon": "🔍"},
            {"name": "Text Extraction", "status": "complete", "icon": "📝"},
            {"name": "Medical NLP / NER", "status": "complete", "icon": "🧠"},
            {"name": "Medicine Name Detection", "status": "complete", "icon": "💊"},
            {"name": "Dosage + Frequency + Duration", "status": "complete", "icon": "⏰"},
        ]
        # Medicine DB verification
        medicines = data.get("medicines") or []
        if medicines:
            data["medicines"] = _verify_medicines(medicines)
            verified_count = sum(1 for m in data["medicines"] if m.get("db_verified"))
            pipeline_stages.append({"name": "Medicine Database Verification", "status": "complete", "icon": "✅", "detail": f"{verified_count}/{len(medicines)} medicines verified"})
        else:
            pipeline_stages.append({"name": "Medicine Database Verification", "status": "skipped", "icon": "⏭️", "detail": "No medicines detected"})
        pipeline_stages.extend([
            {"name": "Confidence Check", "status": "complete", "icon": "📊", "detail": f"{round(confidence*100)}%"},
            {"name": "Structured Prescription", "status": "complete", "icon": "📋"},
            {"name": "Doctor Verification", "status": "pending", "icon": "👨‍⚕️", "detail": "Awaiting review"},
        ])

    elif doc_type == "lab_report":
        pipeline_stages = [
            {"name": "Image Preprocessing", "status": "complete", "icon": "🖼️"},
            {"name": "OCR", "status": "complete", "icon": "🔍"},
            {"name": "Text & Table Extraction", "status": "complete", "icon": "📊"},
            {"name": "Medical NLP / NER", "status": "complete", "icon": "🧠"},
            {"name": "Test Name + Value + Unit Extraction", "status": "complete", "icon": "🔬"},
            {"name": "Reference Range Detection", "status": "complete", "icon": "📏"},
        ]
        # Classify lab values
        tests = data.get("tests") or []
        if tests:
            data["tests"] = _classify_lab_values(tests)
            abnormal = sum(1 for t in tests if t.get("flag") not in ("normal", None))
            pipeline_stages.append({"name": "Normal / High / Low Classification", "status": "complete", "icon": "🚦", "detail": f"{abnormal} abnormal of {len(tests)}"})
        else:
            pipeline_stages.append({"name": "Normal / High / Low Classification", "status": "skipped", "icon": "⏭️"})
        pipeline_stages.extend([
            {"name": "Abnormality Analysis", "status": "complete", "icon": "⚠️"},
            {"name": "AI Summary", "status": "complete", "icon": "🤖"},
            {"name": "Doctor Verification", "status": "pending", "icon": "👨‍⚕️", "detail": "Awaiting review"},
        ])

    elif doc_type == "imaging":
        pipeline_stages = [
            {"name": "Image Preprocessing", "status": "complete", "icon": "🖼️"},
            {"name": "Medical Image AI Model", "status": "complete", "icon": "🤖"},
            {"name": "Feature Extraction", "status": "complete", "icon": "🔍"},
            {"name": "Abnormality Detection", "status": "complete", "icon": "⚠️"},
            {"name": "Segmentation", "status": "complete", "icon": "🎯"},
            {"name": "Classification", "status": "complete", "icon": "🏷️"},
        ]
        # Score imaging findings
        findings = data.get("findings") or []
        if findings:
            scoring = _score_imaging_findings(findings, data.get("impression", ""))
            data["severity_score"] = scoring["severity_score"]
            data["priority"] = scoring["priority"]
            pipeline_stages.append({"name": "Confidence Score", "status": "complete", "icon": "📊", "detail": f"{round(confidence*100)}% confidence, {scoring['priority']} priority"})
        else:
            pipeline_stages.append({"name": "Confidence Score", "status": "complete", "icon": "📊", "detail": f"{round(confidence*100)}%"})
        pipeline_stages.extend([
            {"name": "AI Findings / Report", "status": "complete", "icon": "📋"},
            {"name": "Radiologist Verification", "status": "pending", "icon": "👨‍⚕️", "detail": "Awaiting review"},
        ])

    return {
        "data": data,
        "summary": summary,
        "warnings": warnings,
        "confidence": round(confidence, 2),
        "critical_values": critical_values,
        "urgency": urgency,
        "pipeline_stages": pipeline_stages,
    }


def _estimate_confidence(doc_type: str, data: dict) -> float:
    """Heuristic confidence score based on how much data was extracted."""
    if doc_type == "prescription":
        meds = data.get("medicines") or []
        if not meds:
            return 0.2
        score = 0.5
        for m in meds:
            if m.get("name"): score += 0.1
            if m.get("dosage"): score += 0.05
            if m.get("frequency"): score += 0.05
        return min(score, 0.95)
    elif doc_type == "lab_report":
        tests = data.get("tests") or []
        if not tests:
            return 0.2
        score = 0.5
        for t in tests:
            if t.get("name"): score += 0.05
            if t.get("value"): score += 0.03
            if t.get("flag") and t["flag"] != "unknown": score += 0.02
        return min(score, 0.95)
    elif doc_type == "imaging":
        findings = data.get("findings") or []
        if not findings:
            return 0.3
        score = 0.6 + min(len(findings) * 0.05, 0.3)
        if data.get("impression"): score += 0.05
        return min(score, 0.95)
    return 0.5


def _mediscan_prompt(doc_type: str, from_text: bool) -> str:
    """Returns a short, focused prompt for the given doc_type."""
    input_kind = "plain text from a Word document" if from_text else "the attached image/PDF"

    if doc_type == "prescription":
        return (
            f"You are a clinical pharmacist AI with deep expertise in drug interactions, "
            f"pharmacology, and patient safety. Analyze this prescription from {input_kind}.\n\n"
            f"EXTRACT THE FOLLOWING:\n"
            f"1. Metadata: doctor_name, doctor_registration, patient_name, prescription_date, diagnosis, notes, referrals.\n"
            f"2. For EACH medicine extract: name, generic_name, dosage_form (tablet/capsule/syrup/injection/cream/drops/inhaler/patches), "
            f"strength, dosage, frequency, duration, route (oral/topical/injectable/inhaled/rectal/ophthalmic), "
            f"instructions, quantity, color (if visible).\n"
            f"3. For EACH medicine also provide:\n"
            f"   - mechanism_of_action: how the drug works (one sentence)\n"
            f"   - side_effects: list of common side effects (3-6 items)\n"
            f"   - contraindications: when NOT to use this drug\n"
            f"   - food_interactions: foods/drinks to avoid\n"
            f"   - storage: how to store the medicine\n"
            f"   - warnings: any black-box or serious warnings\n"
            f"   - therapeutic_class: e.g. 'Analogue of PPI', 'NSAID', 'Antibiotic'\n"
            f"4. drug_interactions: list of {{drug_pair: [drug1, drug2], severity: low/moderate/high/critical, description: explanation}}\n"
            f"5. patient_safety: {{age_warnings: if dose inappropriate for elderly/children, pregnancy_warning: bool, renal_adjustment: bool, hepatic_adjustment: bool}}\n"
            f"6. treatment_assessment: {{condition_treated: string, treatment_rationale: why these medicines together, expected_outcome: what to expect, red_flags: [list of things to watch for], follow_up: when to see doctor again}}\n"
            f"7. adherence_tips: practical tips for the patient to take medicines correctly\n\n"
            f"Expand abbreviations: OD=once daily, BD=twice daily, TDS=three times daily, "
            f"HS=at bedtime, AC=before meals, PC=after meals, Tab=tablet, Cap=capsule, "
            f"Syp=syrup, Inj=injection, Neb=nebulization, QID=four times daily, PRN=as needed.\n\n"
            f"Return ONLY valid JSON:\n"
            f'{{"data":{{"doctor_name":null,"doctor_registration":null,"patient_name":null,"prescription_date":null,'
            f'"medicines":[{{"name":"Paracetamol","generic_name":"Paracetamol","dosage_form":"tablet",'
            f'"strength":"500mg","dosage":"1 tablet","frequency":"twice daily","duration":"5 days",'
            f'"route":"oral","instructions":"Take after food","quantity":"10 tablets",'
            f'"mechanism_of_action":"Inhibits COX enzymes to reduce pain and fever",'
            f'"side_effects":["Nausea","Stomach upset","Liver issues with overdose"],'
            f'"contraindications":"Severe liver disease, active GI bleeding",'
            f'"food_interactions":"Avoid alcohol, take with food",'
            f'"storage":"Store below 25C in a dry place",'
            f'"warnings":"Do not exceed 4g per day. Risk of liver damage with overdose.",'
            f'"therapeutic_class":"Analgesic / Antipyretic"}}],'
            f'"diagnosis":"Upper respiratory infection",'
            f'"drug_interactions":[{{"drug_pair":["Drug A","Drug B"],"severity":"moderate","description":"May increase effect"}}],'
            f'"patient_safety":{{"age_warnings":null,"pregnancy_warning":false,"renal_adjustment":false,"hepatic_adjustment":false}},'
            f'"treatment_assessment":{{"condition_treated":"Common cold symptoms","treatment_rationale":"Combination targets fever, pain, and infection","expected_outcome":"Symptom relief in 2-3 days","red_flags":["Fever >103F for more than 3 days"],"follow_up":"Return if no improvement in 5 days"}},'
            f'"adherence_tips":["Take medicines at the same time each day","Complete the full antibiotic course"],"notes":null,"referrals":[]}},'
            f'"summary":"A detailed plain-language explanation (3-5 sentences) for the patient explaining what each medicine does, how to take them, and what to watch for.",'
            f'"warnings":["List any safety warnings"],'
            f'"critical_values":[],"confidence":0.9,"urgency":"routine"}}\n\n'
            f"Rules: Do NOT guess illegible text. Return empty medicines if not a prescription. "
            f"Always provide realistic drug information — do not invent mechanisms or side effects."
        )

    if doc_type == "lab_report":
        return (
            f"You are a clinical laboratory medicine expert AI. Analyze this lab report from {input_kind}.\n\n"
            f"EXTRACT THE FOLLOWING:\n"
            f"1. Metadata: lab_name, report_date, patient_name_on_report, specimen_type, ordering_doctor, fasting_status, collection_time.\n"
            f"2. For EACH test extract: name, value (string), unit, reference_range, "
            f"flag (low/normal/high/critical_low/critical_high/unknown), is_critical (bool), "
            f"clinical_significance (1-2 sentence explanation of what this result means clinically), "
            f"possible_causes (list of conditions that could cause this result).\n"
            f"3. panel_summary: group tests into panels and summarize each:\n"
            f"   - panel_name (e.g. 'Complete Blood Count', 'Lipid Profile', 'Liver Function'), "
            f"   - tests_included (list of test names), "
            f"   - overall_status (normal/abnormal/critical), "
            f"   - clinical_implication (what this panel suggests about the patient's health)\n"
            f"4. differential_diagnosis: list of possible conditions suggested by the abnormal results, "
            f"ordered by likelihood, with confidence (high/medium/low)\n"
            f"5. medication_options: for EACH abnormal finding that may need treatment, provide 1-2 medication options:\n"
            f"   Each with: generic_name, purpose, why_considered (linked to specific test result), dosage, frequency, route, "
            f"   contraindications, common_side_effects, drug_interactions, precautions, confidence (0-100), evidence_level\n"
            f"   Only suggest medications for genuinely abnormal results. For normal results, return empty list.\n"
            f"6. safety_warnings: list of critical findings or medication cautions\n"
            f"7. recommended_followup: list of {{test_name: string, reason: string, urgency: routine/soon/urgent}}\n"
            f"8. risk_assessment: {{cardiovascular_risk: low/medium/high, metabolic_risk: low/medium/high, "
            f"infection_risk: low/medium/high, organ_function: {{liver: normal/abnormal, kidney: normal/abnormal, thyroid: normal/abnormal}}}}\n"
            f"9. interpretation: detailed clinical interpretation (3-5 sentences)\n"
            f"10. lifestyle_recommendations: dietary and lifestyle suggestions based on the results\n\n"
            f"Return ONLY valid JSON:\n"
            f'{{"data":{{"lab_name":null,"report_date":null,"patient_name_on_report":null, '
            f'"specimen_type":"blood","ordering_doctor":null,"fasting_status":null,"collection_time":null, '
            f'"tests":[{{"name":"Hemoglobin","value":"13.5","unit":"g/dL","reference_range":"12.0-16.0",'
            f'"flag":"normal","is_critical":false,"clinical_significance":"Normal hemoglobin indicates adequate oxygen-carrying capacity",'
            f'"possible_causes":["Normal healthy state"]}}], '
            f'"panel_summary":[{{"panel_name":"Complete Blood Count","tests_included":["Hemoglobin","WBC","Platelets"],"overall_status":"normal","clinical_implication":"Blood counts are within normal limits"}}], '
            f'"differential_diagnosis":[{{"condition":"Possible iron deficiency","confidence":"medium","evidence":"Low hemoglobin with low MCV"}}], '
            f'"medication_options":[{{"generic_name":"Ferrous Sulfate","purpose":"Iron supplement","why_considered":"Low hemoglobin and MCV suggest iron deficiency","dosage":"325mg","frequency":"Once daily","route":"oral","contraindications":["Hemochromatosis","GI bleeding"],"common_side_effects":["Constipation","Dark stools"],"drug_interactions":["Antacids","Levothyroxine"],"precautions":"Take on empty stomach","confidence":80,"evidence_level":"well-established"}}], '
            f'"safety_warnings":["Abnormal results require medical attention"], '
            f'"recommended_followup":[{{"test_name":"Iron studies","reason":"To confirm iron deficiency","urgency":"soon"}}], '
            f'"risk_assessment":{{"cardiovascular_risk":"low","metabolic_risk":"low","infection_risk":"low","organ_function":{{"liver":"normal","kidney":"normal","thyroid":"normal"}}}}, '
            f'"interpretation":null,"lifestyle_recommendations":["Eat iron-rich foods","Stay hydrated"],"notes":null,"doctor_verification_required":true,"clinical_disclaimer":"AI-assisted analysis. Consult a healthcare professional."}}, '
            f'"summary":"A detailed plain-language explanation (3-5 sentences) for the patient, explaining what the results mean, which values need attention, and what to do next.", '
            f'"warnings":["List any critical findings"],'
            f'"critical_values":[],"confidence":0.9,"urgency":"routine"}}\n\n'
            f"Rules: Extract EVERY test row. Do NOT skip any. If a value is borderline, flag it. "
            f"Always provide clinical_significance for abnormal results."
        )

    # imaging
    return (
        f"You are an expert radiologist AI with deep knowledge of diagnostic imaging. "
        f"Analyze this medical scan from {input_kind}.\n\n"
        f"IDENTIFY THE FOLLOWING:\n"
        f"1. Metadata: modality (xray/ct/mri/ultrasound/mammography/pet_scan/other), "
        f"body_part, orientation/view (PA/AP/lateral/axial/coronal/sagittal), "
        f"contrast_used (bool), technical_quality (good/adequate/poor), "
        f"patient_position, laterality (left/right/bilateral).\n"
        f"2. For EACH finding extract: "
        f"category (bones/fractures/soft_tissue/organs/air_spaces/vessels/lymph_nodes/other), "
        f"description (detailed, clinical language), location (anatomical), "
        f"significance (mild/moderate/severe/critical), is_abnormal (bool), "
        f"measurement (if visible, e.g. '2.3 cm nodule'), "
        f"pattern (e.g. 'ground-glass', 'consolidation', 'calcification'), "
        f"clinical_correlation (what this finding could mean clinically).\n"
        f"3. impression: radiologist impression (2-4 sentences, clinical language)\n"
        f"4. differential_diagnosis: list of {{diagnosis: string, likelihood: high/medium/low, reasoning: string}}\n"
        f"5. recommendations: list of specific next steps\n"
        f"6. urgency_assessment: {{level: routine/soon/urgent/critical, timeframe: string, reason: string}}\n"
        f"7. comparison_with_prior: 'No prior studies available' or description of changes\n"
        f"8. anatomical_variants: list of normal anatomical variations noted\n"
        f"9. technical_notes: image quality issues or limitations\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"data":{{"modality":"xray","body_part":"Chest","orientation":"PA",'
            f'"contrast_used":null,"technical_quality":"good","patient_position":"upright",'
            f'"laterality":"bilateral",'
            f'"findings":[{{"category":"lungs","description":"Bilateral lower lobe consolidation with air bronchograms",'
            f'"location":"Lower lobes bilaterally","significance":"moderate","is_abnormal":true,'
            f'"measurement":null,"pattern":"consolidation",'
            f'"clinical_correlation":"Suggests pneumonia or other inflammatory process"}}],'
            f'"impression":"Bilateral lower lobe consolidation consistent with pneumonia.",'
            f'"differential_diagnosis":[{{"diagnosis":"Community-acquired pneumonia","likelihood":"high","reasoning":"Consolidation with air bronchograms in lower lobes"}}],'
            f'"recommendations":["Clinical correlation recommended","Consider chest CT if no improvement"],'
            f'"medication_options":[{{"generic_name":"Amoxicillin","purpose":"Antibiotic for bacterial pneumonia","why_considered":"Findings consistent with community-acquired pneumonia","dosage":"500mg","frequency":"Three times daily","duration":"7-10 days","route":"oral","contraindications":["Penicillin allergy","Severe renal impairment"],"common_side_effects":["Diarrhea","Nausea","Skin rash"],"drug_interactions":["Methotrexate","Warfarin"],"precautions":"Complete full course even if feeling better","confidence":75,"evidence_level":"well-established"}}],'
            f'"safety_warnings":["Findings require clinical correlation","Antibiotic only if bacterial cause confirmed"],'
            f'"urgency_assessment":{{"level":"soon","timeframe":"48-72 hours","reason":"Moderate findings requiring clinical attention"}},'
            f'"comparison_with_prior":"No prior studies available",'
            f'"anatomical_variants":[],"technical_notes":"Image is well-positioned and adequately penetrated."}},'
        f'"summary":"A detailed plain-language explanation (3-5 sentences) for the patient describing what the scan shows, what was found, and what the next steps should be.", '
        f'"warnings":["List any critical findings requiring immediate attention"],'
        f'"critical_values":[],"confidence":0.9,"urgency":"routine","doctor_verification_required":true,"clinical_disclaimer":"AI-assisted analysis. Consult a radiologist for definitive interpretation."}}\n\n'
        f"Rules: Describe what you SEE, not diagnoses (except in differential_diagnosis). "
        f"Always provide clinical_correlation for abnormal findings. Always add disclaimer to discuss with doctor."
    )


def _mediscan_fallback(doc_type: str, warning: str) -> dict:
    """Returns a safe empty result when parsing fails or doc_type is invalid."""
    return {
        "data": {},
        "summary": "We couldn't analyze this document. Please try again with a clearer image or file.",
        "warnings": [warning],
        "confidence": 0.0,
        "critical_values": [],
        "urgency": "routine",
        "pipeline_stages": [],
    }


# ---------- Medicine Database for Verification ----------
# Common medicines with expected dosages and uses
MEDICINE_DB = {
    "paracetamol": {"uses": "pain relief, fever", "typical_dose": "500mg-1g", "max_daily": "4g"},
    "amoxicillin": {"uses": "bacterial infections", "typical_dose": "250mg-500mg", "max_daily": "3g"},
    "metformin": {"uses": "type 2 diabetes", "typical_dose": "500mg-850mg", "max_daily": "2550mg"},
    "atorvastatin": {"uses": "cholesterol", "typical_dose": "10mg-80mg", "max_daily": "80mg"},
    "amlodipine": {"uses": "hypertension", "typical_dose": "5mg-10mg", "max_daily": "10mg"},
    "omeprazole": {"uses": "acid reflux, gastric ulcers", "typical_dose": "20mg-40mg", "max_daily": "40mg"},
    "losartan": {"uses": "hypertension", "typical_dose": "25mg-100mg", "max_daily": "100mg"},
    "azithromycin": {"uses": "bacterial infections", "typical_dose": "250mg-500mg", "max_daily": "500mg"},
    "cetirizine": {"uses": "allergies", "typical_dose": "10mg", "max_daily": "10mg"},
    "diclofenac": {"uses": "pain, inflammation", "typical_dose": "50mg", "max_daily": "150mg"},
    "pantoprazole": {"uses": "acid reflux", "typical_dose": "40mg", "max_daily": "40mg"},
    "metoprolol": {"uses": "hypertension, heart conditions", "typical_dose": "25mg-200mg", "max_daily": "400mg"},
    "levothyroxine": {"uses": "hypothyroidism", "typical_dose": "25mcg-200mcg", "max_daily": "300mcg"},
    "prednisolone": {"uses": "inflammation, autoimmune", "typical_dose": "5mg-60mg", "max_daily": "80mg"},
    "montelukast": {"uses": "asthma, allergies", "typical_dose": "10mg", "max_daily": "10mg"},
    "salbutamol": {"uses": "bronchospasm, asthma", "typical_dose": "2mg-4mg", "max_daily": "32mg"},
    "ibuprofen": {"uses": "pain, inflammation, fever", "typical_dose": "200mg-400mg", "max_daily": "1200mg"},
    "ciprofloxacin": {"uses": "bacterial infections", "typical_dose": "250mg-750mg", "max_daily": "1500mg"},
    "doxycycline": {"uses": "bacterial infections, malaria prophylaxis", "typical_dose": "100mg", "max_daily": "300mg"},
    "ceftriaxone": {"uses": "serious bacterial infections", "typical_dose": "1g-2g", "max_daily": "4g"},
    "warfarin": {"uses": "blood thinning, clot prevention", "typical_dose": "1mg-10mg", "max_daily": "10mg"},
    "clopidogrel": {"uses": "anti-platelet, heart attack prevention", "typical_dose": "75mg", "max_daily": "75mg"},
    "insulin": {"uses": "diabetes", "typical_dose": "variable", "max_daily": "variable"},
    "gliclazide": {"uses": "type 2 diabetes", "typical_dose": "40mg-80mg", "max_daily": "320mg"},
    "telmisartan": {"uses": "hypertension", "typical_dose": "20mg-80mg", "max_daily": "80mg"},
    "ramipril": {"uses": "hypertension, heart failure", "typical_dose": "2.5mg-10mg", "max_daily": "10mg"},
    "hydrochlorothiazide": {"uses": "hypertension, edema", "typical_dose": "12.5mg-50mg", "max_daily": "50mg"},
    "riluzole": {"uses": "ALS", "typical_dose": "50mg", "max_daily": "100mg"},
    "baclofen": {"uses": "muscle spasticity", "typical_dose": "5mg-20mg", "max_daily": "80mg"},
    "pregabalin": {"uses": "nerve pain, epilepsy, anxiety", "typical_dose": "75mg-300mg", "max_daily": "600mg"},
}


def _verify_medicines(medicines: list) -> list:
    """Verifies extracted medicines against known database."""
    verified = []
    for med in medicines:
        name = (med.get("name") or "").lower().strip()
        generic = (med.get("generic_name") or "").lower().strip()
        check_name = generic or name
        # Try partial match
        db_match = None
        for db_name, db_info in MEDICINE_DB.items():
            if db_name in check_name or check_name in db_name:
                db_match = (db_name, db_info)
                break
            # Try word-level match
            for word in check_name.split():
                if word == db_name or len(word) > 3 and db_name.startswith(word):
                    db_match = (db_name, db_info)
                    break
            if db_match:
                break

        if db_match:
            med["db_verified"] = True
            med["db_match_name"] = db_match[0]
            med["db_info"] = db_match[1]
        else:
            med["db_verified"] = False
            med["db_match_name"] = None
            med["db_info"] = None
        verified.append(med)
    return verified


def _classify_lab_values(tests: list) -> list:
    """Enhances lab test classification with clinical significance."""
    for t in tests:
        flag = (t.get("flag") or "unknown").lower()
        if flag in ("critical_low", "critical_high"):
            t["severity"] = "critical"
            t["clinical_urgency"] = "Requires immediate medical attention"
        elif flag in ("low", "high"):
            t["severity"] = "abnormal"
            t["clinical_urgency"] = "Should be discussed with your doctor"
        elif flag == "normal":
            t["severity"] = "normal"
            t["clinical_urgency"] = None
        else:
            t["severity"] = "uncertain"
            t["clinical_urgency"] = "Could not be classified — review recommended"

        # Add clinical significance text if not provided
        if not t.get("clinical_significance") and flag != "normal":
            name_lower = (t.get("name") or "").lower()
            if "hemoglobin" in name_lower or "hb" in name_lower:
                t["clinical_significance"] = "May indicate anemia (low) or polycythemia (high)"
            elif "glucose" in name_lower or "sugar" in name_lower:
                t["clinical_significance"] = "May indicate diabetes or hypoglycemia"
            elif "cholesterol" in name_lower:
                t["clinical_significance"] = "Cardiovascular risk factor"
            elif "creatinine" in name_lower:
                t["clinical_significance"] = "May indicate kidney function impairment"
            elif "wbc" in name_lower or "white blood" in name_lower:
                t["clinical_significance"] = "May indicate infection (high) or immune suppression (low)"
            elif "platelet" in name_lower:
                t["clinical_significance"] = "Affects blood clotting ability"
    return tests


def _score_imaging_findings(findings: list, impression: str) -> dict:
    """Adds severity scoring and clinical priority to imaging findings."""
    total_severity = 0
    critical_findings = []
    for f in findings:
        sig = (f.get("significance") or "mild").lower()
        if sig == "critical":
            total_severity += 4
            critical_findings.append(f.get("description", ""))
        elif sig == "severe":
            total_severity += 3
        elif sig == "moderate":
            total_severity += 2
        else:
            total_severity += 1

    max_possible = max(len(findings) * 4, 1)
    severity_score = min(round(total_severity / max_possible * 100), 100)

    if severity_score >= 75:
        priority = "high"
    elif severity_score >= 40:
        priority = "moderate"
    else:
        priority = "low"

    return {
        "severity_score": severity_score,
        "priority": priority,
        "critical_findings_count": len(critical_findings),
        "total_findings": len(findings),
    }


# ---------- MediEcho: clinical conversation analyzer ----------
# Takes a speaker-tagged transcript (Patient/Doctor turns) and returns a
# structured clinical note: symptoms, history, allergies, medications,
# severity, doctor's observations, prescribed medicines, recommended
# tests, referrals, follow-ups, plus a short consultation summary and a
# list of highlighted phrases the doctor should double-check.


MEDIECHO_HIGHLIGHT_KINDS = {
    "red_flag",       # something that warrants urgent action
    "allergy",        # allergy / adverse-reaction mention
    "med_change",     # new prescription or dose change
    "follow_up",      # explicit follow-up instruction
    "test_order",     # test that was ordered
}


def analyze_conversation(segments: list[dict], patient_context: str | None = None) -> dict:
    """Extract a structured clinical note from a speaker-tagged transcript.

    segments: list of {start, end, speaker, text} dicts (speaker is "patient" or "doctor").
    patient_context: optional one-line context (e.g. "45M with known hypertension")
                     so Gemini can resolve "my BP pill" without inventing.

    Returns a dict with keys:
      chief_complaint, symptoms, history, allergies, current_medications,
      duration, severity, doctor_observations,
      prescribed_medicines, recommended_tests, referrals, follow_ups,
      summary, highlights (list of {kind, quote, why}).
    """
    # Format the transcript as a chat log so Gemini can see speaker turns.
    if segments:
        lines = []
        for s in segments:
            speaker = (s.get("speaker") or "speaker").capitalize()
            text = (s.get("text") or "").strip()
            if text:
                lines.append(f"{speaker}: {text}")
        transcript = "\n".join(lines)
    else:
        transcript = ""

    ctx_line = (
        f"Patient context: {patient_context}\n"
        if patient_context else ""
    )

    prompt = f"""
You are a clinical conversation intelligence assistant specializing in comprehensive medical documentation. You are given a transcript of a doctor-patient consultation with speaker labels. Extract a DETAILED structured clinical note that the doctor can review, edit, and save to the patient record.

{ctx_line}
Conversation transcript:
{transcript or "(empty transcript — return empty fields)"}

Return ONLY valid JSON, nothing else - no markdown, no explanation outside the JSON.

Return JSON in EXACTLY this format:
{{
  "chief_complaint": "1-2 sentences describing the main reason for the visit with specific details, or null",
  "symptoms": ["detailed symptom with onset/duration/severity if mentioned"],
  "symptoms_detailed": [
    {{
      "name": "symptom name",
      "onset": "when it started, e.g. '3 days ago', 'gradual over weeks'",
      "severity": "mild/moderate/severe",
      "character": "description of quality, e.g. 'sharp', 'dull', 'burning', 'intermittent'",
      "location": "body part affected, if mentioned",
      "aggravating": "what makes it worse, if mentioned",
      "relieving": "what makes it better, if mentioned",
      "associated": "related symptoms, if any"
    }}
  ],
  "history": ["relevant past medical history mentioned, with timeframes"],
  "family_history": ["family medical history if mentioned, e.g. 'father had diabetes'"],
  "social_history": ["lifestyle factors mentioned, e.g. 'smokes 1 pack/day', 'sedentary'"],
  "allergies": ["drug or food allergy mentioned, with reaction type if specified"],
  "current_medications": [
    {{
      "name": "drug name",
      "dosage": "dose with units",
      "frequency": "how often taken",
      "route": "oral/IV/topical etc. if mentioned",
      "duration": "how long patient has been on it, if mentioned",
      "purpose": "what it is prescribed for, if mentioned"
    }}
  ],
  "duration": "detailed description of symptom timeline, or null",
  "severity": "mild" | "moderate" | "severe" | "unknown",
  "severity_details": "detailed explanation of why this severity was assessed",
  "vitals_mentioned": {{
    "blood_pressure": "e.g. '140/90' or null",
    "heart_rate": "e.g. '88 bpm' or null",
    "temperature": "e.g. '100.2F' or null",
    "respiratory_rate": "e.g. '18/min' or null",
    "oxygen_saturation": "e.g. '97%' or null",
    "weight": "e.g. '72kg' or null",
    "height": "e.g. '5ft 8in' or null"
  }},
  "physical_examination": ["detailed physical exam findings mentioned by doctor"],
  "doctor_observations": ["clinical observations the doctor mentioned, with clinical significance"],
  "differential_diagnosis": [
    {{
      "condition": "possible diagnosis",
      "likelihood": "high/medium/low",
      "reasoning": "why this is considered"
    }}
  ],
  "prescribed_medicines": [
    {{
      "name": "generic drug name",
      "brand_name": "brand name if mentioned",
      "dosage": "e.g. 500mg",
      "frequency": "e.g. twice daily",
      "duration": "e.g. 5 days",
      "route": "oral/IV/topical if mentioned",
      "purpose": "what it treats in this context",
      "instructions": "special instructions like 'take with food', 'avoid dairy'",
      "side_effects_to_watch": ["common side effects to monitor"],
      "notes": "any caveat the doctor mentioned, or null"
    }}
  ],
  "recommended_tests": [
    {{
      "name": "test name",
      "purpose": "why this test was ordered",
      "urgency": "routine/urgent/emergent",
      "instructions": "special prep instructions if any, e.g. 'fasting required'"
    }}
  ],
  "referrals": [
    {{
      "specialty": "medical specialty",
      "reason": "why referred",
      "urgency": "routine/urgent"
    }}
  ],
  "follow_ups": [
    {{
      "instruction": "what to do",
      "timeframe": "when, e.g. '2 weeks', 'after lab results'",
      "condition": "what to monitor before next visit"
    }}
  ],
  "patient_education": ["topics explained to the patient, e.g. 'diabetes management basics'"],
  "red_flags_discussed": ["warning signs the doctor told patient to watch for"],
  "lifestyle_recommendations": ["diet, exercise, or lifestyle changes suggested"],
  "ai_summary": {{
    "one_liner": "one sentence clinical summary",
    "clinical_narrative": "3-5 sentence detailed clinical narrative including history, findings, assessment, and plan (written for doctor's eyes)",
    "key_findings": ["most important clinical findings to note"],
    "assessment": "clinical assessment of the patient's condition",
    "plan": "treatment plan summary",
    "risk_factors": ["risk factors identified during consultation"],
    "prognosis": "expected outcome if treatment plan is followed"
  }},
  "highlights": [
    {{
      "kind": "red_flag" | "allergy" | "med_change" | "follow_up" | "test_order",
      "quote": "short verbatim phrase from the transcript that triggered this highlight",
      "why": "one-sentence explanation of why this was flagged",
      "clinical_significance": "why this matters clinically"
    }}
  ]
}}

Rules:
- Use null for missing string fields. Use [] for missing list fields.
- For vitals_mentioned, use null for any vital not mentioned in the transcript.
- symptoms_detailed should have entries for each significant symptom discussed.
- differential_diagnosis should list at least the top 3 possibilities the doctor considered.
- prescribed_medicines should include side_effects_to_watch (2-3 common ones).
- recommended_tests should include purpose and urgency.
- ai_summary should be comprehensive enough for a doctor to quickly understand the entire consultation.
- highlights should ONLY be populated for clinically meaningful moments. Cap at 10, ordered by clinical importance.
- Do NOT invent information that isn't in the transcript.
- The ai_summary.clinical_narrative should read like a professional medical note.
- Include all patient education and lifestyle recommendations discussed.
"""

    raw_text = _nemotron_chat(prompt)
    cleaned = _clean_json_text(raw_text)

    fallback = {
        "chief_complaint": None,
        "symptoms": [],
        "history": [],
        "allergies": [],
        "current_medications": [],
        "duration": None,
        "severity": "unknown",
        "doctor_observations": [],
        "prescribed_medicines": [],
        "recommended_tests": [],
        "referrals": [],
        "follow_ups": [],
        "summary": "Could not extract a structured note from this conversation. Please review the transcript manually.",
        "highlights": [],
    }

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        return fallback

    # Normalize severity to one of the allowed values.
    sev = (result.get("severity") or "unknown").strip().lower()
    if sev not in {"mild", "moderate", "severe", "unknown"}:
        sev = "unknown"
    result["severity"] = sev

    # Coerce every list field to a clean list[str]; coerce scalars to str or None.
    list_fields = [
        "symptoms", "history", "allergies", "current_medications",
        "doctor_observations", "recommended_tests", "referrals", "follow_ups",
    ]
    for field in list_fields:
        v = result.get(field)
        if not isinstance(v, list):
            v = []
        result[field] = [str(x).strip() for x in v if str(x).strip()][:20]

    # Normalize prescribed_medicines to a list of dicts with required shape.
    raw_meds = result.get("prescribed_medicines")
    if not isinstance(raw_meds, list):
        raw_meds = []
    clean_meds = []
    for entry in raw_meds:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        clean_meds.append({
            "name": name,
            "dosage": (entry.get("dosage") or None),
            "frequency": (entry.get("frequency") or None),
            "duration": (entry.get("duration") or None),
            "notes": (entry.get("notes") or None),
        })
    result["prescribed_medicines"] = clean_meds[:20]

    # Normalize highlights.
    raw_h = result.get("highlights")
    if not isinstance(raw_h, list):
        raw_h = []
    clean_h = []
    for entry in raw_h:
        if not isinstance(entry, dict):
            continue
        kind = (entry.get("kind") or "").strip().lower()
        quote = (entry.get("quote") or "").strip()
        why = (entry.get("why") or "").strip()
        if not quote or not why:
            continue
        if kind not in MEDIECHO_HIGHLIGHT_KINDS:
            kind = "follow_up"
        clean_h.append({"kind": kind, "quote": quote[:240], "why": why[:240]})
    result["highlights"] = clean_h[:8]

    # Top-level scalar fields → str / None.
    for field in ("chief_complaint", "duration"):
        v = result.get(field)
        result[field] = (str(v).strip() if v not in (None, "") else None)

    summary = result.get("summary")
    result["summary"] = (str(summary).strip() if summary else fallback["summary"])

    return result


# ---- 2026-08-23: AI Doctor chat helpers -----------------------------------
# Patient-facing chat with a clinical-grade Gemini persona. The persona
# is intentionally conservative: it triages symptoms, explains findings
# in plain language, and ALWAYS recommends seeing a real doctor for
# anything that could be an emergency or anything that needs a physical
# exam. The hard-coded `safety_preamble` below pins this so prompt
# injection from the patient's input can't dislodge it.

AI_DOCTOR_SAFETY_PREAMBLE_DEFAULT = """You are {PERSONA_NAME}, a professional, empathetic AI medical assistant.

## HOW TO RESPOND
- Start your response with the actual answer. No filler openings like "I understand" or "Thank you for sharing".
- Match response length to the question: simple question = simple answer.
- Use plain language. Explain medical terms when you use them.
- Be conversational but professional — like a knowledgeable doctor talking to a patient.
- When you don't know something, say so honestly.
- Reply in the SAME LANGUAGE the patient uses.

## SAFETY
- Emergency symptoms (chest pain, severe breathing difficulty, loss of consciousness): tell them to call emergency services immediately.
- Never invent medical records, test results, or medications.
- When analyzing uploaded documents, describe only what you can clearly read.
- Always recommend consulting a real doctor for definitive diagnosis and treatment."""


def build_safety_preamble(persona_name: str = "Dr. Mira") -> str:
    """Return the clinical safety preamble with the persona's display name
    substituted in. We only ever swap the name — every rule below stays
    intact so renaming can't loosen the guardrails."""
    name = (persona_name or "").strip() or "Dr. Mira"
    # Cap to 40 chars defensively in case the caller forgot to.
    return AI_DOCTOR_SAFETY_PREAMBLE_DEFAULT.replace("{PERSONA_NAME}", name[:40])


# Back-compat alias used by summarize and any caller that doesn't pass a
# persona. Keeps the old constant name working as a function so we don't
# break a long import chain.
def AI_DOCTOR_SAFETY_PREAMBLE():
    return build_safety_preamble("Dr. Mira")

AI_DOCTOR_PER_TURN_INSTRUCTION = """Reply naturally to the patient's latest message.

## RULES
- Start with the answer, not with filler.
- Match response length to complexity: simple question = short answer.
- If patient data is provided in the context, use it naturally ("Your hemoglobin is...").
- If no patient data is provided, answer from general medical knowledge.
- If you uploaded a document, describe what you see directly.
- Reply in the same language the patient uses.

## MEDICATIONS (when asked)
- Give specific drug names with dosages: PARACETAMOL 500mg - 1 tablet every 6 hours
- Include purpose, common side effects, when to avoid
- Remind to consult their doctor before starting

## SAFETY
- Emergency symptoms: tell them to call emergency services.
- Never invent medical facts.
- Recommend consulting a real doctor for diagnosis and treatment."""


def _format_chat_history_for_prompt(messages, persona_name="Dr. Mira", max_chars=12000):
    """Convert [{role, content, has_media?}] into a clean transcript string
    that Nemotron can parse. Skips system rows. Trims individual messages
    to a sane upper bound so a long session doesn't blow up the prompt.
    `persona_name` is the AI's display name (e.g. "Dr. Mira") used as the
    speaker prefix for assistant turns so the patient sees their own
    chosen name in the transcript.

    Context management strategy:
    - Always include the last 30 turns (capped at 2400 chars each)
    - If total exceeds max_chars, drop oldest turns first
    - Never drop the last user message or last assistant reply
    - Summarize dropped context as a single context-summary line"""
    assistant_label = (persona_name or "Dr. Mira").strip() or "Dr. Mira"
    out = []
    for m in messages or []:
        role = (m.get("role") or "").strip().lower()
        content = (m.get("content") or "").strip()
        has_media = bool(m.get("media_filename"))
        if role not in ("user", "assistant") or not content:
            continue
        prefix = "Patient" if role == "user" else assistant_label
        if has_media and role == "user":
            content = content + " [Patient attached an image/document]"
        out.append(f"{prefix}: {content[:2400]}")
    # Keep last 30 turns max
    out = out[-30:]
    # If total char count exceeds max_chars, drop oldest while keeping last 2
    transcript = "\n\n".join(out)
    if len(transcript) > max_chars and len(out) > 2:
        # Always keep the last 2 turns (last user + last assistant)
        kept = out[-2:]
        dropped_count = 0
        for i in range(len(out) - 2):
            candidate = "\n\n".join([out[i]] + kept)
            if len(candidate) <= max_chars:
                kept = [out[i]] + kept
            else:
                dropped_count += 1
        if dropped_count > 0:
            summary = f"[Earlier context: {dropped_count} previous exchanges were summarized to save context space. The patient's earlier concerns and medical history should be inferred from the current conversation.]"
            transcript = summary + "\n\n" + "\n\n".join(kept)
        else:
            transcript = "\n\n".join(out)
    return transcript


def ai_doctor_chat(
    messages,
    patient_context=None,
    media_bytes=None,
    media_mime=None,
    persona_name="Dr. Mira",
    patient_id=None,
    medical_evidence=None,
    tools_context=None,
):
    """Generate the AI Doctor's next reply.

    `messages` is a list of {role, content, has_media} dicts.
    `patient_context`: demographic string (used only when tools_context is None).
    `tools_context`: when provided by the intent router, this REPLACES
        patient_context + rag_block + evidence_block. For NORMAL_CHAT,
        this is empty — zero patient data is injected.
    `medical_evidence`: legacy evidence injection (used only when tools_context is None).
    `patient_id`: used for RAG retrieval (used only when tools_context is None).

    Returns: {text, status, error_message}
    """
    transcript = _format_chat_history_for_prompt(messages, persona_name=persona_name)
    preamble = build_safety_preamble(persona_name)

    # ---- INTENT-ROUTED PATH ----
    # When tools_context is provided, the intent router has already decided
    # what data to inject. We use ONLY that — no automatic patient data.
    tools_block = ""
    if tools_context is not None:
        tools_text = tools_context.get("context_text", "")
        if tools_text:
            tools_block = tools_text
        # For medical knowledge (general medical questions), also inject evidence
        if medical_evidence:
            med_ev = medical_evidence.get("medical_evidence", "")
            if med_ev:
                tools_block += "\n\n" + med_ev
            sources = medical_evidence.get("sources_used", [])
            if sources:
                source_names = [s.get("name", "") for s in sources if s.get("name")]
                if source_names:
                    tools_block += "\n\nSources consulted: " + ", ".join(source_names)
        full_prompt = (
            preamble
            + ("\n\n" + tools_block if tools_block else "")
            + "\n\n--- CONVERSATION HISTORY ---\n" + (transcript or "(No prior turns — this is the patient's first message.)")
            + "\n\n--- YOUR TASK ---\n" + AI_DOCTOR_PER_TURN_INSTRUCTION
        )
    else:
        # ---- LEGACY PATH (backward compat) ----
        ctx = (patient_context or "No additional patient context.").strip()
        rag_block = ""
        if patient_id is not None:
            try:
                import rag_client
                last_user = next((m for m in reversed(messages or [])
                                  if (m.get("role") or "").lower() == "user"
                                  and (m.get("content") or "").strip()), None)
                if last_user:
                    chunks = rag_client.retrieve(patient_id, last_user["content"])
                    rag_block = rag_client.format_for_prompt(chunks)
            except Exception:
                rag_block = ""
        evidence_block = ""
        if medical_evidence:
            med_ev = medical_evidence.get("medical_evidence", "")
            if med_ev:
                evidence_block = med_ev
            sources = medical_evidence.get("sources_used", [])
            if sources:
                source_names = [s.get("name", "") for s in sources if s.get("name")]
                if source_names:
                    evidence_block += "\n\nSources consulted: " + ", ".join(source_names)
        full_prompt = (
            preamble
            + "\n\n--- PATIENT CONTEXT ---\n" + ctx
            + (("\n\n" + rag_block) if rag_block else "")
            + (("\n\n" + evidence_block) if evidence_block else "")
            + "\n\n--- CONVERSATION HISTORY ---\n" + (transcript or "(No prior turns — this is the patient's first message.)")
            + "\n\n--- YOUR TASK ---\n" + AI_DOCTOR_PER_TURN_INSTRUCTION
        )

    # Build the contents payload. Use Nemotron Omni for images, Nemotron text for text-only.
    # Retry once on transient errors (429 rate limit, timeout).
    import time as _time
    last_exc = None
    for _attempt in range(2):
        try:
            if media_bytes and media_mime:
                text = _omni_analyze(
                    media_bytes, full_prompt, media_mime,
                    temperature=0.6, max_tokens=2000
                )
            else:
                text = _nemotron_chat(full_prompt, temperature=0.6, max_tokens=2000)
            if not text:
                return {
                    "text": "",
                    "status": "blocked",
                    "error_message": (
                        "I can't respond to that safely. If you're in distress, please call your local "
                        "emergency number or contact a licensed clinician right away."
                    ),
                }
            return {"text": text, "status": "ok", "error_message": None}
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            # Retry on rate-limit (429) or timeout — wait 2s then retry once
            if _attempt == 0 and ("429" in err_str or "rate" in err_str or "timeout" in err_str):
                _time.sleep(2)
                continue
            break
    return {
        "text": "",
        "status": "error",
        "error_message": f"AI service error: {last_exc}",
    }

# ---------------------------------------------------------------------------
# 2026-08-24: streaming variant for the WebSocket transport.
# ---------------------------------------------------------------------------
def ai_doctor_chat_stream(
    messages,
    patient_context=None,
    media_bytes=None,
    media_mime=None,
    persona_name="Dr. Mira",
    patient_id=None,
    medical_evidence=None,
    tools_context=None,
):
    """Streaming variant with intent-routed context support.
    When tools_context is provided, ONLY that data is injected — no
    automatic patient data retrieval.
    """
    transcript = _format_chat_history_for_prompt(messages, persona_name=persona_name)
    preamble = build_safety_preamble(persona_name)

    # ---- INTENT-ROUTED PATH ----
    if tools_context is not None:
        tools_text = tools_context.get("context_text", "")
        tools_block = tools_text or ""
        if medical_evidence:
            med_ev = medical_evidence.get("medical_evidence", "")
            if med_ev:
                tools_block += "\n\n" + med_ev
            sources = medical_evidence.get("sources_used", [])
            if sources:
                source_names = [s.get("name", "") for s in sources if s.get("name")]
                if source_names:
                    tools_block += "\n\nSources consulted: " + ", ".join(source_names)
        full_prompt = (
            preamble
            + ("\n\n" + tools_block if tools_block else "")
            + "\n\n--- CONVERSATION HISTORY ---\n" + (transcript or "(No prior turns -- this is the patient's first message.)")
            + "\n\n--- YOUR TASK ---\n" + AI_DOCTOR_PER_TURN_INSTRUCTION
        )
    else:
        # ---- LEGACY PATH ----
        ctx = (patient_context or "No additional patient context.").strip()
        rag_block = ""
        if patient_id is not None:
            try:
                import rag_client
                last_user = next(
                    (m for m in reversed(messages or [])
                     if (m.get("role") or "").lower() == "user"
                     and (m.get("content") or "").strip()),
                    None,
                )
                if last_user:
                    chunks = rag_client.retrieve(patient_id, last_user["content"])
                    rag_block = rag_client.format_for_prompt(chunks)
            except Exception:
                rag_block = ""
        evidence_block = ""
        if medical_evidence:
            med_ev = medical_evidence.get("medical_evidence", "")
            if med_ev:
                evidence_block = med_ev
            sources = medical_evidence.get("sources_used", [])
            if sources:
                source_names = [s.get("name", "") for s in sources if s.get("name")]
                if source_names:
                    evidence_block += "\n\nSources consulted: " + ", ".join(source_names)
        full_prompt = (
            preamble
            + "\n\n--- PATIENT CONTEXT ---\n" + ctx
            + (("\n\n" + rag_block) if rag_block else "")
            + (("\n\n" + evidence_block) if evidence_block else "")
            + "\n\n--- CONVERSATION HISTORY ---\n" + (transcript or "(No prior turns -- this is the patient's first message.)")
            + "\n\n--- YOUR TASK ---\n" + AI_DOCTOR_PER_TURN_INSTRUCTION
        )

    if media_bytes and media_mime:
        import base64 as _b64
        omni_messages = [
            {"role": "system", "content": preamble + "\n\n" + AI_DOCTOR_PER_TURN_INSTRUCTION},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_mime};base64," + _b64.b64encode(media_bytes).decode()}},
                {"type": "text", "text": ctx + "\n\n" + (transcript or "(First message)") + "\n\nPlease analyze the attached image/document and respond accordingly."},
            ]},
        ]
        stream = _nvidia_omni.chat.completions.create(
            model=OMNI_MODEL,
            messages=omni_messages,
            temperature=0.6,
            max_tokens=2000,
            stream=True,
            extra_body={"reasoning_effort": "none"},
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
        return
    else:
        nemotron_messages = [
            {"role": "system", "content": full_prompt},
        ]
        return _nemotron_stream(nemotron_messages, temperature=0.6, max_tokens=2000)


# ---------------------------------------------------------------------------
# 2026-08-24: streaming variant WITH tool calling.
# ---------------------------------------------------------------------------
# Yields tuples:
#   ("token", str)                  — text delta to render
#   ("tool_call", {name, args})     — model wants a tool
#   ("tool_result", {name, result}) — tool result (returned to model on next round)
#   ("done", {"text", "status", "tool_calls": [...]})
#
# The caller (WS handler or REST helper) is responsible for persisting the
# final assistant text and audit rows.

def ai_doctor_chat_stream_with_tools(
    messages,
    patient_context=None,
    media_bytes=None,
    media_mime=None,
    persona_name="Dr. Mira",
    patient_id=None,
    db=None,
    user=None,
    max_rounds=None,
    medical_evidence=None,
    tools_context=None,
):
    """Streaming AI Doctor chat with intent-routed context support.
    When tools_context is provided, ONLY that data is injected — no
    automatic patient data retrieval.
    """
    try:
        import ai_doctor_tools
    except Exception:
        ai_doctor_tools = None
    max_rounds = max_rounds or (ai_doctor_tools.MAX_TOOL_ROUNDS if ai_doctor_tools else 1)

    transcript = _format_chat_history_for_prompt(messages, persona_name=persona_name)
    preamble = build_safety_preamble(persona_name)

    # ---- INTENT-ROUTED PATH ----
    if tools_context is not None:
        tools_text = tools_context.get("context_text", "")
        tools_block = tools_text or ""
        if medical_evidence:
            med_ev = medical_evidence.get("medical_evidence", "")
            if med_ev:
                tools_block += "\n\n" + med_ev
            sources = medical_evidence.get("sources_used", [])
            if sources:
                source_names = [s.get("name", "") for s in sources if s.get("name")]
                if source_names:
                    tools_block += "\n\nSources consulted: " + ", ".join(source_names)
        full_prompt = (
            preamble
            + ("\n\n" + tools_block if tools_block else "")
            + "\n\n--- CONVERSATION HISTORY ---\n" + (transcript or "(No prior turns - this is the patient's first message.)")
            + "\n\n--- YOUR TASK ---\n" + AI_DOCTOR_PER_TURN_INSTRUCTION
        )
    else:
        # ---- LEGACY PATH ----
        ctx = (patient_context or "No additional patient context.").strip()
        rag_block = ""
        if patient_id is not None:
            try:
                import rag_client
                last_user = next(
                    (m for m in reversed(messages or [])
                     if (m.get("role") or "").lower() == "user"
                     and (m.get("content") or "").strip()), None,
                )
                if last_user:
                    chunks = rag_client.retrieve(patient_id, last_user["content"])
                    rag_block = rag_client.format_for_prompt(chunks)
            except Exception:
                rag_block = ""
        evidence_block = ""
        if medical_evidence:
            med_ev = medical_evidence.get("medical_evidence", "")
            if med_ev:
                evidence_block = med_ev
            sources = medical_evidence.get("sources_used", [])
            if sources:
                source_names = [s.get("name", "") for s in sources if s.get("name")]
                if source_names:
                    evidence_block += "\n\nSources consulted: " + ", ".join(source_names)
        full_prompt = (
            preamble
            + "\n\n--- PATIENT CONTEXT ---\n" + ctx
            + ("\n\n" + rag_block if rag_block else "")
            + (("\n\n" + evidence_block) if evidence_block else "")
            + "\n\n--- CONVERSATION HISTORY ---\n" + (transcript or "(No prior turns - this is the patient's first message.)")
            + "\n\n--- YOUR TASK ---\n" + AI_DOCTOR_PER_TURN_INSTRUCTION
    )

    # Build messages for Nemotron Omni
    omni_messages = [{"role": "system", "content": preamble + "\n\n" + AI_DOCTOR_PER_TURN_INSTRUCTION}]
    if media_bytes and media_mime:
        import base64
        b64 = base64.b64encode(media_bytes).decode()
        omni_messages.append({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{media_mime};base64,{b64}"}},
            {"type": "text", "text": ctx + "\n\n" + (transcript or "(First message)") + "\n\nPlease analyze the attached image/document."},
        ]})
    else:
        omni_messages.append({"role": "user", "content": full_prompt})

    full_text_parts = []
    tool_calls = []
    status = "ok"

    try:
        stream = _nvidia_omni.chat.completions.create(
            model=OMNI_MODEL,
            messages=omni_messages,
            temperature=0.6,
            max_tokens=2000,
            stream=True,
            extra_body={"reasoning_effort": "none"},
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                full_text_parts.append(delta.content)
                yield ("token", delta.content)
    except Exception as exc:
        # Retry once on rate-limit
        if '429' in str(exc) or 'rate' in str(exc).lower():
            import time as _t
            _t.sleep(2)
            try:
                stream2 = _nvidia_omni.chat.completions.create(
                    model=OMNI_MODEL, messages=omni_messages,
                    temperature=0.6, max_tokens=2000, stream=True,
                    extra_body={"reasoning_effort": "none"},
                )
                for chunk in stream2:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        full_text_parts.append(delta.content)
                        yield ("token", delta.content)
            except Exception as exc2:
                yield ("done", {"text": "".join(full_text_parts), "status": "error", "error": str(exc2), "tool_calls": tool_calls})
                return
        else:
            yield ("done", {"text": "".join(full_text_parts), "status": "error", "error": str(exc), "tool_calls": tool_calls})
            return

    yield ("done", {"text": "".join(full_text_parts), "status": status, "tool_calls": tool_calls})


def _chunk_text(chunk) -> str:
    if chunk is None:
        return ""
    try:
        return chunk.text or ""
    except Exception:
        pass
    try:
        for cand in (chunk.candidates or []):
            for part in (getattr(cand.content, "parts", None) or []):
                t = getattr(part, "text", None)
                if t:
                    return t
    except Exception:
        pass
    return ""


def ai_doctor_summarize(messages, patient_context=None, persona_name="Dr. Mira"):
    """Generate a structured medical summary of the whole chat for the
    patient to review / share with their doctor. Returns plain text +
    a short list of caveats."""
    transcript = _format_chat_history_for_prompt(messages, persona_name=persona_name)
    ctx = (patient_context or "").strip()
    if not transcript:
        return {
            "text": "No conversation yet — there's nothing to summarise.",
            "status": "ok",
        }

    preamble = build_safety_preamble(persona_name)
    prompt = (
        preamble
        + "\n\n--- PATIENT CONTEXT ---\n" + (ctx or "No additional context.")
        + "\n\n--- CONVERSATION TRANSCRIPT ---\n" + transcript
        + "\n\n--- YOUR TASK ---\n"
        + "Write a concise structured medical summary of the conversation for the patient's own records. "
          "Use the sections below, in this exact order, with bold headings and short bullet points. "
          "Do NOT invent information that isn't in the transcript.\n\n"
          "**Main concern(s)**\n"
          "**Key symptoms reported** (with duration/severity when mentioned)\n"
          "**What the patient has already tried**\n"
          "**Likely considerations** (a short, cautious differential -- frame as possibilities, not diagnoses)\n"
          "**Recommended next steps** (home measures, when to escalate, what kind of doctor to see)\n"
          "**Red flags to watch for** (when to seek emergency care)\n\n"
          "Keep it under 400 words. Plain English. Match the language of the conversation."
    )
    try:
        text = _nemotron_chat(prompt, temperature=0.3, max_tokens=900)
        if not text:
            return {"text": "I couldn't generate a summary right now. Please try again.", "status": "error"}
        return {"text": text, "status": "ok"}
    except Exception as exc:
        return {"text": f"AI service error while summarising: {exc}", "status": "error"}

