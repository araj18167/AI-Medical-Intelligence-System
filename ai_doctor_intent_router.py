"""
AI Doctor Intent Router (2026-09-02)
=====================================
Classifies user messages into intents and determines which tools/data
are required. This is the CORE fix for the "unnatural AI Doctor" problem.

Instead of loading ALL patient data for EVERY message, we classify the
user's intent FIRST and only retrieve what's actually needed.

Architecture:
    User Message
         ↓
    Intent Router (LLM-based + keyword hybrid)
         ↓
    {intent, requires_patient_data, required_tools[]}
         ↓
    Tool Executor (only calls required tools)
         ↓
    Minimal Context Builder
         ↓
    LLM generates natural response
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import deque

logger = logging.getLogger("ai_doctor_intent")


# ===========================================================================
# Intent Definitions
# ===========================================================================

class Intent:
    NORMAL_CHAT = "NORMAL_CHAT"
    GENERAL_MEDICAL = "GENERAL_MEDICAL"
    PATIENT_HISTORY = "PATIENT_HISTORY"
    REPORT_ANALYSIS = "REPORT_ANALYSIS"
    MEDISCAN = "MEDISCAN"
    SYMPTOM_CHECKER = "SYMPTOM_CHECKER"
    MEDICINE_INFO = "MEDICINE_INFO"
    MEDICINE_INTERACTION = "MEDICINE_INTERACTION"
    PATIENT_SPECIFIC = "PATIENT_SPECIFIC"
    EMERGENCY = "EMERGENCY"
    PRESCRIPTION_ANALYSIS = "PRESCRIPTION_ANALYSIS"
    LAB_REPORT = "LAB_REPORT"
    IMAGING_ANALYSIS = "IMAGING_ANALYSIS"
    CONVERSATION_ANALYSIS = "CONVERSATION_ANALYSIS"
    FOLLOW_UP = "FOLLOW_UP"


# ===========================================================================
# Conversation State Tracker
# ===========================================================================

class ConversationState:
    """Lightweight tracker for conversation context. Stores the last few
    turns and the current topic so the intent router can detect follow-ups
    and context switches without re-classifying from scratch."""

    def __init__(self, max_turns: int = 10):
        self.turns: deque = deque(maxlen=max_turns)
        self.current_topic: Optional[str] = None
        self.last_intent: Optional[str] = None
        self.last_tools_used: list = []
        self.last_patient_data_used: bool = False

    def record_turn(self, user_message: str, intent: str, tools_used: list = None,
                    used_patient_data: bool = False):
        self.turns.append({
            "user": user_message[:200],
            "intent": intent,
            "tools": tools_used or [],
            "patient_data": used_patient_data,
        })
        self.last_intent = intent
        self.last_tools_used = tools_used or []
        self.last_patient_data_used = used_patient_data
        # Update topic based on intent
        topic_map = {
            Intent.PATIENT_HISTORY: "patient_history",
            Intent.REPORT_ANALYSIS: "lab_report",
            Intent.MEDISCAN: "imaging",
            Intent.SYMPTOM_CHECKER: "symptom_check",
            Intent.MEDICINE_INFO: "medicine",
            Intent.MEDICINE_INTERACTION: "medicine",
            Intent.PATIENT_SPECIFIC: "patient_medical",
            Intent.GENERAL_MEDICAL: "medical_knowledge",
        }
        self.current_topic = topic_map.get(intent, self.current_topic)

    def is_follow_up(self, message: str) -> bool:
        """Detect if this message is a follow-up to the previous topic.
        Simple heuristic: short messages with pronouns (it, that, them)
        or references (same, also, too, what about) are likely follow-ups."""
        if not self.turns or not self.current_topic:
            return False
        msg = message.strip().lower()
        # Very short messages are often follow-ups
        if len(msg) < 20:
            return True
        # Pronoun references to previous topic
        follow_up_markers = ["it ", "that ", "them ", "its ", "same ",
                            "also", "too", "what about", "and ", "but ",
                            "how about", "what else"]
        if any(marker in msg for marker in follow_up_markers):
            return True
        return False

    def get_context_summary(self) -> str:
        """Return a brief summary of what's been discussed."""
        if not self.turns:
            return ""
        recent = list(self.turns)[-3:]
        topics = [t["intent"] for t in recent]
        return f"Recent topics: {', '.join(topics)}"


# Global conversation state per session (keyed by session_id)
_conversation_states: dict[int, ConversationState] = {}

def get_conversation_state(session_id: int) -> ConversationState:
    """Get or create the conversation state for a session."""
    if session_id not in _conversation_states:
        _conversation_states[session_id] = ConversationState()
    return _conversation_states[session_id]


