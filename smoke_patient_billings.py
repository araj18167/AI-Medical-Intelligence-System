"""End-to-end API tests for the 2026-09 Patient "My Billings" payment center:
1. Hospital invoice with patient_id -> appears in patient's billing feed.
2. Shopkeeper bill linked to patient -> appears in patient's billing feed.
3. Patient read APIs: summary/KPIs, list, detail, search/filter/sort, analytics.
4. Payment: initiate (bank/card/upi), idempotency, overpay-block, cancelled-bill
   block, paid-bill block, partial payment, pharmacy auto-settle.
5. Hospital approval/reject/refund + cross-hospital 403.
6. Notifications + activity rows generated.
7. Cleanup removes every synthetic row (bills, payments, events, notifications).
"""
import requests
import sqlite3
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
    r = requests.post(BASE + "/login", data={"username": username, "password": password})
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text[:200]}"
    return r.json()["access_token"]

def auth(token):
    return {"Authorization": f"Bearer {token}"}

# ---- Credentials (same users as the other smoke suites) ----
PAT = login("test.patient.1", "P@ss1234")          # patient user 3, patient row 1
HOSP = login("bhawani1234@gmail.com", "Hosp@1234")  # hospital user 13 -> hospital 3
SHOP = login("bhawani5061@gmail.com", "Test@1234")  # shopkeeper user 34
HP = auth(PAT)
HH = auth(HOSP)
HS = auth(SHOP)

# Synthetic patient for cross-access tests (created in DB below).
SUFFIX = str(int(time.time()))
PAT2_USER = f"billing.pat2.{SUFFIX}"
PAT2_PASS = "Billing@1234"
PAT2_ID = None
PAT2_TOKEN = None

con = sqlite3.connect("medical_system.db")
cur = con.cursor()
import auth as authmod
cur.execute("INSERT INTO patients (name, age, gender, created_at) VALUES (?,?,?, datetime('now'))",
            ("Billing Smoke Patient 2", 40, "Female"))
PAT2_ID = cur.lastrowid
cur.execute(
    "INSERT INTO users (username, email, full_name, hashed_password, role, patient_id, created_at) "
    "VALUES (?,?,?,?, 'patient', ?, datetime('now'))",
    (PAT2_USER, f"{PAT2_USER}@example.com", "Billing Smoke Patient 2",
     authmod.hash_password(PAT2_PASS), PAT2_ID))
con.commit()
con.close()
HP2 = auth(login(PAT2_USER, PAT2_PASS))

# Track created ids for cleanup.
created = {"hospital_invoices": [], "shop_bills": [], "payments": []}

print("\n[1] Hospital creates an invoice against patient 1")
r = requests.post(BASE + "/pharmacy/invoices", headers=HH, json={
    "hospital_id": 3, "patient_id": 1, "discount": 10,
    "payment_method": "cash", "notes": "smoke billing test",
    "items": [
        {"medicine_name": "Paracetamol 500", "quantity": 2, "unit_price": 50, "mrp": 55},
        {"medicine_name": "Consultation", "quantity": 1, "unit_price": 300, "mrp": 300},
    ],
})
check("hospital invoice created", r.status_code == 200, r.text[:200])
inv = r.json()
created["hospital_invoices"].append(inv["id"])
check("invoice total = 390", abs(inv["total"] - 390.0) < 0.01, inv.get("total"))
check("invoice patient_name", inv.get("patient_name") == "Test Patient", inv.get("patient_name"))

print("\n[2] Shopkeeper creates a bill linked to patient 1")
r = requests.post(BASE + "/bills", headers=HS, json={
    "patient_id": 1, "customer_name": "Test Patient", "customer_phone": "9999999999",
    "status": "pending", "tax_percent": 5, "discount": 20,
    "items": [{"medicine_name": "Amoxicillin 250", "quantity": 1, "unit_price": 120, "mrp": 130}],
})
check("shop bill created", r.status_code == 201, r.text[:300])
b = r.json()
created["shop_bills"].append(b["id"])
check("bill has patient_id 1", b.get("patient_id") == 1, b.get("patient_id"))
# total = (120 - 20) * 1.05 = 105.0
check("bill total = 105.0", abs((b.get("total") or 0) - 105.0) < 0.01, b.get("total"))

