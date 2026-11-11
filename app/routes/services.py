from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..models import Service
from ..extensions import db
from ..audit import log

bp = Blueprint('services', __name__, url_prefix='/services')


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
    services = Service.query.order_by(Service.category, Service.name).all()
    return render_template('services/index.html', services=services)


@bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    if request.method == 'POST':
        s = Service(
            name=request.form['name'],
            category=request.form['category'],
            duration_minutes=int(request.form['duration_minutes']),
            price=float(request.form['price']),
            status=int(request.form['status'])
        )
        db.session.add(s)
        db.session.commit()
        log('catalog.create', 'Added service %s' % s.name)
        flash('Service added.', 'success')
        return redirect(url_for('services.index'))
    return render_template('services/form.html', service=None, action=url_for('services.add'))


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    s = Service.query.get_or_404(id)
    if request.method == 'POST':
        s.name = request.form['name']
        s.category = request.form['category']
        s.duration_minutes = int(request.form['duration_minutes'])
        s.price = float(request.form['price'])
        s.status = int(request.form['status'])
        db.session.commit()
        log('catalog.edit', 'Updated service %s' % s.name)
        flash('Service updated.', 'success')
        return redirect(url_for('services.index'))
    return render_template('services/form.html', service=s, action=url_for('services.edit', id=s.id))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    s = Service.query.get_or_404(id)
    name = s.name
    db.session.delete(s)
    db.session.commit()
    log('catalog.delete', 'Deleted service %s' % name)
    flash('Service deleted.', 'success')
    return redirect(url_for('services.index'))
