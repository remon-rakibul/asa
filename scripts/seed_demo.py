"""Seed a rich, coherent demo dataset for the ASA marketplace.

Additive and idempotent — safe to re-run and safe for existing auth: it never
touches `users` (hospital admins / platform_admin) or `patient_accounts`
(patient logins). It:

  * enriches every existing active doctor with fees, degrees, a bio and a
    weekly schedule (so they become bookable — the manual "book" flow needs
    open slots, otherwise it falls back to the AI chat),
  * adds several fully-populated fictional hospitals, each with departments,
    doctors, schedules, and
  * seeds published reviews (drives the rating/review UI).

Run inside the backend container so settings.database_url points at Postgres:

    docker compose exec -T backend python - < scripts/seed_demo.py
"""

from __future__ import annotations

import asyncio
from datetime import time

import asyncpg

from config import settings

# BD work week runs Sun–Thu. Python weekday(): Mon=0 … Sun=6 → Sun–Thu = 6,0,1,2,3.
SUN_THU = [6, 0, 1, 2, 3]
SAT_WED = [5, 6, 0, 1, 2]

# Session windows (one row per doctor per day — uq_schedule_doctor_day). Wide
# windows so the next 7 days always have plenty of 30-minute slots.
MORNING = (time(10, 0), time(13, 0))
EVENING = (time(17, 0), time(21, 0))
DAYTIME = (time(9, 30), time(16, 30))

# Fictional hospitals — deliberately not real brands. Each department becomes a
# clinic row; each doctor a doctors row with a schedule.
HOSPITALS = [
    {
        "slug": "dhaka-central-hospital", "name": "Dhaka Central Hospital",
        "address": "House 12, Road 5, Dhanmondi, Dhaka",
        "departments": [
            {"name": "Cardiology", "code": "CARD", "doctors": [
                {"name": "Dr. Imran Kabir", "specialty": "Cardiology", "degrees": "MBBS, MD (Cardiology), FCPS",
                 "fee_new": 1000, "fee_followup": 600, "days": SUN_THU, "window": EVENING,
                 "bio": "Interventional cardiologist with 15 years' experience in angioplasty and heart-failure care."},
                {"name": "Dr. Sadia Rahman", "specialty": "Cardiology", "degrees": "MBBS, MD, MRCP",
                 "fee_new": 900, "fee_followup": 500, "days": SAT_WED, "window": MORNING,
                 "bio": "Focuses on preventive cardiology, hypertension and echocardiography."},
            ]},
            {"name": "Medicine", "code": "MED", "doctors": [
                {"name": "Dr. Anisur Rahman", "specialty": "Internal Medicine", "degrees": "MBBS, FCPS (Medicine)",
                 "fee_new": 700, "fee_followup": 400, "days": SUN_THU, "window": DAYTIME,
                 "bio": "General physician treating diabetes, fever, and chronic disease management."},
            ]},
            {"name": "Orthopedics", "code": "ORTH", "doctors": [
                {"name": "Dr. Tanjil Ahmed", "specialty": "Orthopedics", "degrees": "MBBS, MS (Ortho)",
                 "fee_new": 800, "fee_followup": 500, "days": [0, 2, 3], "window": EVENING,
                 "bio": "Joint replacement and sports-injury specialist."},
            ]},
        ],
    },
    {
        "slug": "bengal-specialized-hospital", "name": "Bengal Specialized Hospital",
        "address": "Plot 7, Bir Uttam Rafiqul Islam Ave, Badda, Dhaka",
        "departments": [
            {"name": "Pediatrics", "code": "PEDS", "doctors": [
                {"name": "Dr. Farhana Yasmin", "specialty": "Pediatrics", "degrees": "MBBS, DCH, FCPS (Paediatrics)",
                 "fee_new": 700, "fee_followup": 400, "days": SUN_THU, "window": MORNING,
                 "bio": "Newborn and child-health specialist; vaccination and growth monitoring."},
                {"name": "Dr. Mahmudul Hasan", "specialty": "Pediatrics", "degrees": "MBBS, MD (Paediatrics)",
                 "fee_new": 650, "fee_followup": 350, "days": [5, 6, 1, 3], "window": EVENING,
                 "bio": "Paediatric respiratory and allergy care."},
            ]},
            {"name": "Gynecology", "code": "GYN", "doctors": [
                {"name": "Dr. Nasrin Sultana", "specialty": "Gynecology & Obstetrics", "degrees": "MBBS, FCPS (Gynae & Obs)",
                 "fee_new": 900, "fee_followup": 500, "days": SUN_THU, "window": DAYTIME,
                 "bio": "Antenatal care, infertility and laparoscopic surgery."},
            ]},
            {"name": "Dermatology", "code": "DERM", "doctors": [
                {"name": "Dr. Rezaul Karim", "specialty": "Dermatology", "degrees": "MBBS, DDV",
                 "fee_new": 800, "fee_followup": 500, "days": [6, 1, 3], "window": EVENING,
                 "bio": "Skin, hair and cosmetic dermatology."},
            ]},
        ],
    },
    {
        "slug": "riverside-medical-college", "name": "Riverside Medical College Hospital",
        "address": "Riverside Rd, Uttara Sector 4, Dhaka",
        "departments": [
            {"name": "Neurology", "code": "NEURO", "doctors": [
                {"name": "Dr. Kamal Uddin", "specialty": "Neurology", "degrees": "MBBS, MD (Neurology)",
                 "fee_new": 1200, "fee_followup": 700, "days": [0, 2, 4], "window": EVENING,
                 "bio": "Stroke, epilepsy and headache management."},
            ]},
            {"name": "Gastroenterology", "code": "GASTRO", "doctors": [
                {"name": "Dr. Shirin Akter", "specialty": "Gastroenterology", "degrees": "MBBS, MD (Gastro)",
                 "fee_new": 1000, "fee_followup": 600, "days": SUN_THU, "window": MORNING,
                 "bio": "Endoscopy, liver disease and IBS care."},
            ]},
            {"name": "ENT", "code": "ENT", "doctors": [
                {"name": "Dr. Habibur Rahman", "specialty": "ENT (Otolaryngology)", "degrees": "MBBS, DLO",
                 "fee_new": 700, "fee_followup": 400, "days": [5, 6, 1, 2], "window": DAYTIME,
                 "bio": "Ear, nose and throat surgery; hearing and sinus care."},
            ]},
        ],
    },
    {
        "slug": "green-delta-hospital", "name": "Green Delta Hospital",
        "address": "Green Rd, Panthapath, Dhaka",
        "departments": [
            {"name": "Ophthalmology", "code": "EYE", "doctors": [
                {"name": "Dr. Sabbir Hossain", "specialty": "Ophthalmology", "degrees": "MBBS, DO, FCPS (Eye)",
                 "fee_new": 800, "fee_followup": 500, "days": SUN_THU, "window": EVENING,
                 "bio": "Cataract and retina specialist."},
            ]},
            {"name": "Medicine", "code": "MED", "doctors": [
                {"name": "Dr. Lutfar Rahman", "specialty": "Internal Medicine", "degrees": "MBBS, MRCP (UK)",
                 "fee_new": 900, "fee_followup": 500, "days": SAT_WED, "window": DAYTIME,
                 "bio": "Internal medicine, diabetes and thyroid disorders."},
                {"name": "Dr. Ayesha Noor", "specialty": "Internal Medicine", "degrees": "MBBS, FCPS (Medicine)",
                 "fee_new": 750, "fee_followup": 400, "days": [6, 0, 2], "window": MORNING,
                 "bio": "General medicine and preventive health checkups."},
            ]},
        ],
    },
]

