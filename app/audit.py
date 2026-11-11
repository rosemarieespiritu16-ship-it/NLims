from .extensions import db
from .models import AuditLog


def log(action, details=''):
    """Record an audit trail entry. Never breaks the calling request."""
    try:
        from flask import request
        from flask_login import current_user
        entry = AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            user_name=((current_user.full_name or current_user.name)
                       if current_user.is_authenticated else 'Anonymous'),
            action=action,
            details=details,
            ip=request.remote_addr or '')
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
