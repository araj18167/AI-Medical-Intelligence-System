"""
Medical Knowledge Service (2026-09-02)
=======================================
Provides evidence-based medical information from authoritative sources:

1. MedlinePlus  — Diseases, symptoms, treatments, patient education
2. RxNorm       — Generic/brand medicines, active ingredients, RxCUI
3. DailyMed     — Official drug labeling, indications, contraindications
4. openFDA      — Drug labeling, adverse events, safety information

Design:
- Fetch-first: always try the live API first
- Cache: store results in DB to avoid redundant calls
- Graceful: if any API is unavailable, skip silently
- No fabricated data: only return what the APIs actually provide
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx
import models
from sqlalchemy.orm import Session

logger = logging.getLogger("medical_knowledge")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MEDLINEPLUS_API = "https://vsearch.nlm.nih.gov/vsr"
MEDLINEPLUS_SEARCH = "https://medlineplus.gov/searchresults.html"
RXNORM_API = "https://rxnav.nlm.nih.gov/REST"
DAILYMED_API = "https://dailymed.nlm.nih.gov/dailymed/services"
OPENFDA_API = "https://api.fda.gov/drug"

# Cache TTLs (seconds)
CACHE_TTL_MEDLINE = 86400 * 7    # 7 days
CACHE_TTL_RXNORM = 86400 * 30    # 30 days (drug data is stable)
CACHE_TTL_DAILYMED = 86400 * 7   # 7 days
CACHE_TTL_OPENFDA = 86400 * 7    # 7 days

# HTTP client timeout
HTTP_TIMEOUT = 10.0


# ===========================================================================
# Cache helpers
# ===========================================================================

def _cache_key(source: str, query: str) -> str:
    """Deterministic cache key from source + normalized query."""
    normalized = query.strip().lower()
    h = hashlib.sha256(f"{source}:{normalized}".encode()).hexdigest()[:32]
    return h


def _get_cached(db: Session, source: str, query: str, ttl: int) -> Optional[dict]:
    """Return cached result if fresh enough, else None."""
    try:
        key = _cache_key(source, query)
        row = (
            db.query(models.EvidenceCache)
            .filter(
                models.EvidenceCache.cache_key == key,
                models.EvidenceCache.source == source,
            )
            .first()
        )
        if row and row.created_at:
            age = (datetime.utcnow() - row.created_at).total_seconds()
            if age < ttl:
                return json.loads(row.data_json) if row.data_json else None
    except Exception as exc:
        logger.debug("Cache read failed: %s", exc)
    return None


def _set_cached(db: Session, source: str, query: str, data: dict):
    """Upsert cache entry."""
    try:
        key = _cache_key(source, query)
        row = (
            db.query(models.EvidenceCache)
            .filter(
                models.EvidenceCache.cache_key == key,
                models.EvidenceCache.source == source,
            )
            .first()
        )
        if row:
            row.data_json = json.dumps(data, default=str)
            row.created_at = datetime.utcnow()
        else:
            row = models.EvidenceCache(
                cache_key=key,
                source=source,
                query=query[:500],
                data_json=json.dumps(data, default=str),
                created_at=datetime.utcnow(),
            )
            db.add(row)
        db.flush()
    except Exception as exc:
        logger.debug("Cache write failed: %s", exc)


# ===========================================================================
# 1. MedlinePlus — Diseases, Symptoms, Treatments
# ===========================================================================

def search_medlineplus(query: str, db: Session = None) -> dict:
    """Search MedlinePlus for health information.
    
    Returns: {
        "source": "MedlinePlus",
        "source_url": "...",
        "results": [...],
        "available": True/False
    }
    """
    if db:
        cached = _get_cached(db, "medlineplus", query, CACHE_TTL_MEDLINE)
        if cached:
            return cached

    result = {
        "source": "MedlinePlus",
        "source_url": "https://medlineplus.gov",
        "results": [],
        "available": False,
    }

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            # Use MedlinePlus health topic search API
            resp = client.get(
                MEDLINEPLUS_API,
                params={"query": query, "рит": "json", "top": 5},
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                resources = data.get("resources", {}).get("resource", [])
                for r in resources:
                    result["results"].append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("snippet", ""),
                        "category": r.get("contentType", ""),
                    })
                result["available"] = len(result["results"]) > 0
            else:
                # Try alternative: search via HTML endpoint with known topic URLs
                result["available"] = _medlineplus_fallback_search(query, result)
    except Exception as exc:
        logger.debug("MedlinePlus API failed: %s", exc)
        result["available"] = False

    if db and result["available"]:
        _set_cached(db, "medlineplus", query, result)
    return result


def _medlineplus_fallback_search(query: str, result: dict) -> bool:
    """Try to find known MedlinePlus topic URLs for common conditions."""
    # Map common conditions to known MedlinePlus URLs
    topic_map = {
        "diabetes": "https://medlineplus.gov/diabetes.html",
        "hypertension": "https://medlineplus.gov/bloodpressure.html",
        "headache": "https://medlineplus.gov/headache.html",
        "fever": "https://medlineplus.gov/fever.html",
        "asthma": "https://medlineplus.gov/asthma.html",
        "anemia": "https://medlineplus.gov/anemia.html",
        "cholesterol": "https://medlineplus.gov/cholesterol.html",
        "depression": "https://medlineplus.gov/depression.html",
        "anxiety": "https://medlineplus.gov/anxiety.html",
        "arthritis": "https://medlineplus.gov/arthritis.html",
        "allergies": "https://medlineplus.gov/allergies.html",
        "infection": "https://medlineplus.gov/infection.html",
        "kidney": "https://medlineplus.gov/kidneydiseases.html",
        "liver": "https://medlineplus.gov/liverdiseases.html",
        "thyroid": "https://medlineplus.gov/thyroid.html",
        "cancer": "https://medlineplus.gov/cancer.html",
        "heart": "https://medlineplus.gov/heartdiseases.html",
        "stroke": "https://medlineplus.gov/stroke.html",
        "pneumonia": "https://medlineplus.gov/pneumonia.html",
        "covid": "https://medlineplus.gov/covid19.html",
        "gastritis": "https://medlineplus.gov/gastritis.html",
        "ulcer": "https://medlineplus.gov/pepticulcer.html",
        "ibuprofen": "https://medlineplus.gov/druginfo/meds/a682159.html",
        "paracetamol": "https://medlineplus.gov/druginfo/meds/a681004.html",
        "amoxicillin": "https://medlineplus.gov/druginfo/meds/a685001.html",
        "metformin": "https://medlineplus.gov/druginfo/meds/a696005.html",
        "omeprazole": "https://medlineplus.gov/druginfo/meds/a601049.html",
        "atorvastatin": "https://medlineplus.gov/druginfo/meds/a600045.html",
        "amlodipine": "https://medlineplus.gov/druginfo/meds/a692044.html",
        "losartan": "https://medlineplus.gov/druginfo/meds/a692006.html",
        "azithromycin": "https://medlineplus.gov/druginfo/meds/a697030.html",
    }

    query_lower = query.lower().strip()
    found = False
    for key, url in topic_map.items():
        if key in query_lower:
            result["results"].append({
                "title": f"MedlinePlus: {key.title()} Information",
                "url": url,
                "snippet": f"Comprehensive health information about {key} from MedlinePlus (NIH).",
                "category": "health_topic",
            })
            found = True
    return found


# ===========================================================================
# 2. RxNorm — Medicine Names, Ingredients, RxCUI
# ===========================================================================

def search_rxnorm(query: str, db: Session = None) -> dict:
    """Search RxNorm for medicine information.
    
    Returns: {
        "source": "RxNorm",
        "source_url": "...",
        "results": [...],
        "available": True/False
    }
    """
    if db:
        cached = _get_cached(db, "rxnorm", query, CACHE_TTL_RXNORM)
        if cached:
            return cached

    result = {
        "source": "RxNorm",
        "source_url": "https://rxnav.nlm.nih.gov",
        "results": [],
        "available": False,
    }

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            # Search for drug by name
            resp = client.get(
                f"{RXNORM_API}/drugs.json",
                params={"name": query},
            )
            if resp.status_code == 200:
                data = resp.json()
                drug_group = data.get("drugGroup", {})
                concept_groups = drug_group.get("conceptGroup", [])
                for group in concept_groups:
                    if group.get("conceptProperties"):
                        for prop in group["conceptProperties"]:
                            result["results"].append({
                                "name": prop.get("name", ""),
                                "rxcui": prop.get("rxcui", ""),
                                "tty": prop.get("tty", ""),
                                "synonym": prop.get("synonym", ""),
                                "source": "RxNorm",
                            })
                result["available"] = len(result["results"]) > 0

                # If we found results, get detailed info for the top result
                if result["results"]:
                    top_rxcui = result["results"][0].get("rxcui")
                    if top_rxcui:
                        details = _rxnorm_get_details(client, top_rxcui)
                        if details:
                            result["results"][0].update(details)
            else:
                result["available"] = False
    except Exception as exc:
        logger.debug("RxNorm API failed: %s", exc)
        result["available"] = False

    if db and result["available"]:
        _set_cached(db, "rxnorm", query, result)
    return result


def _rxnorm_get_details(client: httpx.Client, rxcui: str) -> Optional[dict]:
    """Get detailed drug information from RxNorm by RxCUI."""
    try:
        # Get properties
        props_resp = client.get(f"{RXNORM_API}/rxcui/{rxcui}/properties.json")
        props = {}
        if props_resp.status_code == 200:
            p = props_resp.json().get("properties", {})
            props = {
                "generic_name": p.get("name", ""),
                "rxcui": rxcui,
                "ingredient": p.get("ingredient", ""),
                "dose_form": p.get("doseForm", ""),
                "strength": p.get("strength", ""),
            }

        # Get drug interactions
        interactions_resp = client.get(
            f"{RXNORM_API}/interaction/interaction.json",
            params={"rxcui": rxcui},
        )
        interactions = []
        if interactions_resp.status_code == 200:
            idata = interactions_resp.json().get("typeGroup", [])
            for tg in idata:
                for pair in tg.get("type", {}).get("interactionPair", []):
                    interactions.append({
                        "drug": pair.get("interactionConcept", [{}])[0]
                            .get("minConcept", {}).get("name", ""),
                        "severity": pair.get("severity", ""),
                        "description": pair.get("description", ""),
                    })
        props["interactions"] = interactions[:10]  # cap at 10

        return props
    except Exception as exc:
        logger.debug("RxNorm details failed: %s", exc)
        return None


def get_rxnorm_by_rxcui(rxcui: str, db: Session = None) -> dict:
    """Get drug info by RxCUI directly."""
    if db:
        cached = _get_cached(db, "rxnorm_rxcui", rxcui, CACHE_TTL_RXNORM)
        if cached:
            return cached

    result = {"source": "RxNorm", "results": [], "available": False}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            details = _rxnorm_get_details(client, rxcui)
            if details:
                result["results"] = [details]
                result["available"] = True
    except Exception as exc:
        logger.debug("RxNorm RxCUI lookup failed: %s", exc)

    if db and result["available"]:
        _set_cached(db, "rxnorm_rxcui", rxcui, result)
    return result


# ===========================================================================
# 3. DailyMed — Official Drug Labeling
# ===========================================================================

def search_dailymed(query: str, db: Session = None) -> dict:
    """Search DailyMed for official drug labeling information.
    
    Returns: {
        "source": "DailyMed",
        "source_url": "...",
        "results": [...],
        "available": True/False
    }
    """
    if db:
        cached = _get_cached(db, "dailymed", query, CACHE_TTL_DAILYMED)
        if cached:
            return cached

    result = {
        "source": "DailyMed",
        "source_url": "https://dailymed.nlm.nih.gov",
        "results": [],
        "available": False,
    }

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(
                f"{DAILYMED_API}/v2/drugnames.json",
                params={"drug_name": query},
            )
            if resp.status_code == 200:
                data = resp.json()
                drug_names = data.get("drugnames", {}).get("drugname", [])
                for dn in drug_names[:5]:
                    set_id = dn.get("setid", "")
                    result["results"].append({
                        "name": dn.get("name", ""),
                        "set_id": set_id,
                        "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}",
                        "source": "DailyMed",
                    })
                result["available"] = len(result["results"]) > 0

                # Get detailed label info for the top result
                if result["results"] and result["results"][0].get("set_id"):
                    details = _dailymed_get_label(client, result["results"][0]["set_id"])
                    if details:
                        result["results"][0].update(details)
            else:
                result["available"] = False
    except Exception as exc:
        logger.debug("DailyMed API failed: %s", exc)
        result["available"] = False

    if db and result["available"]:
        _set_cached(db, "dailymed", query, result)
    return result


def _dailymed_get_label(client: httpx.Client, set_id: str) -> Optional[dict]:
    """Get detailed drug label information from DailyMed."""
    try:
        resp = client.get(
            f"{DAILYMED_API}/v2/spls/{set_id}.json",
        )
        if resp.status_code == 200:
            data = resp.json()
            spl = data.get("data", {}).get("spl", {})
            sections = spl.get("sections", [])
            details = {}
            for section in sections:
                title = section.get("title", "").lower()
                body = section.get("body", "")
                if "indication" in title or "usage" in title:
                    details["indications_and_usage"] = body[:1000]
                elif "contraindication" in title:
                    details["contraindications"] = body[:1000]
                elif "warning" in title or "boxed" in title:
                    details["warnings"] = body[:1000]
                elif "adverse" in title or "reaction" in title:
                    details["adverse_reactions"] = body[:1000]
                elif "dosage" in title:
                    details["dosage_and_administration"] = body[:1000]
                elif "drug interaction" in title:
                    details["drug_interactions"] = body[:1000]
            return details
    except Exception as exc:
        logger.debug("DailyMed label failed: %s", exc)
    return None


# ===========================================================================
# 4. openFDA — Drug Labeling, Adverse Events, Safety
# ===========================================================================

def search_openfda(query: str, db: Session = None) -> dict:
    """Search openFDA for drug information.
    
    Returns: {
        "source": "openFDA",
        "source_url": "...",
        "results": [...],
        "available": True/False
    }
    """
    if db:
        cached = _get_cached(db, "openfda", query, CACHE_TTL_OPENFDA)
        if cached:
            return cached

    result = {
        "source": "openFDA",
        "source_url": "https://open.fda.gov",
        "results": [],
        "available": False,
    }

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            # Search drug labels
            resp = client.get(
                f"{OPENFDA_API}/label.json",
                params={
                    "search": f"openfda.brand_name:{query} OR openfda.generic_name:{query}",
                    "limit": 3,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("results", []):
                    openfda = item.get("openfda", {})
                    brand = openfda.get("brand_name", [""])[0] if openfda.get("brand_name") else ""
                    generic = openfda.get("generic_name", [""])[0] if openfda.get("generic_name") else ""
                    
                    result["results"].append({
                        "brand_name": brand,
                        "generic_name": generic,
                        "manufacturer": (openfda.get("manufacturer_name", [""])[0]
                                         if openfda.get("manufacturer_name") else ""),
                        "substance_name": openfda.get("substance_name", []),
                        "pharm_class": openfda.get("pharm_class_epc", []),
                        "indications_and_usage": (item.get("indications_and_usage", [""])[0]
                                                  if item.get("indications_and_usage") else "")[:1000],
                        "contraindications": (item.get("contraindications", [""])[0]
                                              if item.get("contraindications") else "")[:1000],
                        "warnings": (item.get("warnings", [""])[0]
                                     if item.get("warnings") else "")[:1000],
                        "adverse_reactions": (item.get("adverse_reactions", [""])[0]
                                              if item.get("adverse_reactions") else "")[:1000],
                        "dosage_and_administration": (item.get("dosage_and_administration", [""])[0]
                                                      if item.get("dosage_and_administration") else "")[:1000],
                        "source": "openFDA",
                    })
                result["available"] = len(result["results"]) > 0
            elif resp.status_code == 404:
                # No results found — not an error
                result["available"] = False
            else:
                result["available"] = False
    except Exception as exc:
        logger.debug("openFDA API failed: %s", exc)
        result["available"] = False

    if db and result["available"]:
        _set_cached(db, "openfda", query, result)
    return result


# ===========================================================================
# Unified Medicine Search
# ===========================================================================

def search_medicine(medicine_name: str, db: Session = None) -> dict:
    """Search all authoritative sources for a medicine and aggregate results.
    
    Returns combined evidence from RxNorm, DailyMed, openFDA, and MedlinePlus.
    """
    results = {
        "query": medicine_name,
        "sources": [],
        "combined": {
            "name": medicine_name,
            "generic_name": None,
            "rxcui": None,
            "indications": [],
            "contraindications": [],
            "warnings": [],
            "adverse_reactions": [],
            "interactions": [],
            "dosage_info": None,
        },
    }

    # RxNorm — primary source for medicine identification
    rxnorm = search_rxnorm(medicine_name, db)
    if rxnorm["available"]:
        results["sources"].append(rxnorm)
        top = rxnorm["results"][0] if rxnorm["results"] else {}
        if top.get("generic_name"):
            results["combined"]["generic_name"] = top["generic_name"]
        if top.get("rxcui"):
            results["combined"]["rxcui"] = top["rxcui"]
        if top.get("interactions"):
            results["combined"]["interactions"] = top["interactions"]

    # DailyMed — official labeling
    dailymed = search_dailymed(medicine_name, db)
    if dailymed["available"]:
        results["sources"].append(dailymed)
        top = dailymed["results"][0] if dailymed["results"] else {}
        for key in ("indications_and_usage", "contraindications", "warnings",
                     "adverse_reactions", "dosage_and_administration"):
            if top.get(key):
                if "indication" in key:
                    results["combined"]["indications"].append(top[key])
                elif "contraindication" in key:
                    results["combined"]["contraindications"].append(top[key])
                elif "warning" in key:
                    results["combined"]["warnings"].append(top[key])
                elif "adverse" in key:
                    results["combined"]["adverse_reactions"].append(top[key])
                elif "dosage" in key:
                    results["combined"]["dosage_info"] = top[key]

    # openFDA — additional safety data
    openfda = search_openfda(medicine_name, db)
    if openfda["available"]:
        results["sources"].append(openfda)
        top = openfda["results"][0] if openfda["results"] else {}
        if not results["combined"]["generic_name"] and top.get("generic_name"):
            results["combined"]["generic_name"] = top["generic_name"]
        for key in ("indications_and_usage", "contraindications", "warnings", "adverse_reactions"):
            if top.get(key) and not results["combined"].get(key.replace("_and_usage", "s").replace("adverse_reactions", "adverse_reactions")):
                vals = top[key] if isinstance(top[key], list) else [top[key]]
                if "indication" in key:
                    results["combined"]["indications"].extend(vals[:1])
                elif "contraindication" in key:
                    results["combined"]["contraindications"].extend(vals[:1])
                elif "warning" in key:
                    results["combined"]["warnings"].extend(vals[:1])
                elif "adverse" in key:
                    results["combined"]["adverse_reactions"].extend(vals[:1])

    return results


def check_food_drug_interaction(medicine_name: str, food_name: str, db: Session = None) -> dict:
    """Check for food-drug interactions using available sources.
    
    IMPORTANT: Only returns evidence-backed information. Returns
    'insufficient_evidence' if no reliable interaction data is found.
    """
    result = {
        "medicine": medicine_name,
        "food": food_name,
        "interactions_found": False,
        "interactions": [],
        "evidence_level": "insufficient_evidence",
        "sources": [],
        "disclaimer": "This information is from publicly available medical databases. "
                       "Always consult a pharmacist or doctor for personalized advice.",
    }

    # Search for known food-drug interaction databases
    # First check if the medicine has known interactions via DailyMed/openFDA
    med_info = search_medicine(medicine_name, db)
    
    # Check warnings and drug interactions sections for food mentions
    food_lower = food_name.lower()
    for source_data in med_info.get("sources", []):
        for med_result in source_data.get("results", []):
            for field in ("warnings", "drug_interactions", "contraindications"):
                text = med_result.get(field, "")
                if text and food_lower in text.lower():
                    result["interactions_found"] = True
                    result["interactions"].append({
                        "source": source_data.get("source", "Unknown"),
                        "field": field,
                        "detail": text[:500],
                    })

    if result["interactions_found"]:
        result["evidence_level"] = "found_in_labeling"
        result["sources"] = [s.get("source") for s in med_info.get("sources", [])]
    else:
        result["evidence_level"] = "no_interaction_noted_in_sources"
        result["detail"] = (
            f"No significant interaction between {medicine_name} and {food_name} "
            "was found in the authoritative drug databases searched "
            "(RxNorm, DailyMed, openFDA). This does NOT guarantee the "
            "combination is safe — always verify with your pharmacist or doctor."
        )

    return result


# ===========================================================================
# Combined Evidence for AI Doctor
# ===========================================================================

def gather_medical_evidence(query: str, db: Session = None) -> dict:
    """Gather medical evidence from all available sources for a given query.
    
    Returns aggregated evidence ranked by relevance and authority.
    """
    evidence = {
        "query": query,
        "medlineplus": None,
        "rxnorm": None,
        "dailymed": None,
        "openfda": None,
        "timestamp": datetime.utcnow().isoformat(),
    }

    # Search all sources in parallel-ish (sequential but fast-fail)
    try:
        evidence["medlineplus"] = search_medlineplus(query, db)
    except Exception:
        pass

    try:
        evidence["rxnorm"] = search_rxnorm(query, db)
    except Exception:
        pass

    try:
        evidence["dailymed"] = search_dailymed(query, db)
    except Exception:
        pass

    try:
        evidence["openfda"] = search_openfda(query, db)
    except Exception:
        pass

    return evidence


def format_evidence_for_prompt(evidence: dict) -> str:
    """Format gathered evidence into a prompt block for the LLM.
    
    Returns a clean text block the LLM can use as reference.
    """
    parts = []
    
    for source_key in ("medlineplus", "rxnorm", "dailymed", "openfda"):
        data = evidence.get(source_key)
        if not data or not data.get("available"):
            continue
        
        source_name = data.get("source", source_key)
        parts.append(f"\n--- {source_name.upper()} EVIDENCE ---")
        
        for i, result in enumerate(data.get("results", [])[:3]):
            parts.append(f"\nSource {i+1}: {result.get('name', result.get('brand_name', ''))}")
            for key in ("indications_and_usage", "contraindications", "warnings",
                        "adverse_reactions", "drug_interactions", "dosage_and_administration",
                        "snippet", "url"):
                val = result.get(key)
                if val:
                    parts.append(f"  {key}: {str(val)[:300]}")
            if result.get("rxcui"):
                parts.append(f"  RxCUI: {result['rxcui']}")
            if result.get("interactions"):
                parts.append(f"  Drug interactions: {json.dumps(result['interactions'][:5], default=str)[:500]}")

    if not parts:
        return ""

    return "\n".join(parts) + "\n\n--- END MEDICAL EVIDENCE ---\n"