# A rotating pool of Bangla reviews (rating, text). Reviewers are drawn from the
# existing patient_accounts so ratings/counts render on doctor pages.
REVIEWS = [
    (5, "খুব ভালো ডাক্তার, মনোযোগ দিয়ে কথা শোনেন।"),
    (5, "সময় মতো দেখেছেন এবং চিকিৎসা কার্যকর হয়েছে।"),
    (4, "অভিজ্ঞ এবং বন্ধুভাবাপন্ন। সুপারিশ করছি।"),
    (4, "ভালো পরামর্শ দিয়েছেন, তবে অপেক্ষা একটু বেশি ছিল।"),
    (5, "ধন্যবাদ, আমার সমস্যার সঠিক সমাধান পেয়েছি।"),
    (3, "চিকিৎসা ঠিক ছিল, চেম্বার আরও গোছানো হলে ভালো হতো।"),
]


async def _reviewer_ids(conn) -> list[int]:
    rows = await conn.fetch("SELECT id FROM patient_accounts ORDER BY id")
    return [r["id"] for r in rows]


async def _add_schedule(conn, clinic_id: int, doctor_id: int, days, window) -> None:
    start, end = window
    for dow in days:
        await conn.execute(
            """INSERT INTO doctor_schedule (clinic_id, doctor_id, day_of_week, start_time, end_time, slot_duration)
               VALUES ($1, $2, $3, $4, $5, 30)
               ON CONFLICT (clinic_id, doctor_id, day_of_week) DO UPDATE
               SET start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time""",
            clinic_id, doctor_id, dow, start, end,
        )


