import os
from datetime import date
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, Date, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

Base = declarative_base()


class Run(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    iso_week = Column(String(10), nullable=False)   # e.g. "2026-W17"
    run_number = Column(Integer, nullable=False)    # 1, 2 or 3
    score_global = Column(Integer, nullable=False)
    score_cat1 = Column(Integer, nullable=False, default=0)
    score_cat2 = Column(Integer, nullable=False, default=0)
    score_cat3 = Column(Integer, nullable=False, default=0)
    score_cat4 = Column(Integer, nullable=False, default=0)
    score_cat5 = Column(Integer, nullable=False, default=0)
    score_cat6 = Column(Integer, nullable=False, default=0)
    citation_rate = Column(Float, nullable=False)   # percentage 0-100
    avg_rank = Column(Float, nullable=True)
    brand_cited = Column(Boolean, nullable=False, default=False)
    brand_position_avg = Column(Float, nullable=True)

    details = relationship("Detail", back_populates="run", cascade="all, delete-orphan")


class Detail(Base):
    __tablename__ = "details"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=False)
    prompt_id = Column(String(10), nullable=False)
    category = Column(String(100), nullable=False)
    category_num = Column(Integer, nullable=False)
    cited = Column(Boolean, nullable=False)
    position = Column(Integer, nullable=True)
    score = Column(Integer, nullable=False)
    response_preview = Column(String(150), nullable=False)

    run = relationship("Run", back_populates="details")


def get_engine():
    url = os.getenv("DATABASE_URL")
    if url:
        return create_engine(url)
    return create_engine("sqlite:///tracker.db")


def get_session():
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    print("[DB] Tables initialized.")
