from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..extensions import db
from ..models import AuditLog, User

bp = Blueprint('audit', __name__, url_prefix='/audit')

ACTION_GROUPS = {
    'auth': 'Authentication',
    'profile': 'Profile',
    'booking': 'Bookings',
    'appt': 'Appointments',
    'pos': 'POS / Transactions',
    'walkin': 'Walk-ins',
    'catalog': 'Catalog',
    'worker': 'Workers',
    'user': 'Users',
    'settings': 'Settings',
    'stock': 'Stock',
}


@bp.route('/')
@login_required
def index():
    if current_user.role not in ['superadmin', 'staff']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard.index'))

    user_id = request.args.get('user_id', type=int)
    action = request.args.get('action', 'all')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = request.args.get('page', 1, type=int)

    query = AuditLog.query.outerjoin(User, AuditLog.user_id == User.id)
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    if action != 'all':
        query = query.filter(AuditLog.action.like(action + '.%'))
    try:
        df = datetime.strptime(date_from, '%Y-%m-%d').date() if date_from else None
        if df:
            query = query.filter(db.func.date(AuditLog.created_at) >= df)
    except ValueError:
        date_from = ''
    try:
        dt = datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else None
        if dt:
            query = query.filter(db.func.date(AuditLog.created_at) <= dt)
    except ValueError:
        date_to = ''

    logs = query.order_by(AuditLog.created_at.desc()).all()
    per_page = 20
    total = len(logs)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    page_logs = logs[(page - 1) * per_page: page * per_page]

    users_with_logs = (User.query.join(AuditLog, AuditLog.user_id == User.id)
                       .distinct().order_by(User.name).all())

    return render_template('audit/index.html',
                           logs=page_logs, users=users_with_logs,
                           user_id=user_id, action=action,
                           groups=ACTION_GROUPS,
                           date_from=date_from, date_to=date_to,
                           page=page, total_pages=total_pages, total=total)
