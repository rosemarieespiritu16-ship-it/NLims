import random
import string
from flask import Blueprint, render_template, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from ..models import User
from ..extensions import db
from ..mail_helper import send_email
from ..pusher import pusher_trigger
from ..notifications import notify
from ..audit import log

bp = Blueprint('users', __name__, url_prefix='/users')


@bp.route('/')
@login_required
def index():
    if current_user.role not in ['superadmin', 'staff']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard.index'))
    users = User.query.filter(User.role != 'client').order_by(User.created_at.desc()).all()
    return render_template('users/index.html', users=users)


@bp.route('/<int:id>/toggle')
@login_required
def toggle(id):
    if current_user.role not in ['superadmin', 'staff']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard.index'))
    u = User.query.get_or_404(id)
    was_pending = u.status == 0
    u.status = 1 if was_pending else 0

    if was_pending:
        raw_password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        u.set_password(raw_password)
        u.must_change_password = 1
        db.session.commit()

        app_url = current_app.config.get('APP_URL', 'http://localhost:5000')
        sender = current_app.config.get('MAIL_DEFAULT_SENDER')

        if sender:
            try:
                html = render_template(
                    'emails/client_approved.html',
                    name=u.first_name or u.name,
                    password=raw_password,
                    app_url=app_url
                )
                send_email(
                    recipients=[u.email],
                    subject='InventorPro Account Approved',
                    html_body=html
                )
                flash('Client approved and password emailed.', 'success')
            except Exception as e:
                flash('Client approved, but password email failed: ' + str(e), 'danger')
        else:
            flash(f'Client approved. Temporary password: {raw_password}', 'success')

        pusher_trigger('users', 'client-approved', {
            'user_id': u.id,
            'email': u.email,
            'name': u.name
        })
        notify(u.id, 'system', 'Account Approved',
               'Your InventorPro account has been approved. You can now log in and book appointments.',
               link=url_for('booking.new'))
        log('user.approve', 'Approved account %s' % u.email)
    else:
        db.session.commit()
        log('user.toggle', 'Set %s status to disabled' % u.email)
        flash('User status updated.', 'success')

    return redirect(url_for('users.index'))
