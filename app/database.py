"""
DB engine + session management.

Trade-off note (documented in README too):
Defaults to SQLite for zero-setup local dev and to keep the assessment
fast to stand up. Set DATABASE_URL env var to a Postgres connection string
in production (e.g. Railway's managed Postgres) since SQLite files on
most PaaS platforms live on ephemeral disk and can be wiped on redeploy.
"""
import os
from sqlmodel import SQLModel, create_engine, Session

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./patients.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
