"""End-to-end API tests for the 2026-09 enhancements:
1. Nurse care-task assignments (My Assignments) — doctor & hospital create,
   nurse workflow (ack/start/progress/note/followup/escalate/complete), history.
2. Role-based settings (storage, validation, audit, notification gating).
3. Live activity feed + heatmap (real events, role-scoped).
"""
import requests
import sqlite3

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
    r = requests.post(BASE + "/login", data={"username": username, "password": password})
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text[:200]}"
    return r.json()["access_token"]

def auth(token):
    return {"Authorization": f"Bearer {token}"}

DR = login("dr1", "Test@1234")              # doctor user 2, linked patients 1, 7
NS = login("nurse1", "Nurse@1234")          # nurse user 14
NS16 = login("nurse2smoke1786942367", "Nurse@1234")  # nurse user 16 (caring for patient 1)
HO = login("bhawani1234@gmail.com", "Hosp@1234")     # hospital user 13 -> hospital 3
PAT = login("test.patient.1", "P@ss1234")   # patient user 3, patient row 1
HD = auth(DR)
HN = auth(NS)
HN16 = auth(NS16)
HH = auth(HO)
HP = auth(PAT)

cleanup_task_ids = []

# ================= 1. Doctor creates task =================
print("\n[1] Doctor creates a care task for nurse1 (patient 1)")
r = requests.get(BASE + "/nurse-tasks/options", headers=HD)
opt = r.json()
check("doctor options has linked patient 1", r.status_code == 200 and any(p["id"] == 1 for p in opt["patients"]))
check("doctor options has nurses", r.status_code == 200 and any(n["id"] == 14 for n in opt["nurses"]))
r = requests.post(BASE + "/nurse-tasks/create", headers=HD, json={
    "patient_id": 1, "nurse_user_id": 14, "title": "Morning vitals round",
    "instructions": "Record vitals and log any abnormal readings.",
    "priority": "high", "due_at": None})
check("create task 200", r.status_code == 200, r.text[:160])
t1 = r.json()
tid1 = t1["id"]
cleanup_task_ids.append(tid1)
check("task patient/patient_name", t1["patient_id"] == 1 and "patient_name" in t1)
check("task creator role doctor", t1["created_by_role"] == "doctor")
check("created event recorded", any(e["kind"] == "created" for e in t1["events"]))
# Notification should have been created for nurse
con = sqlite3.connect("medical_system.db")
n_notif = con.execute("SELECT COUNT(*) FROM notification_events WHERE kind='task_assigned_urgent' AND recipient_user_id=14 AND created_at >= datetime('now','-5 minutes')").fetchone()[0]
con.close()
check("urgent assignment notification fired", n_notif >= 1, str(n_notif))

# Wrong patient (not linked to dr1)
r = requests.post(BASE + "/nurse-tasks/create", headers=HD, json={
    "patient_id": 999999, "nurse_user_id": 14, "title": "X"})
check("unknown patient 404", r.status_code == 404)
r = requests.post(BASE + "/nurse-tasks/create", headers=HD, json={
    "patient_id": 3, "nurse_user_id": 14, "title": "X"})
check("unlinked patient forbidden 403", r.status_code == 403)

# ================= 2. Hospital creates task =================
print("\n[2] Hospital creates a care task (nurse16 caring for patient1 at hospital 3)")
r = requests.get(BASE + "/nurse-tasks/options", headers=HH)
opt2 = r.json()
check("hospital options includes admitted patient 1", r.status_code == 200 and any(p["id"] == 1 for p in opt2["patients"]))
check("hospital options includes caring nurse 16", any(n["id"] == 16 for n in opt2["nurses"]))
r = requests.post(BASE + "/nurse-tasks/create", headers=HH, json={
    "patient_id": 1, "nurse_user_id": 16, "title": "Wound dressing follow-up",
    "instructions": "Change dressing every 4h.", "priority": "medium", "due_at": None})
check("hospital create 200", r.status_code == 200, r.text[:160])
t2 = r.json()
tid2 = t2["id"]
cleanup_task_ids.append(tid2)
check("task created_by_role hospital", t2["created_by_role"] == "hospital")
check("hospital id scoped", t2["hospital_id"] == 3)
# Hospital cannot assign to a nurse not caring there
r = requests.post(BASE + "/nurse-tasks/create", headers=HH, json={
    "patient_id": 1, "nurse_user_id": 14, "title": "X"})
check("cross-org nurse forbidden 403", r.status_code == 403)

