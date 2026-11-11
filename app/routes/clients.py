import os
import uuid
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
from flask_login import login_required, login_user, current_user
from werkzeug.utils import secure_filename
from ..models import User, Appointment, Receipt
from ..extensions import db
from ..audit import log
from .appointments import _txn_row

bp = Blueprint('clients', __name__, url_prefix='/clients')


def _allowed_image(filename):
    return secure_filename(filename.rsplit('.', 1)[-1].lower()) in ('png', 'jpg', 'jpeg', 'gif')


def _save_image(image):
    if not image or not image.filename:
        return None
    ext = secure_filename(image.filename.rsplit('.', 1)[-1].lower())
    if ext not in ('png', 'jpg', 'jpeg', 'gif'):
        return None
    image_filename = f"{uuid.uuid4().hex}.{ext}"
    upload_dir = os.path.join(current_app.root_path, '..', 'assets', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    image.save(os.path.join(upload_dir, image_filename))
    return image_filename


def _staff_only():
    if current_user.role not in ['superadmin', 'staff']:
        flash('Access denied.', 'danger')
        return True
    return False


@bp.route('/')
@login_required
def index():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    clients = User.query.filter_by(role='client').order_by(User.created_at.desc()).all()
    return render_template('clients/index.html', clients=clients)


@bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    if request.method == 'POST':
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        middle_name = request.form.get('middle_name', '')
        name = ' '.join(filter(None, [first_name, middle_name, last_name]))
        image_filename = _save_image(request.files.get('image'))
        u = User(
            role='client',
            status=1,
            name=name,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            birthday=datetime.strptime(request.form['birthday'], '%Y-%m-%d').date() if request.form.get('birthday') else None,
            gender=request.form.get('gender'),
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            address=request.form.get('address'),
            image=image_filename
        )
        # clients are walk-in, no login; placeholder password
        u.set_password(str(uuid.uuid4()))
        db.session.add(u)
        db.session.commit()
        flash('Client added.', 'success')
        return redirect(url_for('clients.index'))
    return render_template('clients/form.html', client=None, action=url_for('clients.add'))


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    c = User.query.get_or_404(id)
    if request.method == 'POST':
        c.first_name = request.form['first_name']
        c.last_name = request.form['last_name']
        c.middle_name = request.form.get('middle_name', '')
        c.name = ' '.join(filter(None, [c.first_name, c.middle_name, c.last_name]))
        c.birthday = datetime.strptime(request.form['birthday'], '%Y-%m-%d').date() if request.form.get('birthday') else None
        c.gender = request.form.get('gender')
        c.email = request.form.get('email')
        c.phone = request.form.get('phone')
        c.address = request.form.get('address')
        image_filename = _save_image(request.files.get('image'))
        if image_filename:
            c.image = image_filename
        db.session.commit()
        flash('Client updated.', 'success')
        return redirect(url_for('clients.index'))
    return render_template('clients/form.html', client=c, action=url_for('clients.edit', id=c.id))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    c = User.query.get_or_404(id)
    db.session.delete(c)
    db.session.commit()
    flash('Client deleted.', 'success')
    return redirect(url_for('clients.index'))


@bp.route('/<int:id>')
@login_required
def view(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    c = User.query.filter_by(id=id, role='client').first_or_404()
    appts = (Appointment.query.filter_by(client_id=id)
             .order_by(Appointment.appointment_date.desc()).all())
    receipts = (Receipt.query.join(Appointment)
                .filter(Appointment.client_id == id)
                .order_by(Receipt.created_at.desc()).all())
    rows = [_txn_row(r) for r in receipts]
    return render_template('clients/view.html', c=c, appts=appts, rows=rows)


@bp.route('/<int:id>/view-as')
@login_required
def view_as(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    c = User.query.filter_by(id=id, role='client').first_or_404()
    if c.status != 1:
        flash('That account is not active — it cannot be previewed.', 'warning')
        return redirect(url_for('clients.view', id=id))
    session['impersonator_id'] = current_user.id
    login_user(c)
    log('user.view_as', 'Viewing as client %s' % c.email)
    return redirect(url_for('dashboard.index'))


@bp.route('/exit-preview')
@login_required
def exit_preview():
    admin_id = session.pop('impersonator_id', None)
    if not admin_id:
        return redirect(url_for('dashboard.index'))
    admin = User.query.get(admin_id)
    if not admin:
        return redirect(url_for('auth.logout'))
    login_user(admin)
    log('user.exit_view', 'Returned to admin account')
    flash('Back to your admin account.', 'info')
    return redirect(url_for('clients.index'))
