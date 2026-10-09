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


class Monitor(db.Model):
    """A user-owned website monitoring target."""

    __tablename__ = "monitors"
    __table_args__ = (db.UniqueConstraint("user_id", "url", name="uq_monitors_user_url"),)

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(2048), nullable=False)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    last_status = db.Column(db.String(12), nullable=True)
    last_checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    checks = db.relationship(
        "CheckResult", back_populates="monitor", cascade="all, delete-orphan"
    )

    def public_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "url": self.url,
            "is_enabled": self.is_enabled, "last_status": self.last_status,
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
