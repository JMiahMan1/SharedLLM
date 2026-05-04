import os
from sqlmodel import Session, SQLModel, create_engine, select
from .models import Workspace

DATABASE_URL = os.getenv("WORKSPACE_DATABASE_URL", "sqlite:///./workspace_runtime.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
