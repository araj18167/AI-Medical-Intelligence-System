"""Live validation of the smart/ services layer against the real DB."""
import sys
from database import SessionLocal
import models
from smart import services
import json

def json_small(o):
    return json.dumps(o, default=str)[:400]

db = SessionLocal()

uid = db.query(models.User).filter(models.User.role == "shopkeeper").first()
print("SHOPKEEPER:", uid.id if uid else None, uid.username if uid else None)
if uid:
    intel = services.pharmacy_intelligence(db, uid)
    print("pharmacy_intelligence:", json_small(intel))
    dem = services.pharmacy_demand_detail(db, uid)
    print("demand items:", len(dem["items"]), "insights:", len(dem["insights"]))
    print("first item:", {k: dem["items"][0][k] for k in ("medicine_name", "current_stock", "avg_daily_demand", "reorder_risk", "expiry", "velocity")} if dem["items"] else None)
    fc = services.pharmacy_sales_forecast_detail(db, uid)
    print("sales forecast sufficient:", fc.get("sufficient"))
    anom = services.pharmacy_billing_anomalies_detail(db, uid)
    print("billing anomalies:", anom["issue_count"], anom["by_type"])
    cust = services.pharmacy_customer_analytics_detail(db, uid)
    print("customers:", cust["count"], "returning:", cust["returning_rate"])
    sr = services.pharmacy_smart_search(db, uid, "paracetamol")
    print("search results:", [(r["medicine_name"], r["score"]) for r in sr["results"]])

# patient intelligence
p = db.query(models.Patient).first()
print("PATIENT:", p.id if p else None, p.name if p else None)
if p:
    sig = services._patient_signals(db, p.id)
    print("signals:", sig)
    r = services.patient_risk_detail(db, p.id)
    print("risk:", r["score"], r["category"], list(r["contributing_factors"].keys()))
    t = services.patient_trends_detail(db, p.id)
    print("trends vitals:", len(t["vitals"]), "labs:", len(t["labs"]))
    fp = services.patient_followup_detail(db, p.id)
    print("followup:", fp["level"], fp["reason"])
    tl = services.patient_timeline_detail(db, p.id)
    print("timeline events:", tl.get("total_events"))
    pi = services.patient_intelligence(db, p.id)
    print("patient intel:", pi["data_completeness"], pi["trend_status"], pi["risk"]["score"])

# hospital
h = db.query(models.User).filter(models.User.role == "hospital").first()
print("HOSPITAL:", h.id if h else None)
if h:
    hi = services.hospital_intelligence(db, h)
    print("hospital intel overall:", hi["overall_score"], "bed util:", hi["bed_utilization"]["utilization_pct"], "anomalies:", hi["anomalies"]["flag_count"])

# doctor + nurse
d = db.query(models.User).filter(models.User.role == "doctor").first()
if d:
    dw = services.doctor_workload_detail(db, d)
    print("doctor workload sufficient:", dw["sufficient"], "count:", dw.get("doctor_count"))
n = db.query(models.User).filter(models.User.role == "nurse").first()
if n:
    nw = services.nurse_workload_detail(db, n)
    print("nurse workload sufficient:", nw["sufficient"], "count:", nw.get("nurse_count"))

# insights + quality + registry
if uid:
    ins = services.role_insights(db, uid)
    print("shopkeeper insights:", [(i["kind"], i["severity"]) for i in ins["insights"]])
    q = services.data_quality_report(db, uid)
    print("quality:", q["summary"])
from smart import registry
print("registry models:", len(registry.list_models()))

db.close()