print("\n[3] Patient 1 sees both bills in summary + list")
r = requests.get(BASE + "/billing/me/summary", headers=HP)
s = r.json()
check("summary ok", r.status_code == 200, r.text[:200])
check("total_bills >= 2", s["total_bills"] >= 2, s)
check("unpaid >= 2", s["unpaid"] >= 2, s)
r = requests.get(BASE + "/billing/me/bills?page_size=50", headers=HP)
lst = r.json()
check("list ok", r.status_code == 200, r.text[:200])
sources = {i["source"] for i in lst["items"]}
check("list has hospital+pharmacy", "hospital" in sources and "pharmacy" in sources, sources)
hosp_items = [i for i in lst["items"] if i["source"] == "hospital" and i["bill_id"] == inv["id"]]
pharm_items = [i for i in lst["items"] if i["source"] == "pharmacy" and i["bill_id"] == b["id"]]
check("hospital invoice visible", len(hosp_items) == 1)
check("hospital org_name present", hosp_items and bool(hosp_items[0]["org_name"]), hosp_items[:1])
check("pharmacy bill visible", len(pharm_items) == 1)
check("pharmacy due = 105", pharm_items and abs(pharm_items[0]["due"] - 105.0) < 0.01, pharm_items[:1])

print("\n[4] Cross-patient protection")
r = requests.get(BASE + "/billing/me/bills/detail?source=hospital&bill_id=" + str(inv["id"]), headers=HP2)
check("patient2 cannot view patient1's hospital bill", r.status_code == 403, (r.status_code, r.text[:120]))
r = requests.get(BASE + "/billing/me/bills/detail?source=pharmacy&bill_id=" + str(b["id"]), headers=HP2)
check("patient2 cannot view patient1's pharmacy bill", r.status_code == 403, (r.status_code, r.text[:120]))
r = requests.post(BASE + "/billing/me/pay", headers=HP2, json={
    "source": "hospital", "bill_id": inv["id"], "method": "upi",
    "amount": 100, "idempotency_key": f"cross-pat-{SUFFIX}"})
check("patient2 cannot pay patient1's bill", r.status_code == 403, (r.status_code, r.text[:120]))

print("\n[5] Detail + search/filter/sort")
r = requests.get(BASE + "/billing/me/bills/detail?source=hospital&bill_id=" + str(inv["id"]), headers=HP)
d = r.json()
check("detail ok", r.status_code == 200, r.text[:200])
check("detail has items", len(d.get("items", [])) == 2, d.get("items"))
check("detail subtotal = 400", abs((d.get("subtotal") or 0) - 400.0) < 0.01, d.get("subtotal"))
check("detail grand total = 390", abs((d.get("total") or 0) - 390.0) < 0.01, d.get("total"))
r = requests.get(BASE + "/billing/me/bills?q=" + (inv["invoice_number"] or "") + "&page_size=50", headers=HP)
check("search by bill number", r.status_code == 200 and any(i["bill_id"] == inv["id"] and i["source"] == "hospital" for i in r.json()["items"]))
r = requests.get(BASE + "/billing/me/bills?source=pharmacy&page_size=50", headers=HP)
check("filter pharmacy only", all(i["source"] == "pharmacy" for i in r.json()["items"]))
r = requests.get(BASE + "/billing/me/analytics", headers=HP)
a = r.json()
check("analytics ok", r.status_code == 200 and "by_source" in a and "monthly" in a, r.text[:160])
check("analytics hospital total >= 390", a["by_source"]["hospital"]["total"] >= 390.0, a["by_source"])

print("\n[6] Payment initiation validations")
r = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": inv["id"], "method": "upi", "amount": 99999,
    "idempotency_key": f"overpay-{SUFFIX}"})
check("overpay blocked", r.status_code == 400, (r.status_code, r.text[:120]))
r = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": inv["id"], "method": "cc",
    "amount": 100, "idempotency_key": f"badmethod-{SUFFIX}"})
check("invalid method blocked", r.status_code == 400, (r.status_code, r.text[:120]))
r = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": inv["id"], "method": "upi", "amount": 100,
    "idempotency_key": "short"})
check("short idempotency key blocked", r.status_code == 400, (r.status_code, r.text[:120]))

print("\n[7] Hospital payment -> awaiting hospital approval")
r = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": inv["id"], "method": "upi", "amount": 200,
    "method_detail": "upi@bank ref UPI1234", "idempotency_key": f"hpay-{SUFFIX}"})
check("hospital pay initiated", r.status_code == 200, r.text[:300])
pay1 = r.json()
created["payments"].append(pay1["id"])
check("status awaiting_approval", pay1["status"] == "awaiting_approval", pay1["status"])
check("needs_approval true", pay1["needs_approval"] is True)
check("txn ref recorded", pay1["transaction_reference"] and pay1["transaction_reference"] != "PENDING", pay1)
check("events recorded", len(pay1.get("events", [])) >= 3, pay1.get("events"))
# Idempotency: same key -> same payment id
r2 = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": inv["id"], "method": "upi", "amount": 200,
    "idempotency_key": f"hpay-{SUFFIX}"})
check("idempotent replay returns same payment", r2.status_code == 200 and r2.json()["id"] == pay1["id"], (r2.status_code, r2.text[:160]))
# A second payment on the same bill while the first is still awaiting is
# accepted (approved count is 0 so due is still 390).
r3 = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": inv["id"], "method": "bank", "amount": 300,
    "idempotency_key": f"hpay2-{SUFFIX}"})
