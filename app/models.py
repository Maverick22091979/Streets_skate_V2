from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    connections: Mapped[list["AuthConnection"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    routes: Mapped[list["Route"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthConnection(Base):
    __tablename__ = "auth_connections"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id", name="uq_auth_connections_provider_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_username: Mapped[str | None] = mapped_column(String(255))
    label: Mapped[str | None] = mapped_column(String(255))
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    token_type: Mapped[str | None] = mapped_column(String(50))
    scope: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_profile: Mapped[dict | list | None] = mapped_column(JSON)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="connections")
    routes: Mapped[list["Route"]] = relationship(back_populates="connection", cascade="all, delete-orphan")


class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_routes_provider_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[int] = mapped_column(ForeignKey("auth_connections.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    import_user_label: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    sport_type: Mapped[str | None] = mapped_column(String(100))
    start_date_local: Mapped[str | None] = mapped_column(String(64))
    distance_km: Mapped[float | None] = mapped_column(Float)
    distance_m: Mapped[float | None] = mapped_column(Float)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float)
    avg_grade_pct: Mapped[float | None] = mapped_column(Float)
    average_speed_kmh: Mapped[float | None] = mapped_column(Float)
    moving_time_sec: Mapped[int | None] = mapped_column(Integer)
    summary_polyline: Mapped[str | None] = mapped_column(Text)
    map_polyline_available: Mapped[bool] = mapped_column(default=False, nullable=False)
    polyline_points_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bbox: Mapped[dict | None] = mapped_column(JSON)
    enrichment: Mapped[dict | None] = mapped_column(JSON)
    difficulty: Mapped[dict | None] = mapped_column(JSON)
    raw_payload: Mapped[dict | list | None] = mapped_column(JSON)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="routes")
    connection: Mapped["AuthConnection"] = relationship(back_populates="routes")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_name: Mapped[str | None] = mapped_column(String(255))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    actor_label: Mapped[str] = mapped_column(String(512), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    occurred_at_label: Mapped[str] = mapped_column(String(32), nullable=False)
    request_ip: Mapped[str | None] = mapped_column(String(64))
    request_method: Mapped[str | None] = mapped_column(String(16))
    request_path: Mapped[str | None] = mapped_column(String(1024))
    request_user_agent: Mapped[str | None] = mapped_column(Text)
    request_referer: Mapped[str | None] = mapped_column(Text)
    request_origin: Mapped[str | None] = mapped_column(Text)
    request_forwarded_for: Mapped[str | None] = mapped_column(Text)
    geo_country: Mapped[str | None] = mapped_column(String(128))
    geo_region: Mapped[str | None] = mapped_column(String(128))
    geo_city: Mapped[str | None] = mapped_column(String(128))
    geo_latitude: Mapped[float | None] = mapped_column(Float)
    geo_longitude: Mapped[float | None] = mapped_column(Float)
    geo_org: Mapped[str | None] = mapped_column(String(255))
    geo_asn: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict | list | None] = mapped_column(JSON)
