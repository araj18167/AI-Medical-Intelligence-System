"""Smoke tests for the 2026-09 ML ecosystem additions:

1. POST /api/ml/symptoms/analyze      — structured symptom pipeline
2. GET  /api/ml/patterns/conditions   — condition catalog
3. POST /api/ml/patterns/analyze      — differential pattern analysis
4. GET  /api/ml/nurse/assignments     — prioritized care-task queue
5. GET  /api/ml/nurse/missed          — missed/at-risk task detection
6. GET  /api/ml/network/me            — unified care-network view
7. Auth guards: patient scoping, role restrictions, registry visibility
"""
import requests

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def login(username, password):
    r = requests.post(f"{BASE}/login", data={"username": username, "password": password}, timeout=30)
    assert r.status_code == 200, f"login {username}: {r.status_code} {r.text[:120]}"
    return r.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


print("== logging in ==")
t_patient = login("test.patient.1", "P@ss1234")
t_doctor = login("dr1", "Test@1234")
t_nurse = login("nurse1", "Nurse@1234")

print("== symptom intelligence ==")
r = requests.post(f"{BASE}/api/ml/symptoms/analyze", json={
    "symptoms": "fever, body ache, chills and headache since two days",
    "age": 34, "gender": "male", "duration": "2 days"}, headers=auth(t_patient), timeout=30)
check("symptoms 200", r.status_code == 200, r.text[:150])
d = r.json()
check("symptoms sufficient", d.get("sufficient") is True)
check("symptoms extracted >= 3", len(d.get("extracted_symptoms", [])) >= 3, str(d.get("extracted_symptoms")))
check("symptoms confidence in (0,1]", 0 < d.get("confidence", 0) <= 1)
check("symptoms decision-support", d.get("is_decision_support") is True)
check("symptoms top pattern present", d.get("top_pattern") is not None)

r = requests.post(f"{BASE}/api/ml/symptoms/analyze", json={"symptoms": "random unparseable gibberish zzz"},
                  headers=auth(t_patient), timeout=30)
d = r.json()
check("symptoms insufficient fallback", r.status_code == 200 and d.get("sufficient") is False)
check("symptoms insufficient message", "Insufficient" in d.get("message", ""))

r = requests.post(f"{BASE}/api/ml/symptoms/analyze", json={"symptoms": "chest pain and shortness of breath"},
                  headers=auth(t_patient), timeout=30)
d = r.json()
check("symptoms red-flag risk high", d.get("risk_level") == "high" and len(d.get("red_flags", [])) == 2)

r = requests.post(f"{BASE}/api/ml/symptoms/analyze", json={"symptoms": ""}, headers=auth(t_patient), timeout=30)
check("symptoms empty 400", r.status_code == 400)

print("== pattern analysis ==")
r = requests.get(f"{BASE}/api/ml/patterns/conditions", headers=auth(t_doctor), timeout=30)
check("catalog 200", r.status_code == 200)
check("catalog >= 10 conditions", len(r.json().get("conditions", [])) >= 10)

r = requests.post(f"{BASE}/api/ml/patterns/analyze", json={"symptoms": "fever, cough, sore throat, runny nose"},
                  headers=auth(t_doctor), timeout=30)
d = r.json()
check("patterns sufficient", d.get("sufficient") is True)
check("patterns candidates ranked", len(d.get("candidates", [])) >= 2)
check("patterns evidence attributed", len(d["candidates"][0].get("evidence", [])) >= 1)
check("patterns note", "not a confirmed diagnosis" in d.get("note", ""))

print("== nurse intelligence ==")
r = requests.get(f"{BASE}/api/ml/nurse/assignments", headers=auth(t_nurse), timeout=30)
check("nurse assignments 200", r.status_code == 200, r.text[:150])
d = r.json()
check("nurse items list", isinstance(d.get("items"), list))
for it in d["items"]:
    check("nurse scored 0..100", 0 <= it.get("priority_score", -1) <= 100)
    break
check("nurse sorted desc", all(d["items"][i]["priority_score"] >= d["items"][i + 1]["priority_score"]
                              for i in range(len(d["items"]) - 1)))

r = requests.get(f"{BASE}/api/ml/nurse/missed", headers=auth(t_nurse), timeout=30)
check("nurse missed 200", r.status_code == 200)
d = r.json()
check("nurse missed counts", "missed_count" in d and "at_risk_count" in d)
check("nurse workload summary", "workload" in d and "completion_rate" in d["workload"])

r = requests.get(f"{BASE}/api/ml/nurse/assignments", headers=auth(t_patient), timeout=30)
check("nurse route rejects patient", r.status_code == 403)

print("== care network ==")
r = requests.get(f"{BASE}/api/ml/network/me", headers=auth(t_patient), timeout=30)
check("network patient 200", r.status_code == 200, r.text[:150])
d = r.json()
check("network role patient", d.get("role") == "patient")
check("network summary keys", set(d.get("summary", {})) >= {"doctors", "hospitals", "nurses"})
check("network connections", "connections" in d and "doctors" in d["connections"])

r = requests.get(f"{BASE}/api/ml/network/me", headers=auth(t_doctor), timeout=30)
d = r.json()
check("network doctor 200", r.status_code == 200 and d.get("role") == "doctor")
check("network doctor patients", "patients" in d.get("connections", {}))

r = requests.get(f"{BASE}/api/ml/network/me", headers=auth(t_nurse), timeout=30)
d = r.json()
check("network nurse 200", r.status_code == 200 and d.get("role") == "nurse")

print("== registry visibility ==")
r = requests.get(f"{BASE}/api/ml/models", headers=auth(t_doctor), timeout=30)
check("registry 200", r.status_code == 200)
names = [m.get("name") for m in r.json().get("models", r.json().get("items", []))]
# normalise in case payload shape differs
if not names:
    names = list(r.json().keys())
check("registry has symptom_intelligence", any("symptom_intelligence" in str(n) for n in names), str(names)[:200])
check("registry has care_network_engine", any("care_network_engine" in str(n) for n in names), str(names)[:200])

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)