@dataclass
class IntentResult:
    intent: str
    requires_patient_data: bool = False
    required_tools: list = field(default_factory=list)
    confidence: float = 1.0
    reasoning: str = ""


# ===========================================================================
# Keyword-based fast path (no LLM call needed for obvious cases)
# ===========================================================================

# Patterns that ALWAYS need patient data
_PATIENT_DATA_PATTERNS = [
    (r"\b(my|me|my\s+)(medical\s+)?history\b", Intent.PATIENT_HISTORY),
    (r"\b(my|me|my\s+)(previous|past|old|last)\s+(report|lab|test|result|blood|urine)", Intent.REPORT_ANALYSIS),
    (r"\b(my|me|my\s+)(x-?ray|ct|mri|scan|imaging|ultrasound)", Intent.MEDISCAN),
    (r"\b(my|me|my\s+)(symptom|symptom\s+checker|assessment)\s*(result|check|say|finding)", Intent.SYMPTOM_CHECKER),
    (r"\b(my|me|my\s+)(current|existing)\s+medic(ine|ation|s)", Intent.MEDICINE_INTERACTION),
    (r"\b(am\s+i|do\s+i)\s+(taking|on)\s+\w+", Intent.MEDICINE_INTERACTION),
    (r"\b(check|show|give|tell)\s+(my|me)\s+(medic|drug|prescription|rx)", Intent.PATIENT_HISTORY),
    (r"\b(what|which)\s+(medicine|drug|tablet|pill)\s+(am\s+i|was\s+i)\s+(taking|prescribed)", Intent.PATIENT_HISTORY),
    (r"\b(compare|trend|change)\s+(my|me)?\s*(report|lab|result)", Intent.REPORT_ANALYSIS),
    (r"\b(based\s+on\s+my|according\s+to\s+my|from\s+my)\s+(report|result|scan|x-?ray|lab)", Intent.PATIENT_SPECIFIC),
    (r"\bmy\s+(hemoglobin|glucose|cholesterol|bp|blood\s+pressure|sugar)\s+(is|was|level|result)", Intent.REPORT_ANALYSIS),
    (r"\b(latest|recent|recent)\s+(blood|lab|report|test|result)\b", Intent.REPORT_ANALYSIS),
]

# Patterns for general medical knowledge (NO patient data needed)
_GENERAL_MEDICAL_PATTERNS = [
    (r"\b(what\s+is|what\s+are|define|explain|tell\s+me\s+about)\s+(fever|headache|cold|flu|asthma|diabetes|hypertension|cancer|infection|allergy|arthritis|pneumonia|migraine|dengue|malaria|covid|typhoid|tuberculosis)", Intent.GENERAL_MEDICAL),
    (r"\b(symptoms?\s+of|signs?\s+of|causes?\s+of|treatment\s+for|prevention\s+of)\b", Intent.GENERAL_MEDICAL),
    (r"\b(how\s+does|how\s+do|what\s+does)\s+(this|that|it|the\s+body|human\s+body)\b", Intent.GENERAL_MEDICAL),
    (r"\b(what\s+is|how\s+does|tell\s+me\s+about)\s+(paracetamol|ibuprofen|amoxicillin|metformin|omeprazole|aspirin|atorvastatin|amlodipine|losartan|azithromycin)\b", Intent.MEDICINE_INFO),
    (r"\b(medicine|drug|tablet|pill|capsule|syrup|drug)\s+(name|info|information|detail|dosage|side\s+effect)\b", Intent.MEDICINE_INFO),
    (r"\b(can\s+i\s+take|is\s+it\s+safe\s+to\s+take|interaction\s+between)\s+\w+\s+(and|with)\s+\w+", Intent.MEDICINE_INTERACTION),
]

# Patterns for medicine info (no patient data needed)
_MEDICINE_INFO_PATTERNS = [
    (r"\b(what\s+is|tell\s+me\s+about|explain|info(?:rmation)?\s+about)\s+\w+\s*(tablet|capsule|syrup|injection|drug|medicine)?", Intent.MEDICINE_INFO),
    (r"\b(side\s+effect|dosage|contraindication|precaution|interaction)\s+(of|for)\s+\w+", Intent.MEDICINE_INFO),
    (r"\b(generic\s+name|brand\s+name|active\s+ingredient)\s+(of|for)\s+\w+", Intent.MEDICINE_INFO),
]

# Emergency patterns
_EMERGENCY_PATTERNS = [
    (r"\b(chest\s+pain|heart\s+attack|stroke|seizure|breathing\s+difficulty|severe\s+bleeding|unconscious|anaphylaxis|overdose|suicid|poisoning)\b", Intent.EMERGENCY),
    (r"\b(emergency|urgent|help\s+me\s+now|call\s+ambulance|dying)\b", Intent.EMERGENCY),
]

