"""
models.py - Modelos SQLAlchemy com Restrição de Unicidade Global de Telefone
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from src.database.connection import Base

class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False, index=True)
    description = Column(Text, nullable=True)
    status = Column(String(30), default="active", index=True)  # active, paused, completed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    contacts = relationship("Contact", back_populates="campaign", cascade="all, delete-orphan")

class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    
    raw_phone = Column(String(50), nullable=False)
    # UNICIDADE GLOBAL: Sob hipótese alguma o banco de dados armazena telefones repetidos
    phone = Column(String(20), nullable=False, unique=True, index=True)
    whatsapp_name = Column(String(150), nullable=True)
    contact_name = Column(String(150), nullable=True)
    clean_name = Column(String(150), nullable=True)
    
    cpf = Column(String(14), nullable=True, index=True)
    mind7_name = Column(String(150), nullable=True)
    cep = Column(String(10), nullable=True)
    house_number = Column(String(20), nullable=True)
    age = Column(String(30), nullable=True)  # Histórico legado; o fluxo usa birth_date.
    birth_date = Column(String(10), nullable=True)  # ISO: YYYY-MM-DD
    enrichment_status = Column(String(30), default="PENDENTE", index=True)
    enrichment_attempts = Column(Integer, default=0)
    enrichment_observation = Column(Text, nullable=True)
    enriched_at = Column(DateTime, nullable=True)
    mind7_attempts = Column(Integer, default=0)
    
    credline_status = Column(String(50), default="PENDENTE", index=True)
    financed_value = Column(String(50), nullable=True)
    observation = Column(Text, nullable=True)
    c6_status = Column(String(40), default="PENDENTE", index=True)
    c6_financed_value = Column(String(50), nullable=True)
    c6_observation = Column(Text, nullable=True)
    c6_attempts = Column(Integer, default=0)
    c6_processed_at = Column(DateTime, nullable=True)
    
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    campaign = relationship("Campaign", back_populates="contacts")
    audit_logs = relationship("AuditLog", back_populates="contact", cascade="all, delete-orphan")

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True, index=True)
    step = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    message = Column(Text, nullable=True)
    screenshot_path = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    contact = relationship("Contact", back_populates="audit_logs")