# ================= 3. Nurse workflow on t1 =================
print("\n[3] Nurse1 workflow: ack -> start -> progress -> note -> followup -> complete")
r = requests.get(BASE + "/nurse-tasks/mine", headers=HN, params={"filter": "all"})
mine = r.json()
check("nurse lists task", r.status_code == 200 and any(t["id"] == tid1 for t in mine["items"]))
r = requests.get(BASE + "/nurse-tasks/mine/stats", headers=HN)
st = r.json()
check("stats include pending/high", st["pending"] >= 1 and st["high_priority"] >= 1, str(st))
r = requests.get(BASE + "/nurse-tasks/mine", headers=HN, params={"filter": "high"})
check("high filter returns t1", any(t["id"] == tid1 for t in r.json()["items"]))

r = requests.post(BASE + f"/nurse-tasks/{tid1}/acknowledge", headers=HN)
check("acknowledge", r.status_code == 200 and r.json()["status"] == "acknowledged")
r = requests.post(BASE + f"/nurse-tasks/{tid1}/start", headers=HN)
check("start", r.status_code == 200 and r.json()["status"] == "in_progress")
r = requests.post(BASE + f"/nurse-tasks/{tid1}/progress", headers=HN, json={"progress": 40, "note": "BP stable"})
check("progress 40", r.status_code == 200 and r.json()["progress"] == 40)
r = requests.post(BASE + f"/nurse-tasks/{tid1}/notes", headers=HN, json={"note": "Patient comfortable", "category": "observation"})
check("note appended 200", r.status_code == 200)
rg = requests.get(BASE + f"/nurse-tasks/{tid1}", headers=HN).json()
check("note event recorded", any(e["kind"] == "note" and "Patient comfortable" in (e["note"] or "") for e in rg["events"]))
r = requests.post(BASE + f"/nurse-tasks/{tid1}/followup", headers=HN, json={"due_at": "2026-12-01T09:00:00", "reason": "Recheck vitals"})
check("followup scheduled", r.status_code == 200 and r.json().get("next_followup") is not None)
r = requests.post(BASE + f"/nurse-tasks/{tid1}/complete", headers=HN, json={"note": "All vitals normal"})
check("complete", r.status_code == 200 and r.json()["status"] == "completed" and r.json()["progress"] == 100)
r = requests.get(BASE + f"/nurse-tasks/{tid1}", headers=HN)
d1 = r.json()
kinds = [e["kind"] for e in d1["events"]]
check("history order created..completed", kinds == ["created", "acknowledged", "started", "progress", "note", "followup_scheduled", "completed"], str(kinds))
r = requests.get(BASE + "/nurse-tasks/mine/stats", headers=HN)
st2 = r.json()
check("completed_today >= 1", st2["completed_today"] >= 1, str(st2))

# ================= 4. Escalation =================
print("\n[4] Escalation workflow (nurse -> doctor)")
r = requests.post(BASE + "/nurse-tasks/create", headers=HD, json={
    "patient_id": 7, "nurse_user_id": 14, "title": "Escalation test task",
    "instructions": "", "priority": "low", "due_at": None})
t3 = r.json()
tid3 = t3["id"]
cleanup_task_ids.append(tid3)
r = requests.post(BASE + f"/nurse-tasks/{tid3}/escalate", headers=HN, json={"reason": "Patient needs review", "to_role": "doctor"})
check("escalate", r.status_code == 200 and r.json()["status"] == "escalated", r.text[:160])
r = requests.post(BASE + f"/nurse-tasks/{tid3}/escalate", headers=HN16)
check("other nurse cannot escalate -> 403", r.status_code == 403, f"got {r.status_code} {r.text[:140]}")
r = requests.post(BASE + f"/nurse-tasks/{tid3}/respond-escalation", headers=HD, json={"note": "Reviewed — continue care"})
check("doctor responds", r.status_code == 200 and r.json()["status"] == "in_progress")
r = requests.post(BASE + f"/nurse-tasks/{tid3}/cancel", headers=HD, json={"reason": "No longer needed"})
check("doctor cancels task", r.status_code == 200 and r.json()["status"] == "cancelled")
r = requests.post(BASE + f"/nurse-tasks/{tid3}/complete", headers=HN)
check("cannot complete cancelled", r.status_code == 400, f"got {r.status_code} {r.text[:140]}")

# ================= 5. Authorization =================
print("\n[5] Authorization")
r = requests.post(BASE + "/nurse-tasks/create", headers=HN, json={"patient_id": 1, "nurse_user_id": 14, "title": "X"})
check("nurse cannot create -> 403", r.status_code == 403)
r = requests.post(BASE + "/nurse-tasks/create", headers=HP, json={"patient_id": 1, "nurse_user_id": 14, "title": "X"})
check("patient cannot create -> 403", r.status_code == 403)
r = requests.get(BASE + "/nurse-tasks/mine", headers=HD)
check("doctor cannot list nurse mine -> 403", r.status_code == 403)
r = requests.get(BASE + "/nurse-tasks/created", headers=HD)
check("doctor lists created", r.status_code == 200 and any(t["id"] in (tid1, tid3) for t in r.json()["items"]))
r = requests.get(BASE + "/nurse-tasks/created", headers=HH)
check("hospital lists created", r.status_code == 200 and any(t["id"] == tid2 for t in r.json()["items"]))
r = requests.get(BASE + "/nurse-tasks/999999", headers=HN)
check("unknown task 404", r.status_code == 404)

