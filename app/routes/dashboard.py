from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Receipt, Appointment, User

bp = Blueprint('dashboard', __name__)


@bp.route('/')
@login_required
def index():
    if current_user.role == 'client':
        upcoming = Appointment.query.filter(
            Appointment.client_id == current_user.id,
            Appointment.appointment_date >= date.today(),
            Appointment.status.in_(['pending', 'confirmed'])
        ).order_by(Appointment.appointment_date.asc()).limit(8).all()
        pending_count = Appointment.query.filter_by(
            client_id=current_user.id, status='pending').count()
        completed_count = Appointment.query.filter_by(
            client_id=current_user.id, status='completed').count()
        return render_template('dashboard_client.html',
                               upcoming=upcoming,
                               next_appt=upcoming[0] if upcoming else None,
                               pending_count=pending_count,
                               completed_count=completed_count)
    return render_template('dashboard.html')


@bp.route('/data')
@login_required
def data():
    if current_user.role == 'client':
        return jsonify({'error': 'forbidden'}), 403

    try:
        days = int(request.args.get('range', 30))
    except (TypeError, ValueError):
        days = 30
    if days not in (7, 30, 90):
        days = 30

    today = date.today()
    since = datetime.combine(today - timedelta(days=days - 1), datetime.min.time())
    since_date = since.date()

    receipts = Receipt.query.filter(Receipt.created_at >= since).all()
    appts_in_range = Appointment.query.filter(Appointment.created_at >= since).all()

    # --- Revenue trend (every day in range, zero-filled) ---
    trend = {}
    for i in range(days - 1, -1, -1):
        trend[(today - timedelta(days=i)).isoformat()] = 0.0
    for r in receipts:
        if r.created_at:
            key = r.created_at.date().isoformat()
            if key in trend:
                trend[key] += float(r.total or 0)

    # --- Payment method split ---
    methods = {'cash': 0.0, 'gcash': 0.0}
    method_counts = {'cash': 0, 'gcash': 0}
    for r in receipts:
        m = (r.payment_method or 'cash').lower()
        methods[m] = methods.get(m, 0.0) + float(r.total or 0)
        method_counts[m] = method_counts.get(m, 0) + 1

    # --- Top services / packages (revenue from items on paid receipts) ---
    item_rev = {}
    item_qty = {}
    worker_rev = {}
    worker_count = {}
    for r in receipts:
        appt = r.appointment
        if not appt:
            continue
        for i in appt.items:
            name = i.service.name if i.item_type == 'service' and i.service else \
                   i.package.name if i.item_type == 'package' and i.package else 'Other'
            key = (name, i.item_type)
            item_rev[key] = item_rev.get(key, 0.0) + float(i.price or 0) * i.quantity
            item_qty[key] = item_qty.get(key, 0) + i.quantity
        wname = appt.worker.name if appt.worker else 'Unassigned'
        worker_rev[wname] = worker_rev.get(wname, 0.0) + float(r.total or 0)
        worker_count[wname] = worker_count.get(wname, 0) + 1

    top_items = sorted(
        [{'name': k[0], 'type': k[1], 'revenue': v, 'qty': item_qty[k]} for k, v in item_rev.items()],
        key=lambda x: x['revenue'], reverse=True
    )[:8]
    workers = sorted(
        [{'name': k, 'revenue': v, 'count': worker_count[k]} for k, v in worker_rev.items()],
        key=lambda x: x['revenue'], reverse=True
    )[:8]

    # --- Status breakdown + walk-in split ---
    status = {'pending': 0, 'confirmed': 0, 'draft': 0, 'completed': 0, 'cancelled': 0, 'voided': 0}
    split = {'walkin': 0, 'client': 0}
    for a in appts_in_range:
        if a.status in status:
            status[a.status] += 1
        split['walkin' if a.is_walkin else 'client'] += 1

    # --- KPIs ---
    total_clients = User.query.filter_by(role='client', status=1).count()
    today_appts = Appointment.query.filter(
        Appointment.appointment_date == today,
        ~Appointment.status.in_(['cancelled', 'voided'])
    ).count()

    # --- Lists ---
    recent = Receipt.query.order_by(Receipt.created_at.desc()).limit(5).all()
    upcoming = Appointment.query.filter(
        Appointment.appointment_date >= today,
        Appointment.status.in_(['pending', 'confirmed'])
    ).order_by(Appointment.appointment_date.asc()).limit(8).all()

    return jsonify({
        'days': days,
        'kpis': {
            'revenue': sum(float(r.total or 0) for r in receipts),
            'transactions': len(receipts),
            'clients': total_clients,
            'today_appts': today_appts
        },
        'revenue_trend': {'labels': list(trend.keys()), 'data': list(trend.values())},
        'methods': {
            'labels': [m.upper() for m in methods],
            'data': [methods[m] for m in methods],
            'counts': [method_counts[m] for m in methods]
        },
        'top_items': {
            'labels': [x['name'] for x in top_items],
            'data': [x['revenue'] for x in top_items],
            'types': [x['type'] for x in top_items],
            'qty': [x['qty'] for x in top_items]
        },
        'workers': {
            'labels': [w['name'] for w in workers],
            'data': [w['revenue'] for w in workers],
            'counts': [w['count'] for w in workers]
        },
        'status': {'labels': [k.capitalize() for k in status], 'data': list(status.values())},
        'split': {'labels': ['Walk-in', 'Client'], 'data': [split['walkin'], split['client']]},
        'recent': [{
            'id': r.id,
            'code': r.transaction_number,
            'total': float(r.total or 0),
            'method': (r.payment_method or '').upper(),
            'when': r.created_at.strftime('%b %d, %Y %I:%M %p') if r.created_at else ''
        } for r in recent],
        'upcoming': [{
            'date': a.appointment_date.strftime('%b %d, %Y') if a.appointment_date else '',
            'client': a.client.full_name if a.client else 'Walk-in',
            'worker': a.worker.name if a.worker else '-',
            'code': a.appointment_code or '-',
            'status': a.status
        } for a in upcoming]
    })
