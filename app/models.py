from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    birth_date: Mapped[date | None] = mapped_column(Date)
    username: Mapped[str | None] = mapped_column(String(255), unique=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(64), unique=True)
    role: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ad_free_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    connections: Mapped[list["AuthConnection"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    routes: Mapped[list["Route"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    registration_requests: Mapped[list["UserRegistrationRequest"]] = relationship(
        back_populates="reviewed_by_user",
        cascade="save-update",
        foreign_keys=lambda: [UserRegistrationRequest.reviewed_by_user_id],
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserRegistrationRequest(Base):
    __tablename__ = "user_registration_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_type: Mapped[str] = mapped_column(String(50), nullable=False, default="new_user")
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    reviewed_by_user: Mapped["User | None"] = relationship(
        foreign_keys=lambda: [UserRegistrationRequest.reviewed_by_user_id],
        back_populates="registration_requests",
    )


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
    publication_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    publication_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_payments_provider_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    account_email: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, default="premium_ad_free")
    amount_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(8))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    premium_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str | None] = mapped_column(String(32))
    raw_payload: Mapped[dict | list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="payments")


class CoverageSnapshot(Base):
    __tablename__ = "coverage_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signature: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


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


class OfficialSkateRule(Base):
    __tablename__ = "official_skate_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    meeting_point_key: Mapped[str] = mapped_column(String(80), nullable=False)
    start_time: Mapped[str | None] = mapped_column(String(8))
    end_time: Mapped[str | None] = mapped_column(String(8))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (UniqueConstraint("calendar_id", "external_id", name="uq_calendar_event_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(512))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CalendarSyncState(Base):
    __tablename__ = "calendar_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    sync_token: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class OfficialSkateCandidate(Base):
    __tablename__ = "official_skate_candidates"
    __table_args__ = (UniqueConstraint("route_id", name="uq_official_candidate_route"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    calendar_event_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_events.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="needs_review")
    reasons: Mapped[list | None] = mapped_column(JSON)
    meeting_point_key: Mapped[str | None] = mapped_column(String(80))
    segment_start_index: Mapped[int | None] = mapped_column(Integer)
    segment_end_index: Mapped[int | None] = mapped_column(Integer)
    owner_publication_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notification_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    route: Mapped["Route"] = relationship()
    owner: Mapped["User"] = relationship(foreign_keys=[owner_user_id])
    calendar_event: Mapped["CalendarEvent | None"] = relationship()


class OfficialSkateSegment(Base):
    __tablename__ = "official_skate_segments"
    __table_args__ = (UniqueConstraint("candidate_id", name="uq_official_segment_candidate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("official_skate_candidates.id", ondelete="CASCADE"), nullable=False)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"), nullable=False)
    public_name: Mapped[str | None] = mapped_column(String(255))
    points: Mapped[list] = mapped_column(JSON, nullable=False)
    distance_m: Mapped[float | None] = mapped_column(Float)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    candidate: Mapped["OfficialSkateCandidate"] = relationship()


class OfficialSkateDecision(Base):
    __tablename__ = "official_skate_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("official_skate_candidates.id", ondelete="CASCADE"), nullable=False)
    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    old_start_index: Mapped[int | None] = mapped_column(Integer)
    old_end_index: Mapped[int | None] = mapped_column(Integer)
    new_start_index: Mapped[int | None] = mapped_column(Integer)
    new_end_index: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VerifiedSurfaceArea(Base):
    __tablename__ = "verified_surface_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    surface: Mapped[str] = mapped_column(String(80), nullable=False)
    polygon: Mapped[list] = mapped_column(JSON, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
