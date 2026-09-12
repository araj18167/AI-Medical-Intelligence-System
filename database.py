

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

DATABASE_URL = "sqlite:///./medical_system.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # needed only for SQLite
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def _run_lightweight_migrations() -> None:
    """Idempotent column-level migrations for older SQLite files.

    SQLAlchemy's ``create_all`` only creates *missing tables*; it never adds
    new columns to an existing table. This brings an older ``medical_system.db``
    in sync with the current ``models.py`` so we don't hit ``StatementError``
    (sqlalche.me/e/20/e3q8) on INSERT/UPDATE.

    Add new entries here whenever you add a non-nullable column with a server
    default. SQLite's ``ALTER TABLE ADD COLUMN`` does not support ``NOT NULL``
    without a default, so every column added here must be nullable.
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return  # create_all() below will make the fresh table.

    existing = {c["name"] for c in inspector.get_columns("users")}
    migrations = [
        # (column_name, ALTER TABLE statement)
        ("profile_picture", "ALTER TABLE users ADD COLUMN profile_picture VARCHAR"),
        # Doctor → patient auto-credentials feature.
        ("created_by_doctor_id", "ALTER TABLE users ADD COLUMN created_by_doctor_id INTEGER"),
        # Shopkeeper 2026-09: composition text on stock, bill payment status.
        ("composition", "ALTER TABLE medicine_stock ADD COLUMN composition VARCHAR"),
        ("status", "ALTER TABLE bills ADD COLUMN status VARCHAR"),
    ]

    with engine.begin() as conn:
        for col, ddl in migrations:
            if col in existing:
                continue
            try:
                conn.execute(text(ddl))
            except Exception as exc:  # pragma: no cover - defensive
                # Don't crash the server on a best-effort migration; surface
                # the error so it shows up in the logs.
                print(f"[migrations] skipped {ddl}: {exc}")

    # ---- patients.onboarded_by_doctor_id ----
    # Newer files have a column on patients linking them to the doctor who
    # invited them (or who created them via the dashboard). Older DBs don't.
    if "patients" in inspector.get_table_names():
        patient_cols = {c["name"] for c in inspector.get_columns("patients")}
        if "onboarded_by_doctor_id" not in patient_cols:
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        "ALTER TABLE patients ADD COLUMN onboarded_by_doctor_id INTEGER"
                    ))
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[migrations] skipped patients.onboarded_by_doctor_id: {exc}")

    # ---- chat_messages: reply/edit/delete columns (2026-09) ----
    if "chat_messages" in inspector.get_table_names():
        cm_cols = {c["name"] for c in inspector.get_columns("chat_messages")}
        with engine.begin() as conn:
            for col, ddl in [
                ("reply_to_id", "ALTER TABLE chat_messages ADD COLUMN reply_to_id INTEGER"),
                ("edited_at", "ALTER TABLE chat_messages ADD COLUMN edited_at DATETIME"),
                ("deleted_at", "ALTER TABLE chat_messages ADD COLUMN deleted_at DATETIME"),
                ("deleted_by_user_id", "ALTER TABLE chat_messages ADD COLUMN deleted_by_user_id INTEGER"),
                ("hidden_from_user_ids", "ALTER TABLE chat_messages ADD COLUMN hidden_from_user_ids TEXT"),
                ("client_message_id", "ALTER TABLE chat_messages ADD COLUMN client_message_id VARCHAR"),
            ]:
                if col in cm_cols:
                    continue
                try:
                    conn.execute(text(ddl))
                except Exception as exc:  # pragma: no cover
                    print(f"[migrations] skipped chat_messages.{col}: {exc}")

    # ---- notification_preferences per-feature flags ----
    # Older DBs were created with just (email_enabled, browser_enabled,
    # reminder_lead_minutes, updated_at). Newer code reads/writes three
    # per-feature boolean columns. SQLite ALTER doesn't support NOT NULL
    # without a default, so we add them as nullable and rely on
    # NotificationPreference defaults at insert time.
    if "notification_preferences" in inspector.get_table_names():
        np_cols = {c["name"] for c in inspector.get_columns("notification_preferences")}
        with engine.begin() as conn:
            for col, ddl in [
                ("notif_patient_added", "ALTER TABLE notification_preferences ADD COLUMN notif_patient_added BOOLEAN"),
                ("notif_doctor_linked", "ALTER TABLE notification_preferences ADD COLUMN notif_doctor_linked BOOLEAN"),
                ("notif_chat_message",  "ALTER TABLE notification_preferences ADD COLUMN notif_chat_message BOOLEAN"),
            ]:
                if col in np_cols:
                    continue
                try:
                    conn.execute(text(ddl))
                except Exception as exc:  # pragma: no cover
                    print(f"[migrations] skipped notification_preferences.{col}: {exc}")

    # ---- drop the old doctor_invite_codes table (no longer in models) ----
    # SQLite doesn't let us drop a table referenced by views or by foreign
    # keys that lack ON DELETE CASCADE, but our schema has none of those, so
    # a plain DROP TABLE works. Wrapped in try/except so a fresh DB (where
    # create_all hasn't run yet) is a no-op rather than a crash.
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS doctor_invite_codes"))
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[migrations] drop doctor_invite_codes: {exc}")

    # ---- bills table: per-shopkeeper bill_number ----
    # Earlier versions had a global UNIQUE constraint on bill_number which
    # prevented two shops from independently starting at INV-YYYYMMDD-0001.
    # The current model uses a composite UniqueConstraint(shopkeeper_id,
    # bill_number). SQLite can't ALTER an existing constraint, so we:
    #   1. drop the global unique index if present, and
    #   2. create a composite unique index on (shopkeeper_id, bill_number).
    if "bills" in inspector.get_table_names():
        bill_indexes = {ix["name"] for ix in inspector.get_indexes("bills")}
        with engine.begin() as conn:
            if "ix_bills_bill_number" in bill_indexes:
                try:
                    conn.execute(text("DROP INDEX ix_bills_bill_number"))
                except Exception as exc:
                    print(f"[migrations] drop ix_bills_bill_number: {exc}")
            if "uq_bills_shopkeeper_bill_number" not in bill_indexes:
                try:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX uq_bills_shopkeeper_bill_number "
                        "ON bills (shopkeeper_id, bill_number)"
                    ))
                except Exception as exc:
                    print(f"[migrations] create composite unique index: {exc}")

    # ---- Bills / bill_line_items / medicine_stock new columns (2026-08) ----
    # Per-feature refactor: "Inventory bill" vs "Proforma bill" — Bill row
    # carries affects_inventory; line items carry richer per-batch metadata;
    # stock rows carry manufacturer + unit + MRP.
    table_schemas = {
        "bills": [
            ("affects_inventory", "ALTER TABLE bills ADD COLUMN affects_inventory BOOLEAN"),
            # 2026-09: authoritative patient link for patient My Billings.
            ("patient_id", "ALTER TABLE bills ADD COLUMN patient_id INTEGER"),
        ],
        "bill_line_items": [
            ("manufacture_date", "ALTER TABLE bill_line_items ADD COLUMN manufacture_date DATE"),
            ("expiry_date",      "ALTER TABLE bill_line_items ADD COLUMN expiry_date DATE"),
            ("unit",             "ALTER TABLE bill_line_items ADD COLUMN unit VARCHAR"),
            ("mrp",              "ALTER TABLE bill_line_items ADD COLUMN mrp FLOAT"),
        ],
        "medicine_stock": [
            ("manufacturer", "ALTER TABLE medicine_stock ADD COLUMN manufacturer VARCHAR"),
            ("unit",         "ALTER TABLE medicine_stock ADD COLUMN unit VARCHAR"),
            ("mrp",          "ALTER TABLE medicine_stock ADD COLUMN mrp FLOAT"),
        ],
        # 2026-09 hospital billing center: bill lifecycle + richer items.
        "hospital_invoices": [
            ("status", "ALTER TABLE hospital_invoices ADD COLUMN status VARCHAR"),
            ("cancelled_at", "ALTER TABLE hospital_invoices ADD COLUMN cancelled_at DATETIME"),
            ("cancelled_by", "ALTER TABLE hospital_invoices ADD COLUMN cancelled_by INTEGER"),
            ("cancellation_reason", "ALTER TABLE hospital_invoices ADD COLUMN cancellation_reason TEXT"),
            ("updated_at", "ALTER TABLE hospital_invoices ADD COLUMN updated_at DATETIME"),
        ],
        "hospital_invoice_items": [
            ("item_type", "ALTER TABLE hospital_invoice_items ADD COLUMN item_type VARCHAR"),
            ("batch_no", "ALTER TABLE hospital_invoice_items ADD COLUMN batch_no VARCHAR"),
            ("discount", "ALTER TABLE hospital_invoice_items ADD COLUMN discount FLOAT"),
            ("tax_percent", "ALTER TABLE hospital_invoice_items ADD COLUMN tax_percent FLOAT"),
            ("tax_amount", "ALTER TABLE hospital_invoice_items ADD COLUMN tax_amount FLOAT"),
        ],
    }
    for table_name, migrations in table_schemas.items():
        if table_name not in inspector.get_table_names():
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        with engine.begin() as conn:
            for col, ddl in migrations:
                if col in existing_cols:
                    continue
                try:
                    conn.execute(text(ddl))
                except Exception as exc:  # pragma: no cover
                    print(f"[migrations] skipped {table_name}.{col}: {exc}")

    # ---- beds (ward_id, bed_number) unique index (2026-08) ----
    # Mirror the bills composite-unique swap: ``__table_args__`` only runs
    # at table-creation time, so DBs that already had a ``beds`` table
    # (without the constraint) won't have it on disk. Create it now if
    # missing. Wrapped in try/except so a fresh DB is a no-op.
    if "beds" in inspector.get_table_names():
        bed_indexes = {ix["name"] for ix in inspector.get_indexes("beds")}
        with engine.begin() as conn:
            if "uq_bed_per_ward" not in bed_indexes:
                try:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX uq_bed_per_ward "
                        "ON beds (ward_id, bed_number)"
                    ))
                except Exception as exc:  # pragma: no cover - defensive
                    print(f"[migrations] create uq_bed_per_ward: {exc}")

    # ---- wards.hospital_id (2026-08) ----
    # Multi-hospital feature: wards now belong to an optional Hospital. The
    # new hospital-admin "place patient" flow only considers beds whose
    # ward.hospital_id matches the admin's hospital. Existing wards are
    # left at NULL (unassigned) — they remain visible to doctors but the
    # hospital-admin place-patient flow will exclude them.
    if "wards" in inspector.get_table_names():
        ward_cols = {c["name"] for c in inspector.get_columns("wards")}
        if "hospital_id" not in ward_cols:
            try:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE wards ADD COLUMN hospital_id INTEGER"))
            except Exception as exc:  # pragma: no cover
                print(f"[migrations] wards.hospital_id: {exc}")

    # ---- admissions.hospital_id (2026-08) ----
    # Set by the place-patient flow. Existing doctor-direct admissions
    # remain at NULL.
    if "admissions" in inspector.get_table_names():
        adm_cols = {c["name"] for c in inspector.get_columns("admissions")}
        if "hospital_id" not in adm_cols:
            try:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE admissions ADD COLUMN hospital_id INTEGER"))
            except Exception as exc:  # pragma: no cover
                print(f"[migrations] admissions.hospital_id: {exc}")

    # ---- admission_requests.new columns (2026-08) ----
    # Two flows share this table:
    #   - legacy patient→doctor-for-bed (request_kind NULL or "patient_to_doctor")
    #   - NEW doctor→hospital (request_kind="doctor_to_hospital")
    # The discriminator keeps a single table; NULL means "legacy".
    if "admission_requests" in inspector.get_table_names():
        ar_cols = {c["name"] for c in inspector.get_columns("admission_requests")}
        with engine.begin() as conn:
            for col, ddl in [
                ("request_kind", "ALTER TABLE admission_requests ADD COLUMN request_kind VARCHAR"),
                ("target_hospital_id", "ALTER TABLE admission_requests ADD COLUMN target_hospital_id INTEGER"),
                ("requested_by_doctor_user_id", "ALTER TABLE admission_requests ADD COLUMN requested_by_doctor_user_id INTEGER"),
                ("placed_admission_id", "ALTER TABLE admission_requests ADD COLUMN placed_admission_id INTEGER"),
            ]:
                if col in ar_cols:
                    continue
                try:
                    conn.execute(text(ddl))
                except Exception as exc:  # pragma: no cover
                    print(f"[migrations] admission_requests.{col}: {exc}")

    # ---- nurse_notes.summary / summary_generated_at (2026-08) ----
    # 2026-08: AI-summary on the nurse report. Both fields are nullable so
    # old notes remain intact and the migration is a pure additive ALTER.
    if "nurse_notes" in inspector.get_table_names():
        nn_cols = {c["name"] for c in inspector.get_columns("nurse_notes")}
        with engine.begin() as conn:
            for col, ddl in [
                ("summary", "ALTER TABLE nurse_notes ADD COLUMN summary TEXT"),
                ("summary_generated_at", "ALTER TABLE nurse_notes ADD COLUMN summary_generated_at DATETIME"),
                ("kind", "ALTER TABLE nurse_notes ADD COLUMN kind VARCHAR"),
            ]:
                if col in nn_cols:
                    continue
                try:
                    conn.execute(text(ddl))
                except Exception as exc:  # pragma: no cover - defensive
                    print(f"[migrations] skipped nurse_notes.{col}: {exc}")

    # ---- notification_preferences.notif_admission_events (2026-08) ----
    # Buckets all hospital / nurse / discharge notifications under one
    # opt-out. Existing rows get NULL which the route treats as True
    # (matches the column default).
    if "notification_preferences" in inspector.get_table_names():
        np_cols = {c["name"] for c in inspector.get_columns("notification_preferences")}
        if "notif_admission_events" not in np_cols:
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        "ALTER TABLE notification_preferences "
                        "ADD COLUMN notif_admission_events BOOLEAN"
                    ))
            except Exception as exc:  # pragma: no cover
                print(f"[migrations] notification_preferences.notif_admission_events: {exc}")

    # ---- patients.aadhar_number / patients.place (2026-08) ----
    # PatientRecords feature: doctors/hospitals/nurses search the patient
    # roster by Aadhaar number, place, age range, gender. ``aadhar_number``
    # is the unique national identity (XXXX-XXXX-XXXX) — stored as a plain
    # string since SQLite has no encryption primitive; existing rows
    # without it remain readable. ``place`` is a free-text locality
    # (city / state) denormalised off ``patient_profiles`` so the
    # search-by-place filter doesn't have to JOIN.
    if "patients" in inspector.get_table_names():
        p_cols = {c["name"] for c in inspector.get_columns("patients")}
        with engine.begin() as conn:
            for col, ddl in [
                ("aadhar_number", "ALTER TABLE patients ADD COLUMN aadhar_number VARCHAR"),
                ("place",         "ALTER TABLE patients ADD COLUMN place VARCHAR"),
            ]:
                if col in p_cols:
                    continue
                try:
                    conn.execute(text(ddl))
                except Exception as exc:  # pragma: no cover
                    print(f"[migrations] skipped patients.{col}: {exc}")

    # ---- mediecho_sessions.doctor_user_id → NULLable (2026-08) ----
    # Patient accounts now create their own sessions for their own record.
    # The model is nullable=True; older SQLite DBs still have NOT NULL on
    # the column. SQLite ALTER can only relax constraints by recreating the
    # table. We rebuild it via the standard 12-step rename-and-copy pattern.
    if "mediecho_sessions" in inspector.get_table_names():
        me_cols = inspector.get_columns("mediecho_sessions")
        if any(c["name"] == "doctor_user_id" and not c["nullable"] for c in me_cols):
            try:
                with engine.begin() as conn:
                    rows = list(conn.execute(text(
                        "SELECT id, doctor_user_id, patient_id, title, audio_filename, "
                        "audio_stored_path, mime_type, file_size, duration_seconds, "
                        "stt_model, transcript_json, status, error_message, "
                        "created_at, updated_at FROM mediecho_sessions"
                    )))
                    conn.execute(text("PRAGMA foreign_keys=OFF"))
                    conn.execute(text("ALTER TABLE mediecho_sessions RENAME TO _mediecho_sessions_old"))
                    conn.execute(text(
                        "CREATE TABLE mediecho_sessions ("
                        "id INTEGER PRIMARY KEY,"
                        "doctor_user_id INTEGER,"
                        "patient_id INTEGER,"
                        "title VARCHAR,"
                        "audio_filename VARCHAR,"
                        "audio_stored_path VARCHAR,"
                        "mime_type VARCHAR,"
                        "file_size INTEGER,"
                        "duration_seconds FLOAT,"
                        "stt_model VARCHAR,"
                        "transcript_json TEXT,"
                        "status VARCHAR NOT NULL DEFAULT 'recording',"
                        "error_message TEXT,"
                        "created_at DATETIME,"
                        "updated_at DATETIME,"
                        "FOREIGN KEY(doctor_user_id) REFERENCES users(id),"
                        "FOREIGN KEY(patient_id) REFERENCES patients(id)"
                        ")"
                    ))
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_mediecho_sessions_doctor_user_id "
                        "ON mediecho_sessions(doctor_user_id)"
                    ))
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_mediecho_sessions_patient_id "
                        "ON mediecho_sessions(patient_id)"
                    ))
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_mediecho_sessions_status "
                        "ON mediecho_sessions(status)"
                    ))
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_mediecho_sessions_created_at "
                        "ON mediecho_sessions(created_at)"
                    ))
                    for r in rows:
                        conn.execute(text(
                            "INSERT INTO mediecho_sessions (id, doctor_user_id, patient_id, "
                            "title, audio_filename, audio_stored_path, mime_type, file_size, "
                            "duration_seconds, stt_model, transcript_json, status, error_message, "
                            "created_at, updated_at) VALUES "
                            "(:id, :doctor_user_id, :patient_id, :title, :audio_filename, "
                            ":audio_stored_path, :mime_type, :file_size, :duration_seconds, "
                            ":stt_model, :transcript_json, :status, :error_message, "
                            ":created_at, :updated_at)"
                        ), dict(r._mapping))
                    conn.execute(text("DROP TABLE _mediecho_sessions_old"))
                    conn.execute(text("PRAGMA foreign_keys=ON"))
            except Exception as exc:
                print(f"[migrations] rebuild mediecho_sessions: {exc}")


def get_db():
    """Provides a database session to each request, and closes it afterward."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def backfill_patient_doctor_links() -> None:
    """One-time migration: copy any pre-existing Patient.onboarded_by_doctor_id
    rows into the new patient_doctor_links table. Safe to call on fresh DBs
    (no rows to copy), and safe to call multiple times (uses INSERT OR IGNORE
    against the unique constraint). Called from main.py AFTER
    ``Base.metadata.create_all`` so the new table exists."""
    inspector = inspect(engine)
    if "patients" not in inspector.get_table_names():
        return
    if "patient_doctor_links" not in inspector.get_table_names():
        return
    with engine.begin() as conn:
        try:
            # Find every patient that has a legacy single-FK link but no new M2M link.
            # We INSERT OR IGNORE so reruns don't error on the unique constraint.
            conn.execute(text(
                "INSERT OR IGNORE INTO patient_doctor_links "
                "(patient_id, doctor_user_id, department, created_at) "
                "SELECT p.id, p.onboarded_by_doctor_id, NULL, p.created_at "
                "FROM patients p "
                "WHERE p.onboarded_by_doctor_id IS NOT NULL "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM patient_doctor_links l "
                "    WHERE l.patient_id = p.id "
                "      AND l.doctor_user_id = p.onboarded_by_doctor_id"
                "  )"
            ))
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[migrations] backfill patient_doctor_links: {exc}")


