from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from datetime import date, datetime
from ..models import Worker, Appointment, Receipt
from ..extensions import db
from ..audit import log
from .appointments import _txn_row

bp = Blueprint('workers', __name__, url_prefix='/workers')


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
    workers = Worker.query.order_by(Worker.name).all()
    busy = {}
    for w in workers:
        busy[w.id] = Appointment.query.filter_by(worker_id=w.id).filter(
            Appointment.status.in_(['draft', 'confirmed'])
        ).first()
    return render_template('workers/index.html', workers=workers, busy=busy)


@bp.route('/add', methods=['POST'])
@login_required
def add():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    w = Worker(
        name=request.form['name'],
        email=request.form.get('email'),
        phone=request.form.get('phone'),
        specialty=request.form.get('specialty', ''),
        notes=request.form.get('notes', ''),
        status=1
    )
    db.session.add(w)
    db.session.commit()
    log('worker.add', 'Added worker %s' % w.name)
    flash('Worker added.', 'success')
    return redirect(url_for('workers.index'))


@bp.route('/<int:id>/ledger')
@login_required
def ledger(id):
    """Per-worker ledger: clients served + revenue per day / week / month."""
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    worker = Worker.query.get_or_404(id)
    receipts = (Receipt.query.join(Appointment)
                .filter(Appointment.worker_id == id)
                .order_by(Receipt.created_at.desc()).all())

    # --- filters (breakdowns + table) ---
    method = request.args.get('method', 'all')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    try:
        df = datetime.strptime(date_from, '%Y-%m-%d').date() if date_from else None
    except ValueError:
        df = date_from = None
    try:
        dt = datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else None
    except ValueError:
        dt = date_to = None
    filtered = [r for r in receipts
                if (method not in ('cash', 'gcash') or r.payment_method == method)
                and (not df or (r.created_at and r.created_at.date() >= df))
                and (not dt or (r.created_at and r.created_at.date() <= dt))]

    today = date.today()
    cur_year, cur_week, _ = today.isocalendar()
    cur_month = today.strftime('%Y-%m')

    def bucket(key_fn):
        out = {}
        for r in filtered:
            if not r.created_at:
                continue
            k = key_fn(r.created_at)
            b = out.setdefault(k, {'clients': 0, 'total': 0.0})
            b['clients'] += 1
            b['total'] += float(r.total or 0)
        return out

    daily = bucket(lambda c: c.date())
    weekly = bucket(lambda c: '{}-W{:02d}'.format(*c.isocalendar()[:2]))
    monthly = bucket(lambda c: c.strftime('%Y-%m'))

    today_stats = daily.get(today, {'clients': 0, 'total': 0.0})
    week_stats = weekly.get('{}-W{:02d}'.format(cur_year, cur_week),
                            {'clients': 0, 'total': 0.0})
    month_stats = monthly.get(cur_month, {'clients': 0, 'total': 0.0})
    grand = {'clients': len(receipts),
             'total': sum(float(r.total or 0) for r in receipts)}

    return render_template('workers/ledger.html', worker=worker,
                           rows=[_txn_row(r) for r in filtered],
                           today=today_stats, week=week_stats,
                           month=month_stats, grand=grand,
                           method=method, date_from=date_from, date_to=date_to,
                           daily=sorted(daily.items(), key=lambda kv: kv[0], reverse=True),
                           weekly=sorted(weekly.items(), key=lambda kv: kv[0], reverse=True),
                           monthly=sorted(monthly.items(), key=lambda kv: kv[0], reverse=True))


@bp.route('/<int:id>/edit', methods=['POST'])
@login_required
def edit(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    w = Worker.query.get_or_404(id)
    w.name = request.form.get('name', w.name)
    w.email = request.form.get('email', w.email)
    w.phone = request.form.get('phone', w.phone)
    w.specialty = request.form.get('specialty', w.specialty)
    w.notes = request.form.get('notes', w.notes)
    db.session.commit()
    log('worker.edit', 'Updated worker %s' % w.name)
    flash('Worker updated.', 'success')
    return redirect(url_for('workers.index'))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    w = Worker.query.get_or_404(id)
    name = w.name
    db.session.delete(w)
    db.session.commit()
    log('worker.delete', 'Removed worker %s' % name)
    flash('Worker removed.', 'success')
    return redirect(url_for('workers.index'))
