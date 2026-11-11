"""Admin salon schedule / daily availability."""
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..models import SalonAvailability
from ..extensions import db

bp = Blueprint('schedule', __name__, url_prefix='/schedule')


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
    start = request.args.get('start', date.today().strftime('%Y-%m-%d'))
    start_obj = datetime.strptime(start, '%Y-%m-%d').date()
    end_obj = start_obj + timedelta(days=30)
    rows = SalonAvailability.query.filter(
        SalonAvailability.date >= start_obj,
        SalonAvailability.date <= end_obj
    ).order_by(SalonAvailability.date).all()
    prev_start = (start_obj - timedelta(days=30)).strftime('%Y-%m-%d')
    next_start = (start_obj + timedelta(days=30)).strftime('%Y-%m-%d')
    return render_template('schedule/index.html', rows=rows, start=start, prev_start=prev_start, next_start=next_start)


@bp.route('/add', methods=['POST'])
@login_required
def add():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    d = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
    slot_type = request.form['slot_type']
    max_slots = int(request.form['max_slots'])

    existing = SalonAvailability.query.filter_by(date=d, slot_type=slot_type).first()
    if existing:
        existing.max_slots = max_slots
        flash('Availability updated.', 'success')
    else:
        sa = SalonAvailability(date=d, slot_type=slot_type, max_slots=max_slots, used_slots=0)
        db.session.add(sa)
        flash('Availability added.', 'success')
    db.session.commit()
    return redirect(url_for('schedule.index'))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    sa = SalonAvailability.query.get_or_404(id)
    db.session.delete(sa)
    db.session.commit()
    flash('Availability removed.', 'success')
    return redirect(url_for('schedule.index'))