# Simple greeting / normal chat patterns (NEVER needs patient data)
_NORMAL_CHAT_PATTERNS = [
    (r"^(hi|hello|hey|good\s+(morning|afternoon|evening)|namaste|howdy|greetings)(\s+(doctor|dr|sir|madam|ma'am))?\s*[!.]?\s*$", Intent.NORMAL_CHAT),
    (r"^(how\s+are\s+you|how\s+are\s+you\s+doing|what'?s?\s+up|hru|r\s+u|how\s+is\s+it\s+going)\s*[?.]?\s*$", Intent.NORMAL_CHAT),
    (r"^(thank|thanks|thank\s+you|thx|ty|ok|okay|alright|got\s+it|understood|bye|goodbye|see\s+ya|see\s+you)\s*[!.]?\s*$", Intent.NORMAL_CHAT),
    (r"^(tell\s+me\s+a\s+joke|make\s+me\s+laugh|something\s+funny|who\s+are\s+you|what\s+are\s+you)\s*[?.]?\s*$", Intent.NORMAL_CHAT),
]


def _keyword_classify(message: str) -> Optional[IntentResult]:
    """Fast keyword-based classification. Returns None if ambiguous."""
    msg = message.strip().lower()
    if not msg:
        return IntentResult(intent=Intent.NORMAL_CHAT, confidence=1.0)

    # Check normal chat FIRST — highest priority for short/greeting messages
    for pattern, intent in _NORMAL_CHAT_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return IntentResult(
                intent=intent,
                requires_patient_data=False,
                confidence=0.99,
                reasoning="Keyword match: normal chat pattern",
            )

    # Check emergency — high priority
    for pattern, intent in _EMERGENCY_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return IntentResult(
                intent=intent,
                requires_patient_data=False,
                confidence=0.95,
                reasoning="Keyword match: emergency pattern",
            )

    # Check patient-data patterns (these need patient data)
    for pattern, intent in _PATIENT_DATA_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return IntentResult(
                intent=intent,
                requires_patient_data=True,
                confidence=0.9,
                reasoning="Keyword match: patient data required",
            )

    # Check general medical patterns (no patient data needed)
    for pattern, intent in _GENERAL_MEDICAL_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return IntentResult(
                intent=intent,
                requires_patient_data=False,
                confidence=0.85,
                reasoning="Keyword match: general medical question",
            )

    # Check medicine info patterns
    for pattern, intent in _MEDICINE_INFO_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return IntentResult(
                intent=intent,
                requires_patient_data=False,
                confidence=0.85,
                reasoning="Keyword match: medicine information",
            )

    # Ambiguous — needs LLM classification
    return None


# ===========================================================================
# LLM-based classification (for ambiguous messages)
# ===========================================================================

_INTENT_CLASSIFICATION_PROMPT = """Classify this patient message into ONE of these intents. Return ONLY a JSON object, nothing else.

INTENTS:
- NORMAL_CHAT: Greetings, small talk, jokes, off-topic, acknowledgments, farewells
- GENERAL_MEDICAL: General medical questions about diseases, symptoms, conditions, treatments (NOT about the patient's own data)
- PATIENT_HISTORY: Patient asks about THEIR OWN medical history, records, past consultations, diagnoses
- REPORT_ANALYSIS: Patient asks about THEIR OWN lab reports, test results, blood work, urine tests
- MEDISCAN: Patient asks about THEIR OWN X-ray, CT, MRI, ultrasound, imaging results
- SYMPTOM_CHECKER: Patient asks about THEIR OWN symptom checker results or assessment
- MEDICINE_INFO: General medicine information (what is X, side effects of Y, dosage of Z) — NOT about patient's own medicines
- MEDICINE_INTERACTION: Patient asks about interactions between medicines, or about THEIR OWN current medicines
- PATIENT_SPECIFIC: Patient asks a question that combines their personal data with medical knowledge
- EMERGENCY: Emergency symptoms, urgent medical situations
- PRESCRIPTION_ANALYSIS: Patient asks about THEIR OWN prescription
- LAB_REPORT: Patient asks about THEIR OWN lab report analysis

RULES:
1. If the message mentions "my", "me", "I", "my report", "my scan", "my medicine", "my history" — it likely needs patient data
2. If the message is a general question ("what is fever?", "how does paracetamol work?") — it does NOT need patient data
3. If the message is a greeting, small talk, or acknowledgment — it's NORMAL_CHAT
4. Short messages like "hi", "hello", "ok", "thanks" are always NORMAL_CHAT
5. "Can I take X with Y?" could be general or patient-specific — check if "my" is used

Return ONLY this JSON:
{"intent": "INTENT_NAME", "requires_patient_data": true/false, "required_tools": ["tool1", "tool2"]}

REQUIRED TOOLS (only when patient data needed):
- get_patient_history: patient history, past records, consultations
- get_latest_report: latest lab report, blood test, urine test
- get_previous_reports: compare reports, old results, trends
- get_mediscan_results: X-ray, CT, MRI, scan results, MediScan results
- get_symptom_checker_results: symptom assessment, symptom checker
- get_current_medicines: current medications, prescriptions
- search_medicine: general medicine info, drug details
- check_interactions: drug interactions, food-drug interactions

Message: "{message}"
"""


