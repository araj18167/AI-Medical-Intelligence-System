"""End-to-end API tests for:
1. Patient ID → Name lookup (CHANGE 1)
2. AI Report history auto-save + listing (CHANGE 2)
"""
import requests
import sqlite3
import json
import time

BASE = "http://127.0.0.1:8000"
passed = 0
failed = 0

def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {extra}")

def login(username, password):
    r = requests.post(f"{BASE}/login", data={"username": username, "password": password})
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text[:200]}"
    return r.json()["access_token"]

def auth(token):
    return {"Authorization": f"Bearer {token}"}

# ---- Credentials ----
PAT = login("test.patient.1", "P@ss1234")       # patient user 3, patient row 1
DR = login("dr1", "Test@1234")                   # doctor user 2
HOSP = login("bhawani1234@gmail.com", "Hosp@1234")  # hospital user 13
HP = auth(PAT)
HD = auth(DR)
HH = auth(HOSP)

# ---- CHANGE 1: Patient ID → Name lookup ----
print("\n=== CHANGE 1: Patient ID -> Name Lookup ===")

print("\n[1] Doctor looks up valid patient")
r = requests.get(f"{BASE}/patients/1/basic-info", headers=HD)
check("doctor lookup 200", r.status_code == 200, r.text[:200])
info = r.json()
check("patient_id=1", info.get("patient_id") == 1)
check("name present", bool(info.get("name")))
check("no medical records exposed", "diagnosis" not in info and "symptoms" not in info)

print("\n[2] Hospital looks up valid patient")
r = requests.get(f"{BASE}/patients/1/basic-info", headers=HH)
check("hospital lookup 200", r.status_code == 200, r.text[:200])
info2 = r.json()
check("hospital gets same patient name", info2.get("name") == info.get("name"))

print("\n[3] Invalid patient ID")
r = requests.get(f"{BASE}/patients/99999/basic-info", headers=HD)
check("invalid ID returns 404", r.status_code == 404, r.text[:120])

print("\n[4] Patient cannot use lookup (unauthorized role)")
r = requests.get(f"{BASE}/patients/1/basic-info", headers=HP)
check("patient role blocked", r.status_code == 403, r.text[:120])

print("\n[5] Unauthenticated access blocked")
r = requests.get(f"{BASE}/patients/1/basic-info")
check("no-token blocked", r.status_code == 401, r.text[:120])

# ---- CHANGE 2: AI Report history ----
print("\n=== CHANGE 2: AI Report History ===")

print("\n[6] Patient has no reports initially")
r = requests.get(f"{BASE}/ai-reports", headers=HP)
check("list reports 200", r.status_code == 200, r.text[:200])
data = r.json()
check("initially empty", data.get("total", 0) == 0)

print("\n[7] Unauthenticated cannot list reports")
r = requests.get(f"{BASE}/ai-reports")
check("no-token blocked", r.status_code == 401, r.text[:120])

print("\n[8] Doctor cannot list patient reports")
r = requests.get(f"{BASE}/ai-reports", headers=HD)
check("doctor blocked", r.status_code == 403, r.text[:120])

print("\n[9] Patient uses Symptom Checker → report auto-saved")
r = requests.post(f"{BASE}/predict-disease", headers=HP, json={
    "symptoms": "headache, fever, fatigue",
    "age": 30,
    "gender": "male"
})
check("predict-disease 200", r.status_code == 200, r.text[:200])
# Check report was auto-saved
r2 = requests.get(f"{BASE}/ai-reports", headers=HP)
data2 = r2.json()
check("report auto-saved", data2.get("total", 0) >= 1, data2)
symptom_reports = [i for i in data2.get("items", []) if i["feature_type"] == "symptom_checker"]
check("symptom_checker report exists", len(symptom_reports) >= 1)
if symptom_reports:
    rpt = symptom_reports[0]
    check("report has title", bool(rpt.get("title")))
    check("report has findings", bool(rpt.get("findings")))
    check("report status=completed", rpt.get("status") == "completed")
    check("report ai_model present", bool(rpt.get("ai_model")))