def seed_default_ward() -> None:
    """One-time seed: insert a single 'General Ward' + 5 beds so the
    beds.html grid is populated on the very first run. Idempotent —
    re-runs are a no-op (we check for any ward row first)."""
    from sqlalchemy import inspect as _inspect
    inspector = _inspect(engine)
    if "wards" not in inspector.get_table_names():
        return  # create_all hasn't run yet — main.py will call us again
    with engine.begin() as conn:
        existing = conn.execute(text("SELECT COUNT(*) FROM wards")).scalar() or 0
        if existing > 0:
            return
        try:
            # Insert the default ward.
            conn.execute(text(
                "INSERT INTO wards (name, ward_type, daily_rate, total_beds, notes, created_at) "
                "VALUES (:name, :wt, :dr, :tb, :notes, :ts)"
            ), {
                "name": "General Ward",
                "wt": "general",
                "dr": 500.0,
                "tb": 5,
                "notes": "Default ward — created automatically on first run.",
                "ts": datetime.utcnow(),
            })
            ward_id = conn.execute(text("SELECT id FROM wards WHERE name = :n"), {"n": "General Ward"}).scalar()
            # Insert 5 beds (G-1 .. G-5).
            for n in range(1, 6):
                conn.execute(text(
                    "INSERT INTO beds (ward_id, bed_number, bed_type, notes, created_at) "
                    "VALUES (:w, :b, :t, :notes, :ts)"
                ), {
                    "w": ward_id,
                    "b": f"G-{n}",
                    "t": "standard",
                    "notes": None,
                    "ts": datetime.utcnow(),
                })
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[migrations] seed_default_ward: {exc}")