async def _add_reviews(conn, doctor_id: int, reviewer_ids: list[int], start: int, n: int) -> None:
    for i in range(n):
        acct = reviewer_ids[(start + i) % len(reviewer_ids)]
        rating, text = REVIEWS[(start + i) % len(REVIEWS)]
        await conn.execute(
            """INSERT INTO doctor_reviews (doctor_id, account_id, rating, text, status)
               VALUES ($1, $2, $3, $4, 'published')
               ON CONFLICT (doctor_id, account_id) DO NOTHING""",
            doctor_id, acct, rating, text,
        )


async def seed() -> None:
    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        reviewer_ids = await _reviewer_ids(conn)
        review_cursor = 0

        # 1) Enrich existing active doctors + give them a schedule so they book.
        existing = await conn.fetch(
            """SELECT d.id, d.clinic_id, d.name, d.specialty, d.fee_new, d.degrees
               FROM doctors d JOIN clinics c ON c.id = d.clinic_id
               WHERE c.status = 'active'"""
        )
        for idx, d in enumerate(existing):
            await conn.execute(
                """UPDATE doctors SET
                     degrees = CASE WHEN degrees = '' THEN 'MBBS, FCPS' ELSE degrees END,
                     description = CASE WHEN description = '' THEN
                        'Experienced consultant providing patient-centred care.' ELSE description END,
                     specialty = CASE WHEN specialty = '' THEN 'Cardiology' ELSE specialty END,
                     fee_new = COALESCE(fee_new, 800),
                     fee_followup = COALESCE(fee_followup, 500)
                   WHERE id = $1""",
                d["id"],
            )
            window = [MORNING, EVENING, DAYTIME][idx % 3]
            days = [SUN_THU, SAT_WED, [6, 0, 2, 3]][idx % 3]
            await _add_schedule(conn, d["clinic_id"], d["id"], days, window)
            await _add_reviews(conn, d["id"], reviewer_ids, review_cursor, 3)
            review_cursor += 3

        # 2) New hospitals → departments (clinics) → doctors → schedules → reviews.
        for h in HOSPITALS:
            hosp_id = await conn.fetchval(
                """INSERT INTO hospitals (slug, name, address, status)
                   VALUES ($1, $2, $3, 'active')
                   ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, address = EXCLUDED.address
                   RETURNING id""",
                h["slug"], h["name"], h["address"],
            )
            for dept in h["departments"]:
                clinic_slug = f"{h['slug']}-{dept['code'].lower()}"
                clinic_id = await conn.fetchval(
                    """INSERT INTO clinics (slug, name, hospital_id, specialty_code, doctor_name, status)
                       VALUES ($1, $2, $3, $4, $5, 'active')
                       ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name,
                         hospital_id = EXCLUDED.hospital_id, specialty_code = EXCLUDED.specialty_code
                       RETURNING id""",
                    clinic_slug, dept["name"], hosp_id, dept["code"],
                    dept["doctors"][0]["name"],
                )
                for j, doc in enumerate(dept["doctors"]):
                    # Idempotent doctor identity: (clinic_id, name).
                    doc_id = await conn.fetchval(
                        "SELECT id FROM doctors WHERE clinic_id = $1 AND name = $2",
                        clinic_id, doc["name"],
                    )
                    if doc_id is None:
                        doc_id = await conn.fetchval(
                            """INSERT INTO doctors
                                 (clinic_id, name, specialty, degrees, description, fee_new, fee_followup, is_primary)
                               VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id""",
                            clinic_id, doc["name"], doc["specialty"], doc["degrees"],
                            doc["bio"], doc["fee_new"], doc["fee_followup"], j == 0,
                        )
                    else:
                        await conn.execute(
                            """UPDATE doctors SET specialty=$2, degrees=$3, description=$4,
                                 fee_new=$5, fee_followup=$6 WHERE id=$1""",
                            doc_id, doc["specialty"], doc["degrees"], doc["bio"],
                            doc["fee_new"], doc["fee_followup"],
                        )
                    await _add_schedule(conn, clinic_id, doc_id, doc["days"], doc["window"])
                    await _add_reviews(conn, doc_id, reviewer_ids, review_cursor, 4)
                    review_cursor += 4

        # Summary
        docs = await conn.fetchval("SELECT COUNT(*) FROM doctors")
        hosps = await conn.fetchval("SELECT COUNT(*) FROM hospitals WHERE status='active'")
        sched = await conn.fetchval("SELECT COUNT(DISTINCT doctor_id) FROM doctor_schedule WHERE doctor_id IS NOT NULL")
        revs = await conn.fetchval("SELECT COUNT(*) FROM doctor_reviews")
        print(f"Seed complete: {hosps} active hospitals, {docs} doctors, "
              f"{sched} doctors with schedules, {revs} reviews.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())