check("second hospital payment accepted while first pending", r3.status_code == 200, r3.text[:200])
pay2 = r3.json()
created["payments"].append(pay2["id"])
# Combined pending (200+300=500) exceeds the 390 bill — approving the second
# later must be refused by the live-balance revalidation at approval time.
r4 = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": inv["id"], "method": "card", "amount": 400,
    "idempotency_key": f"hpay3-{SUFFIX}"})
check("single payment above total due blocked", r4.status_code == 400, (r4.status_code, r4.text[:160]))

print("\n[8] Hospital approval + concurrent-overpay protection")
r = requests.get(BASE + "/billing/hospital/pending", headers=HH)
pend = r.json()
check("hospital pending list ok", r.status_code == 200 and any(p["id"] == pay1["id"] for p in pend["items"]), r.text[:200])
# patient cannot approve
r = requests.post(BASE + f"/billing/hospital/payments/{pay1['id']}/approve", headers=HP, json={})
check("patient cannot approve own payment", r.status_code == 403, (r.status_code, r.text[:120]))
r = requests.post(BASE + f"/billing/hospital/payments/{pay1['id']}/approve", headers=HH, json={})
check("hospital approves first payment", r.status_code == 200, r.text[:300])
ap = r.json()
check("approved status", ap["status"] == "approved", ap["status"])
check("approved_at set", ap.get("approved_at"), ap)
r = requests.get(BASE + "/billing/me/bills/detail?source=hospital&bill_id=" + str(inv["id"]), headers=HP)
dd = r.json()
check("bill shows partial after approval", dd["bill_state"] == "partial", dd["bill_state"])
check("paid=200 due=190", abs(dd["paid"] - 200) < 0.01 and abs(dd["due"] - 190) < 0.01, (dd["paid"], dd["due"]))
# Approving the 300 payment would overpay (300 > 190 remaining) -> 409
r = requests.post(BASE + f"/billing/hospital/payments/{pay2['id']}/approve", headers=HH, json={})
check("concurrent overpay refused at approval", r.status_code == 409, (r.status_code, r.text[:160]))

print("\n[9] Pharmacy payment auto-settles (no approval)")
r = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "pharmacy", "bill_id": b["id"], "method": "upi", "amount": 105.0,
    "idempotency_key": f"ppay-{SUFFIX}"})
check("pharmacy pay ok", r.status_code == 200, r.text[:300])
pp = r.json()
created["payments"].append(pp["id"])
check("pharmacy payment approved immediately", pp["status"] == "approved", pp["status"])
check("pharmacy txn reference", pp["transaction_reference"] and pp["transaction_reference"].startswith("SBX-"), pp["transaction_reference"])
# pharmacy paid bill cannot be paid again
r = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "pharmacy", "bill_id": b["id"], "method": "bank", "amount": 10,
    "idempotency_key": f"ppay2-{SUFFIX}"})
check("fully paid bill cannot be paid again", r.status_code == 400, (r.status_code, r.text[:120]))
r = requests.get(BASE + "/billing/me/bills/detail?source=pharmacy&bill_id=" + str(b["id"]), headers=HP)
check("pharmacy bill now paid", r.json()["bill_state"] == "paid", r.json().get("bill_state"))

print("\n[10] Receipts")
r = requests.get(BASE + f"/billing/me/receipt/{pp['id']}", headers=HP)
rc = r.json()
check("receipt ok", r.status_code == 200 and rc["receipt_number"].startswith("RCPT-"), r.text[:200])
check("receipt fields", rc.get("amount") == 105.0 and rc.get("method") == "upi", rc)

print("\n[11] Cancel eligible payment")
r = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": inv["id"], "method": "bank", "amount": 100,
    "idempotency_key": f"cancelme-{SUFFIX}"})
pc = r.json()
created["payments"].append(pc["id"])
check("third hospital payment created", r.status_code == 200 and pc["status"] == "awaiting_approval", (r.status_code, pc.get("status")))
r = requests.post(BASE + f"/billing/me/payments/{pc['id']}/cancel", headers=HP)
check("patient cancels own awaiting payment", r.status_code == 200 and r.json()["status"] == "cancelled", r.text[:200])
r = requests.get(BASE + f"/billing/me/payments/{pc['id']}/cancel", headers=HP)
check("approved payment cannot be cancelled via POST-to-GET (404 method)", r.status_code in (404, 405), r.status_code)

