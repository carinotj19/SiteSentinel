"""Database entities for SiteSentinel."""

from datetime import datetime, timezone
from uuid import uuid4

from werkzeug.security import check_password_hash, generate_password_hash

from sitesentinel.extensions import db


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    display_name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # Bumping this number invalidates all previously issued access/refresh JWTs.
    auth_version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="scrypt")

    def verify_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "created_at": _utc_timestamp(self.created_at),
            "updated_at": _utc_timestamp(self.updated_at),
        }


class ConsumedRefreshToken(db.Model):
    """Persistent refresh token rotation: a JWT jti can only be refreshed once."""

    __tablename__ = "consumed_refresh_tokens"

    jti = db.Column(db.String(36), primary_key=True)
    consumed_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class Workspace(db.Model):
    """An organization or personal workspace; UUIDs are not authorization."""

    __tablename__ = "workspaces"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    name = db.Column(db.String(100), nullable=False)
    created_by_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    personal_owner_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), unique=True, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    def public_dict(self, role: str) -> dict:
        return {
            "id": self.id, "name": self.name, "role": role,
            "is_personal": self.personal_owner_user_id is not None,
            "created_at": _utc_timestamp(self.created_at),
        }


class Membership(db.Model):
    """A unique user/workspace role assignment."""

    __tablename__ = "workspace_memberships"
    __table_args__ = (db.UniqueConstraint("workspace_id", "user_id", name="uq_membership_workspace_user"),)

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id = db.Column(db.String(36), db.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = db.Column(db.String(12), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    def public_dict(self) -> dict:
        user = db.session.get(User, self.user_id)
        return {"user_id": self.user_id, "email": user.email if user else None,
                "display_name": user.display_name if user else None, "role": self.role,
                "created_at": _utc_timestamp(self.created_at)}


class Invitation(db.Model):
    """A hashed, single-use invitation. Raw token is never stored."""

    __tablename__ = "workspace_invitations"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id = db.Column(db.String(36), db.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(12), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    invited_by_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def public_dict(self) -> dict:
        return {"id": self.id, "email": self.email, "role": self.role,
                "expires_at": _utc_timestamp(self.expires_at),
                "accepted_at": _utc_timestamp(self.accepted_at) if self.accepted_at else None,
                "revoked_at": _utc_timestamp(self.revoked_at) if self.revoked_at else None}


class Monitor(db.Model):
    """A user-owned website monitoring target."""

    __tablename__ = "monitors"
    __table_args__ = (db.UniqueConstraint("workspace_id", "url", name="uq_monitors_workspace_url"),)

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id = db.Column(db.String(36), db.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(2048), nullable=False)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    last_status = db.Column(db.String(12), nullable=True)
    last_checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    interval_seconds = db.Column(db.Integer, nullable=False, default=300)
    next_check_at = db.Column(db.DateTime(timezone=True), nullable=True)
    failure_streak = db.Column(db.Integer, nullable=False, default=0)
    lease_token = db.Column(db.String(36), nullable=True)
    lease_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    checks = db.relationship("CheckResult", back_populates="monitor", cascade="all, delete-orphan")
    incidents = db.relationship("Incident", back_populates="monitor", cascade="all, delete-orphan")
    alerts = db.relationship("AlertEvent", back_populates="monitor", cascade="all, delete-orphan")

    def public_dict(self) -> dict:
        return {
            "id": self.id, "workspace_id": self.workspace_id, "name": self.name, "url": self.url,
            "is_enabled": self.is_enabled, "last_status": self.last_status,
            "interval_seconds": self.interval_seconds,
            "next_check_at": _utc_timestamp(self.next_check_at) if self.next_check_at else None,
            "failure_streak": self.failure_streak,
            "last_checked_at": _utc_timestamp(self.last_checked_at) if self.last_checked_at else None,
            "created_at": _utc_timestamp(self.created_at),
            "updated_at": _utc_timestamp(self.updated_at),
        }


class CheckResult(db.Model):
    """Immutable history of individual monitor checks."""

    __tablename__ = "check_results"
    __table_args__ = (db.Index("ix_check_results_monitor_checked", "monitor_id", "checked_at"),)

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    monitor_id = db.Column(
        db.String(36), db.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False
    )
    checked_at = db.Column(db.DateTime(timezone=True), nullable=False)
    checked_url = db.Column(db.String(2048), nullable=False)
    status = db.Column(db.String(12), nullable=False)  # up, down, blocked
    status_code = db.Column(db.Integer, nullable=True)
    response_time_ms = db.Column(db.Integer, nullable=True)
    certificate_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    final_url = db.Column(db.String(2048), nullable=True)
    error = db.Column(db.String(500), nullable=True)
    monitor = db.relationship("Monitor", back_populates="checks")

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "monitor_id": self.monitor_id,
            "checked_at": _utc_timestamp(self.checked_at),
            "checked_url": self.checked_url,
            "status": self.status,
            "status_code": self.status_code,
            "response_time_ms": self.response_time_ms,
            "certificate_expires_at": (
                _utc_timestamp(self.certificate_expires_at) if self.certificate_expires_at else None
            ),
            "final_url": self.final_url,
            "error": self.error,
        }


class Incident(db.Model):
    """A sustained outage opened after consecutive failed checks."""

    __tablename__ = "incidents"
    __table_args__ = (db.Index("ix_incidents_monitor_resolved", "monitor_id", "resolved_at"),)

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    monitor_id = db.Column(db.String(36), db.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    opened_at = db.Column(db.DateTime(timezone=True), nullable=False)
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reason = db.Column(db.String(500), nullable=True)
    monitor = db.relationship("Monitor", back_populates="incidents")

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "monitor_id": self.monitor_id,
            "opened_at": _utc_timestamp(self.opened_at),
            "resolved_at": _utc_timestamp(self.resolved_at) if self.resolved_at else None,
            "reason": self.reason,
            "status": "resolved" if self.resolved_at else "open",
        }


class AlertEvent(db.Model):
    """Auditable notification event. Delivery occurs in the background worker."""

    __tablename__ = "alert_events"
    __table_args__ = (db.Index("ix_alert_events_monitor_created", "monitor_id", "created_at"),)

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    monitor_id = db.Column(db.String(36), db.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    incident_id = db.Column(db.String(36), db.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    kind = db.Column(db.String(16), nullable=False)  # down, recovered
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    delivery_status = db.Column(db.String(16), nullable=False, default="pending")
    attempts = db.Column(db.Integer, nullable=False, default=0)
    next_attempt_at = db.Column(db.DateTime(timezone=True), nullable=True)
    delivered_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_error = db.Column(db.String(400), nullable=True)
    monitor = db.relationship("Monitor", back_populates="alerts")
    incident = db.relationship("Incident")

    def public_dict(self) -> dict:
        return {
            "id": self.id, "monitor_id": self.monitor_id,
            "incident_id": self.incident_id, "kind": self.kind,
            "created_at": _utc_timestamp(self.created_at),
            "delivery_status": self.delivery_status, "attempts": self.attempts,
            "delivered_at": _utc_timestamp(self.delivered_at) if self.delivered_at else None,
        }