print("\n[10] Second symptom check creates NEW report (not overwrite)")
r = requests.post(f"{BASE}/predict-disease", headers=HP, json={
    "symptoms": "chest pain, shortness of breath",
    "age": 30,
    "gender": "male"
})
check("second predict-disease 200", r.status_code == 200)
r2 = requests.get(f"{BASE}/ai-reports", headers=HP)
data3 = r2.json()
check("total >= 2 after second check", data3.get("total", 0) >= 2)
symptom_reports2 = [i for i in data3.get("items", []) if i["feature_type"] == "symptom_checker"]
check("2 symptom_checker reports (no overwrite)", len(symptom_reports2) >= 2)

print("\n[11] Search works")
r = requests.get(f"{BASE}/ai-reports?q=headache", headers=HP)
data4 = r.json()
check("search by keyword", data4.get("total", 0) >= 1)

print("\n[12] Feature filter works")
r = requests.get(f"{BASE}/ai-reports?feature_type=symptom_checker", headers=HP)
data5 = r.json()
check("filter by symptom_checker", all(i["feature_type"] == "symptom_checker" for i in data5.get("items", [])))

r = requests.get(f"{BASE}/ai-reports?feature_type=mediscan", headers=HP)
data6 = r.json()
check("filter mediscan returns 0", data6.get("total", 0) == 0)

print("\n[13] Get single report by ID")
if data3.get("items"):
    rid = data3["items"][0]["id"]
    r = requests.get(f"{BASE}/ai-reports/{rid}", headers=HP)
    check("get single report 200", r.status_code == 200)
    check("report id matches", r.json().get("id") == rid)

print("\n[14] Cannot access other patient's report")
# Create temp patient
con = sqlite3.connect("medical_system.db")
cur = con.cursor()
import auth as authmod
suffix = str(int(time.time()))
cur.execute("INSERT INTO patients (name, age, gender, created_at) VALUES ('Other Patient', 25, 'Male', datetime('now'))")
other_pid = cur.lastrowid
cur.execute("INSERT INTO users (username, email, full_name, hashed_password, role, patient_id, created_at) VALUES (?,?,?,?,'patient',?,datetime('now'))",
            (f"other.rpt.{suffix}", f"other{suffix}@x.com", "Other Patient", authmod.hash_password("Other@1234"), other_pid))
other_uid = cur.lastrowid
con.commit()
con.close()
OTHER_TOKEN = auth(login(f"other.rpt.{suffix}", "Other@1234"))
# Other patient tries to access patient 1's report
if data3.get("items"):
    rid = data3["items"][0]["id"]
    r = requests.get(f"{BASE}/ai-reports/{rid}", headers=OTHER_TOKEN)
    check("cross-patient access blocked", r.status_code == 404, r.text[:120])

print("\n[15] Summary endpoint works")
r = requests.get(f"{BASE}/ai-reports/summary", headers=HP)
s = r.json()
check("summary has total", "total" in s)
check("summary has by_feature", "by_feature" in s)
check("symptom_checker count >= 2", s.get("by_feature", {}).get("symptom_checker", 0) >= 2)

# Cleanup
print("\n[cleanup] removing test data")
con = sqlite3.connect("medical_system.db")
cur = con.cursor()
cur.execute("DELETE FROM ai_reports WHERE patient_id=1")
cur.execute("DELETE FROM medical_records WHERE patient_id=1 AND notes='Auto-generated by AI prediction'")
cur.execute("DELETE FROM users WHERE id=?", (other_uid,))
cur.execute("DELETE FROM patients WHERE id=?", (other_pid,))
con.commit()
con.close()
print("  cleaned: ai_reports, test medical_records, temp user/patient")

print(f"\n===== RESULT: {passed} passed, {failed} failed =====")
raise SystemExit(1 if failed else 0)
