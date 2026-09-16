"""Seed the database with 2 demo patient records. Run: python seed.py"""
from datetime import date
from sqlmodel import Session, select
from app.database import engine, init_db
from app.models import Patient

init_db()

seed_patients = [
    Patient(
        first_name="Jane", last_name="Doe", date_of_birth=date(1990, 5, 15),
        sex="Female", phone_number="5551234567", email="jane.doe@example.com",
        address_line_1="123 Main St", city="Austin", state="TX", zip_code="78701",
        preferred_language="English",
    ),
    Patient(
        first_name="Carlos", last_name="Mendoza", date_of_birth=date(1978, 11, 2),
        sex="Male", phone_number="5559876543", email="carlos.m@example.com",
        address_line_1="456 Oak Ave", address_line_2="Apt 3B", city="Denver",
        state="CO", zip_code="80202", insurance_provider="Blue Cross",
        insurance_member_id="BC-88214", preferred_language="Spanish",
        emergency_contact_name="Maria Mendoza", emergency_contact_phone="5559876544",
    ),
]

with Session(engine) as session:
    for p in seed_patients:
        existing = session.exec(select(Patient).where(Patient.phone_number == p.phone_number)).first()
        if not existing:
            session.add(p)
    session.commit()

print(f"Seeded {len(seed_patients)} demo patients (skipping any that already exist).")
