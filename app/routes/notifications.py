"""Notification list + Pusher private-channel auth."""
from flask import Blueprint, render_template, redirect, url_for, request, jsonify
from flask_login import login_required, current_user
from ..models import Notification, Message
from ..extensions import db
from ..pusher import get_pusher_client
from ..notifications import user_channel

bp = Blueprint('notifications', __name__)


@bp.route('/notifications')
@login_required
def index():
    notifs = Notification.query.filter_by(user_id=current_user.id)\
        .order_by(Notification.created_at.desc()).limit(50).all()
    return render_template('notifications/index.html', notifs=notifs)


@bp.route('/notifications/<int:id>/read', methods=['POST'])
@login_required
def read(id):
    n = Notification.query.get_or_404(id)
    if n.user_id != current_user.id:
        return redirect(url_for('notifications.index'))
    n.is_read = 1
    db.session.commit()
    return redirect(n.link or url_for('notifications.index'))


@bp.route('/notifications/<int:id>/open')
@login_required
def open(id):
    n = Notification.query.get_or_404(id)
    if n.user_id != current_user.id:
        return redirect(url_for('notifications.index'))
    n.is_read = 1
    db.session.commit()
    return redirect(n.link or url_for('notifications.index'))


@bp.route('/notifications/read-all', methods=['POST'])
@login_required
def read_all():
    Notification.query.filter_by(user_id=current_user.id, is_read=0)\
        .update({'is_read': 1}, synchronize_session=False)
    db.session.commit()
    return redirect(url_for('notifications.index'))


@bp.route('/notifications/badge')
@login_required
def badge():
    return jsonify({
        'notif': Notification.query.filter_by(user_id=current_user.id, is_read=0).count(),
        'msg': Message.query.filter_by(receiver_id=current_user.id, is_read=0).count()
    })


@bp.route('/pusher/auth', methods=['POST'])
@login_required
def pusher_auth():
    channel = request.form.get('channel_name', '')
    socket_id = request.form.get('socket_id', '')
    if channel != user_channel(current_user.id):
        return jsonify({'error': 'forbidden'}), 403
    client = get_pusher_client()
    if not client:
        return jsonify({'error': 'pusher not configured'}), 503
    return jsonify(client.authenticate(channel=channel, socket_id=socket_id))