# ================= 6. Settings + audit + notification gating =================
print("\n[6] Role-based settings")
r = requests.get(BASE + "/settings", headers=HN)
s = r.json()
check("nurse settings groups", r.status_code == 200 and s["has_settings"] and s["role"] == "nurse", str(s)[:200])
keys = [x["key"] for g in s["groups"] for x in g["settings"]]
check("nurse settings include assignment_notifications", "assignment_notifications" in keys)
r = requests.get(BASE + "/settings", headers=HD)
check("doctor settings has link_request_notifications",
      any(x["key"] == "link_request_notifications" for g in r.json()["groups"] for x in g["settings"]))
r = requests.get(BASE + "/settings", headers=HP)
check("patient has_settings true", r.json()["has_settings"])
r = requests.put(BASE + "/settings", headers=HN, json={"updates": {"assignment_notifications": False}})
check("PUT valid setting", r.status_code == 200 and r.json()["settings"]["assignment_notifications"] is False)
r = requests.put(BASE + "/settings", headers=HN, json={"updates": {"not_a_real_setting": True}})
check("PUT unknown key rejected 400", r.status_code == 400)
r = requests.put(BASE + "/settings", headers=HN, json={"updates": {"assignment_notifications": "yes"}})
check("PUT wrong type rejected 400", r.status_code == 400)
r = requests.get(BASE + "/settings/audit", headers=HN)
check("audit trail written", r.status_code == 200 and len(r.json()["items"]) >= 1)
# Gating: with assignment notifications OFF a new normal task must NOT notify.
def _count_notifs():
    con = sqlite3.connect("medical_system.db")
    n = con.execute("SELECT COUNT(*) FROM notification_events WHERE kind IN ('task_assigned','task_assigned_urgent') AND recipient_user_id=14 AND created_at >= datetime('now','-3 minutes')").fetchone()[0]
    con.close()
    return n
before = _count_notifs()
r = requests.post(BASE + "/nurse-tasks/create", headers=HD, json={
    "patient_id": 7, "nurse_user_id": 14, "title": "Silent test task", "priority": "low"})
t4 = r.json()
cleanup_task_ids.append(t4["id"])
after = _count_notifs()
check("no notification when assignment_notifications off", after == before, f"before={before} after={after}")
r = requests.put(BASE + "/settings", headers=HN, json={"updates": {"assignment_notifications": True}})
check("PUT restore on", r.status_code == 200)

# ================= 7. Activity feed + heatmap =================
print("\n[7] Live activity feed + heatmap")
r = requests.get(BASE + "/api/activity", headers=HD, params={"limit": 50})
feed = r.json()
types = [i["event_type"] for i in feed["items"]]
check("doctor feed has task_assigned", r.status_code == 200 and "task_assigned" in types, str(types))
check("feed items carry detail+time", all(i["detail"] and i["created_at"] for i in feed["items"]))
r = requests.get(BASE + "/api/activity/heatmap", headers=HD, params={"days": 56})
hm = r.json()
check("heatmap 56 days", len(hm["days"]) == 56)
check("heatmap total > 0", hm["total"] > 0, str(hm["total"]))
r = requests.get(BASE + "/api/activity", headers=HP)
ptypes = [i["event_type"] for i in r.json()["items"]]
check("patient sees own/self activity only", all(i["patient_id"] == 1 or i.get("actor_name") for i in r.json()["items"]))
check("patient feed non-empty", len(r.json()["items"]) > 0, str(ptypes))
r = requests.get(BASE + "/api/activity", headers=HN)
check("nurse sees feed", r.status_code == 200 and len(r.json()["items"]) > 0)
r = requests.get(BASE + "/api/activity", headers=HH)
check("hospital sees feed", r.status_code == 200 and len(r.json()["items"]) > 0)

# ================= 8. Cleanup created test tasks =================
print("\n[8] Cleanup")
con = sqlite3.connect("medical_system.db")
for t in cleanup_task_ids:
    con.execute("DELETE FROM nurse_task_events WHERE task_id=?", (t,))
    con.execute("DELETE FROM nurse_tasks WHERE id=?", (t,))
con.execute("DELETE FROM system_activities WHERE entity_type='nurse_task' AND detail LIKE 'Assigned care task%'")
con.commit()
con.close()
check("cleanup done", True)

print(f"\n===== {passed} passed, {failed} failed =====")
raise SystemExit(1 if failed else 0)
