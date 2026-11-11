"""In-app notification + realtime push helpers."""
from .models import Notification, User
from .extensions import db
from .pusher import pusher_trigger


def user_channel(user_id):
    return 'private-user-%s' % user_id


def push_to(user_id, event, data):
    pusher_trigger(user_channel(user_id), event, data)


def notify(user_id, type, title, body, link=None):
    n = Notification(user_id=user_id, type=type, title=title, body=body, link=link)
    db.session.add(n)
    db.session.commit()
    push_to(user_id, 'notification', {
        'id': n.id,
        'type': n.type,
        'title': n.title,
        'body': n.body,
        'link': n.link or '',
        'when': n.created_at.strftime('%b %d, %I:%M %p') if n.created_at else ''
    })
    return n


def notify_staff(type, title, body, link=None):
    staff = User.query.filter(User.role.in_(['superadmin', 'staff']), User.status == 1).all()
    for u in staff:
        notify(u.id, type, title, body, link)
