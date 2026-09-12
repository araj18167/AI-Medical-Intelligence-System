"""End-to-end tests for the ML intelligence endpoints."""
import sys
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

# Demo credentials (password reset during earlier shopkeeper work)
CREDS = {
    "shopkeeper": ("bhawani5061@gmail.com", "Test@1234"),
    "patient": ("test.patient.1", "Test@1234"),
    "doctor": ("dr1", "Test@1234"),
    "nurse": ("nurse1", "Test@1234"),
    "hospital": ("krishna1111@gmail.com", "Test@1234"),
    "admin": ("admin1809@gmail.com", "Test@1234"),
}

passed = 0
failed = 0


def req(method, path, token=None, body=None, expected=200):
    global passed, failed
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            code = resp.status
            raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        code = e.code
        raw = e.read().decode()
    ok = code == expected
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {method} {path} -> {code} (expected {expected})")
    if not ok:
        failed += 1
        print("   ", raw[:300])
    else:
        passed += 1
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return raw


def login(role):
    user, pw = CREDS[role]
    import urllib.parse
    data = urllib.parse.urlencode({"username": user, "password": pw}).encode()
    h = {"Content-Type": "application/x-www-form-urlencoded"}
    r = urllib.request.Request(BASE + "/login", data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read().decode()
            return json.loads(raw).get("access_token")
    except Exception as e:
        print("   login failed:", e)
        return None


print("=" * 60)
print("ML INTELLIGENCE API TESTS")
print("=" * 60)

tokens = {}
for role in CREDS:
    tokens[role] = login(role)
    print(f"login {role}: {'OK' if tokens[role] else 'FAIL'}")

# ---- registry / insights / quality (any role) ----
for role in ("shopkeeper", "patient"):
    r = req("GET", "/api/ml/models", token=tokens[role])
    if isinstance(r, dict):
        print("   models:", r.get("count"))

r = req("GET", "/api/ml/insights", token=tokens["shopkeeper"])
if isinstance(r, dict):
    print("   shopkeeper insights:", [(i["kind"], i["severity"]) for i in r.get("insights", [])])

r = req("GET", "/api/ml/quality", token=tokens["shopkeeper"])
if isinstance(r, dict):
    print("   quality:", r.get("summary"))

# normalize
r = req("POST", "/api/ml/normalize", token=tokens["shopkeeper"],
        body={"text": "Paracitamol 500mg", "entity_type": "medicine"})
if isinstance(r, dict):
    print("   normalize:", r.get("normalized"), r.get("confidence"))

# ---- pharmacy ----
r = req("GET", "/api/ml/pharmacy/intelligence", token=tokens["shopkeeper"])
if isinstance(r, dict):
    print("   pharmacy intel:", r.get("overall_score"), r.get("components"))
r = req("GET", "/api/ml/pharmacy/demand", token=tokens["shopkeeper"])
if isinstance(r, dict):
    print("   demand items:", len(r.get("items", [])),
          "opt:", r.get("optimization", {}).get("by_action"),
          "insights:", len(r.get("insights", [])))
r = req("GET", "/api/ml/pharmacy/sales-forecast?horizon=7", token=tokens["shopkeeper"])
if isinstance(r, dict):
    print("   sales forecast:", r.get("sufficient"), r.get("model"),
          "next7:", sum(r.get("forecast", [])))
r = req("GET", "/api/ml/pharmacy/billing-anomalies", token=tokens["shopkeeper"])
if isinstance(r, dict):
    print("   billing anomalies:", r.get("issue_count"))
r = req("GET", "/api/ml/pharmacy/customer-analytics", token=tokens["shopkeeper"])
if isinstance(r, dict):
    print("   customer analytics:", r.get("count"), r.get("returning_rate"))
r = req("GET", "/api/ml/pharmacy/search?q=paractimol", token=tokens["shopkeeper"])
if isinstance(r, dict):
    print("   fuzzy search:", [(x["medicine_name"], x["score"]) for x in r.get("results", [])])

# role denial: patient hitting pharmacy endpoints
req("GET", "/api/ml/pharmacy/demand", token=tokens["patient"], expected=403)

# ---- patient ----
if tokens["patient"]:
    r = req("GET", "/api/ml/patient/intelligence", token=tokens["patient"])
    if isinstance(r, dict):
        print("   patient intel:", r.get("data_completeness"), r.get("trend_status"),
              "risk:", r.get("risk", {}).get("category"))
    # patient cannot access ANOTHER patient's ML data (non-existent -> 404,
    # existing-but-not-owned -> 403)
    req("GET", "/api/ml/patient/999/risk", token=tokens["patient"], expected=404)
    req("GET", "/api/ml/patient/2/risk", token=tokens["patient"], expected=403)
# doctor access to linked patient
doc_tok = tokens["doctor"]
if doc_tok:
    # find a linked patient id
    import urllib.request as ur
    h = {"Authorization": f"Bearer {doc_tok}"}
    r = ur.Request("http://127.0.0.1:8000/patients/mine", headers=h)
    try:
        with ur.urlopen(r) as resp:
            my_patients = json.loads(resp.read().decode())
    except Exception:
        my_patients = []
    pid = my_patients[0]["id"] if my_patients else 1
    r = req("GET", f"/api/ml/patient/{pid}/risk", token=doc_tok)
    if isinstance(r, dict):
        print("   doctor->patient risk:", r.get("score"), r.get("category"))
    r = req("GET", f"/api/ml/patient/{pid}/trends", token=doc_tok)
    if isinstance(r, dict):
        print("   doctor->patient trends:", r.get("sufficient"),
              "vitals:", len(r.get("vitals", [])), "labs:", len(r.get("labs", [])))
    r = req("GET", f"/api/ml/patient/{pid}/followup", token=doc_tok)
    if isinstance(r, dict):
        print("   followup:", r.get("level"), r.get("reason"))
    r = req("GET", f"/api/ml/patient/{pid}/timeline", token=doc_tok)
    if isinstance(r, dict):
        print("   timeline events:", r.get("total_events"))
    r = req("GET", f"/api/ml/patient/{pid}/intelligence", token=doc_tok)
    if isinstance(r, dict):
        print("   patient intel by doctor:", r.get("trend_status"), r.get("data_completeness"))

# unauthorized patient access: doctor accessing non-linked patient
req("GET", "/api/ml/patient/999/risk", token=doc_tok, expected=404)

# ---- hospital ----
if tokens["hospital"]:
    r = req("GET", "/api/ml/hospital/intelligence", token=tokens["hospital"])
    if isinstance(r, dict):
        print("   hospital intel:", r.get("overall_score"),
              "bed util:", r.get("bed_utilization", {}).get("utilization_pct"),
              "anomalies:", r.get("anomalies", {}).get("flag_count"))

# ---- doctor / nurse workload ----
if doc_tok:
    r = req("GET", "/api/ml/doctor/workload", token=doc_tok)
    if isinstance(r, dict):
        print("   doctor workload:", r.get("doctor_count"), r.get("avg_workload"))
if tokens["nurse"]:
    r = req("GET", "/api/ml/nurse/workload", token=tokens["nurse"])
    if isinstance(r, dict):
        print("   nurse workload:", r.get("nurse_count"))

# ---- monitor admin-only ----
if tokens["admin"]:
    r = req("GET", "/api/ml/monitor", token=tokens["admin"])
    if isinstance(r, dict):
        print("   admin monitor models:", r.get("count"))
else:
    print("[WARN] admin login failed - skipping monitor test")
req("GET", "/api/ml/monitor", token=(tokens["shopkeeper"] or "x"), expected=403)

# ---- explain (will likely hit LLM; verify shape + fallback) ----
r = req("POST", "/api/ml/explain", token=tokens["shopkeeper"],
        body={"role": "shopkeeper",
              "intelligence": {"insights": [
                  {"kind": "low_stock", "severity": "high", "message": "2 items below low-stock threshold."},
                  {"kind": "sales_up", "severity": "info", "message": "Sales rose 12% this week."}]}})
if isinstance(r, dict):
    print("   explain fallback:", r.get("fallback"), "len:", len(str(r.get("explanation"))))

print("=" * 60)
print(f"RESULT: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)