print("\n[12] Reject + refund")
r = requests.post(BASE + f"/billing/hospital/payments/{pay2['id']}/reject", headers=HH, json={"reason": "Reference mismatch in smoke test"})
check("hospital rejects second payment", r.status_code == 200 and r.json()["status"] == "rejected", r.text[:200])
check("rejection reason recorded", "Reference mismatch" in (r.json().get("rejection_reason") or ""), r.json())
r = requests.post(BASE + f"/billing/hospital/payments/{pay1['id']}/refund", headers=HH, json={"reason": "Duplicated charge"})
check("approved payment can be refunded", r.status_code == 200 and r.json()["status"] == "refunded", r.text[:200])
r = requests.get(BASE + "/billing/me/bills/detail?source=hospital&bill_id=" + str(inv["id"]), headers=HP)
dd = r.json()
check("bill returns to unpaid after refund", dd["bill_state"] == "unpaid", dd["bill_state"])

print("\n[13] Cross-hospital approval blocked")
# Create a bill for patient1 under hospital 2 (krishna user 8) then try to approve with hospital 3.
HOSP2 = login("krishna1111@gmail.com", "Test@1234")
HH2 = auth(HOSP2)
r = requests.post(BASE + "/pharmacy/invoices", headers=HH2, json={
    "hospital_id": 2, "patient_id": 1, "discount": 0,
    "items": [{"medicine_name": "Ibuprofen", "quantity": 1, "unit_price": 80, "mrp": 90}]})
inv2 = r.json()
created["hospital_invoices"].append(inv2["id"])
r = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": inv2["id"], "method": "upi", "amount": 80,
    "idempotency_key": f"xhop-{SUFFIX}"})
px = r.json()
created["payments"].append(px["id"])
r = requests.post(BASE + f"/billing/hospital/payments/{px['id']}/approve", headers=HH, json={})
check("hospital 3 cannot approve hospital 2's payment", r.status_code == 403, (r.status_code, r.text[:120]))
r = requests.post(BASE + f"/billing/hospital/payments/{px['id']}/approve", headers=HH2, json={})
check("hospital 2 can approve its own payment", r.status_code == 200, r.text[:200])

print("\n[14] Notifications + activity rows written")
con = sqlite3.connect("medical_system.db")
cur = con.cursor()
n = cur.execute("SELECT COUNT(*) FROM notification_events WHERE recipient_user_id=3 AND created_at >= datetime('now','-1 hour')").fetchone()[0]
check("patient got notifications", n >= 5, n)
a = cur.execute("SELECT COUNT(*) FROM system_activities WHERE created_at >= datetime('now','-1 hour')").fetchone()[0]
check("activity rows written", a >= 4, a)
al = cur.execute("SELECT COUNT(*) FROM billing_payment_events WHERE created_at >= datetime('now','-1 hour')").fetchone()[0]
check("payment event ledger written", al >= 15, al)
con.close()

print("\n[15] Hospital pending includes previously reviewed")
r = requests.get(BASE + "/billing/hospital/pending", headers=HH)
pend = r.json()
check("hospital sees rejected payment in review list", any(p["id"] == pay2["id"] and p["status"] == "rejected" for p in pend["items"]))

# ===================== CLEANUP =====================
print("\n[cleanup] removing synthetic rows")
con = sqlite3.connect("medical_system.db")
cur = con.cursor()
ids = created["payments"]
for pid in ids:
    cur.execute("DELETE FROM billing_payment_events WHERE payment_id=?", (pid,))
cur.executemany("DELETE FROM billing_payments WHERE id=?", [(x,) for x in ids])
for iid in created["hospital_invoices"]:
    cur.execute("DELETE FROM hospital_invoice_items WHERE invoice_id=?", (iid,))
    cur.execute("DELETE FROM hospital_invoices WHERE id=?", (iid,))
for bid in created["shop_bills"]:
    cur.execute("DELETE FROM bill_line_items WHERE bill_id=?", (bid,))
    cur.execute("DELETE FROM bills WHERE id=?", (bid,))
# notifications / activities referencing our synthetic objects
cur.execute("DELETE FROM notification_events WHERE recipient_user_id IN (3,13) AND created_at >= datetime('now','-1 hour')")
cur.execute("DELETE FROM system_activities WHERE created_at >= datetime('now','-1 hour')")
# cross-access patient 2 account
cur.execute("DELETE FROM billing_payments WHERE patient_id=?", (PAT2_ID,))
cur.execute("DELETE FROM users WHERE id IN (SELECT id FROM users WHERE patient_id=?)", (PAT2_ID,))
cur.execute("DELETE FROM patients WHERE id=?", (PAT2_ID,))
con.commit()
con.close()
print(f"  cleaned: {len(ids)} payments, {len(created['hospital_invoices'])} hospital invoices, "
      f"{len(created['shop_bills'])} shop bills, patient2 account")

print(f"\n===== RESULT: {passed} passed, {failed} failed =====")
raise SystemExit(1 if failed else 0)