def _llm_classify_intent(message: str, llm_func) -> IntentResult:
    """Use the LLM to classify ambiguous messages."""
    prompt = _INTENT_CLASSIFICATION_PROMPT.replace("{message}", message[:500])
    try:
        response = llm_func(prompt, temperature=0.1, max_tokens=300)
        if not response:
            return IntentResult(
                intent=Intent.NORMAL_CHAT,
                requires_patient_data=False,
                confidence=0.5,
                reasoning="LLM classification failed, defaulting to normal chat",
            )

        # Parse JSON from response
        import re as _re
        json_match = _re.search(r'\{[^}]+\}', response)
        if json_match:
            data = json.loads(json_match.group())
            intent = data.get("intent", Intent.NORMAL_CHAT)
            requires = data.get("requires_patient_data", False)
            tools = data.get("required_tools", [])

            # Validate intent
            valid_intents = [getattr(Intent, attr) for attr in dir(Intent) if not attr.startswith("_")]
            if intent not in valid_intents:
                intent = Intent.NORMAL_CHAT
                requires = False

            return IntentResult(
                intent=intent,
                requires_patient_data=requires,
                required_tools=tools,
                confidence=0.8,
                reasoning="LLM classification",
            )
    except Exception as exc:
        logger.debug("LLM intent classification failed: %s", exc)

    return IntentResult(
        intent=Intent.NORMAL_CHAT,
        requires_patient_data=False,
        confidence=0.5,
        reasoning="LLM classification failed, defaulting to normal chat",
    )


# ===========================================================================
# Public API
# ===========================================================================

def classify_intent(message: str, llm_func=None) -> IntentResult:
    """Classify a user message into an intent.

    Uses keyword matching first (fast, no LLM call). Falls back to
    LLM classification for ambiguous messages.

    Args:
        message: The user's message text
        llm_func: Optional LLM function (signature: llm_func(prompt, temperature, max_tokens) -> str)

    Returns:
        IntentResult with intent, requires_patient_data, required_tools
    """
    # Try keyword classification first (fast path)
    result = _keyword_classify(message)
    if result is not None:
        logger.info(
            "Intent classified (keyword): intent=%s, requires_patient_data=%s, tools=%s",
            result.intent, result.requires_patient_data, result.required_tools,
        )
        return result

    # Ambiguous — use LLM if available
    if llm_func:
        result = _llm_classify_intent(message, llm_func)
        logger.info(
            "Intent classified (LLM): intent=%s, requires_patient_data=%s, tools=%s, confidence=%.2f",
            result.intent, result.requires_patient_data, result.required_tools, result.confidence,
        )
        return result

    # No LLM available — default to GENERAL_MEDICAL for ambiguous messages
    # (safer to provide medical info than to guess patient data needs)
    logger.info("Intent defaulted: GENERAL_MEDICAL (no LLM for classification)")
    return IntentResult(
        intent=Intent.GENERAL_MEDICAL,
        requires_patient_data=False,
        confidence=0.5,
        reasoning="No LLM available for classification, defaulting to general medical",
    )


def get_tool_names_for_intent(intent: IntentResult) -> list[str]:
    """Map an IntentResult to the specific tools that should be called."""
    tool_map = {
        Intent.PATIENT_HISTORY: ["get_patient_history"],
        Intent.REPORT_ANALYSIS: ["get_latest_report"],
        Intent.MEDISCAN: ["get_mediscan_results"],
        Intent.SYMPTOM_CHECKER: ["get_symptom_checker_results"],
        Intent.MEDICINE_INTERACTION: ["get_current_medicines", "check_interactions"],
        Intent.PATIENT_SPECIFIC: ["get_patient_context", "get_latest_report", "search_medicine"],
        Intent.PRESCRIPTION_ANALYSIS: ["get_patient_history"],
        Intent.LAB_REPORT: ["get_latest_report"],
        Intent.IMAGING_ANALYSIS: ["get_mediscan_results"],
        Intent.CONVERSATION_ANALYSIS: ["get_patient_history"],
        Intent.MEDICINE_INFO: ["search_medicine"],
        Intent.EMERGENCY: [],  # Don't retrieve data — just respond with emergency guidance
    }
    return tool_map.get(intent.intent, intent.required_tools or [])
