#!/usr/bin/env python3
"""
One-time migration: Add MA (Music Assistant) credentials to the system user.
Run via: docker exec -i identity python3 /app/scripts/seed_ma.py
Or:     python3 scripts/seed_ma_identity.py

Requires MA_URL and MA_TOKEN in .env (NOT committed to git).
"""
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Load .env (gitignored - credentials come from here, never hardcoded)
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from sqlmodel import Session, select, create_engine
from services.identity.models import User
from services.identity.crypto import encrypt

# MA credentials from .env only
MA_URL = os.getenv("MA_URL", os.getenv("MA_URL", "http://ha.sumemail.com:8095"))
MA_TOKEN = os.getenv("MA_TOKEN", os.getenv("MA_TOKEN"))

# Database URL - default to sqlite at /data/identity.db
DB_URL = os.getenv("DATABASE_URL", "sqlite:////data/identity.db")

if "sqlite" in DB_URL:
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        DB_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
        echo=False,
    )
else:
    engine = create_engine(DB_URL, echo=False)

def run_migration():
    with Session(engine) as session:
        # Find the system default user (or any user)
        user = session.exec(select(User).where(User.is_system_default == True)).first()
        if not user:
            user = session.exec(select(User)).first()
        
        if not user:
            print("[ERROR] No user found in database. Seed the database first.")
            sys.exit(1)
        
        print(f"[INFO] Updating MA credentials for user: {user.username} (id={user.id})")
        
        # Check if already set
        if user.mass_url == MA_URL and user.mass_token_enc:
            print("[INFO] MA credentials already set. Nothing to do.")
        else:
            user.mass_url = MA_URL
            user.mass_token_enc = encrypt(MA_TOKEN)
            session.add(user)
            session.commit()
            print(f"[OK] MA credentials updated: url={MA_URL}")
            print(f"[OK] MA token encrypted and stored.")

if __name__ == "__main__":
    run_migration()
