"""Central audit recording with deliberately small, non-sensitive fields."""
from flask import has_request_context, request
from models import db


def record(action, object_type, object_id=None, *, actor_type="system", actor_id=None, outcome="success"):
    from models import AuditEvent

    request_id = request.headers.get("X-Request-ID") if has_request_context() else None
    event = AuditEvent(
        action=action, object_type=object_type, object_id=object_id,
        actor_type=actor_type, actor_id=actor_id, outcome=outcome,
        request_id=request_id[:64] if request_id else None,
    )
    db.session.add(event)
    return event
