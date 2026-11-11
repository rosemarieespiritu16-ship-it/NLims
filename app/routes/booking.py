"""Booking routes for clients."""
import uuid
from datetime import datetime, date, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app, session, jsonify)
from flask_login import login_required, current_user
from ..models import (Appointment, SalonAvailability, AppointmentItem,
                      AppointmentAddon, Package, Service, Product,
                      Message, User)
from ..extensions import db
from ..pusher import pusher_trigger
from ..mail_helper import send_email
from ..notifications import notify_staff, push_to
from ..paymongo import create_payment_link, is_link_paid
from ..audit import log
from .settings import reservation_fee_for, get_setting

bp = Blueprint('booking', __name__, url_prefix='/booking')


@bp.before_request
def _block_impersonated():
    """While an admin previews a client: bookings are view-only (no create/cancel/pay)."""
    if session.get('impersonator_id'):
        if request.method == 'GET' and request.endpoint in ('booking.index', 'booking.detail'):
            return None
        flash('Bookings are view-only while previewing a client.', 'info')
        return redirect(url_for('dashboard.index'))


def _interested_from_request():
    """Package/service the client flagged as 'interested' in chat."""
    pkg_id = request.values.get('package', type=int)
    svc_id = request.values.get('service', type=int)
    if pkg_id:
        p = Package.query.get(pkg_id)
        if p and p.status == 1:
            return {'type': 'package', 'id': p.id, 'name': p.name,
                    'price': float(p.price or 0), 'obj': p}
    if svc_id:
        s = Service.query.get(svc_id)
        if s and s.status == 1:
            return {'type': 'service', 'id': s.id, 'name': s.name,
                    'price': float(s.price or 0), 'obj': s}
    return None


def _post_interested_msg(appt, interested):
    """Post the 'Interested in X' card to the staff chat thread."""
    label = interested['name']
    body = 'Interested in %s: %s' % (interested['type'], label)
    staff = User.query.filter(
        User.role.in_(['superadmin', 'staff']), User.status == 1).all()
    if not staff:
        return
    gid = uuid.uuid4().hex
    mk = dict(sender_id=current_user.id, client_id=current_user.id,
              appointment_id=appt.id, group_id=gid, message=body,
              package_id=interested['id'] if interested['type'] == 'package' else None,
              service_id=interested['id'] if interested['type'] == 'service' else None)
    msgs = [Message(receiver_id=s.id, **mk) for s in staff]
    db.session.add_all(msgs)
    db.session.commit()
    payload = {'client_id': current_user.id,
               'sender': current_user.full_name or current_user.name,
               'sender_id': current_user.id,
               'message': body, 'gid': gid,
               'appt': {'id': appt.id, 'code': appt.appointment_code or '',
                        'date': appt.appointment_date.strftime('%b %d, %Y'),
                        'status': appt.status}}
    for s, m in zip(staff, msgs):
        p = dict(payload)
        p['id'] = m.id
        push_to(s.id, 'new-message', p)


def _collect_items():
    """Validate the picker's chosen items; prices always from the DB."""
    rows = []
    for t, i, q in zip(request.form.getlist('item_type'),
                       request.form.getlist('item_id'),
                       request.form.getlist('qty')):
        try:
            i, q = int(i), max(1, int(q))
        except (TypeError, ValueError):
            continue
        obj = None
        if t == 'service':
            obj = Service.query.get(i)
        elif t == 'package':
            obj = Package.query.get(i)
        elif t == 'product':
            obj = Product.query.get(i)
            if obj:
                q = min(q, max(0, obj.quantity or 0))
        if not obj or getattr(obj, 'status', 1) != 1 or q < 1:
            continue
        rows.append({'type': t, 'id': i, 'qty': q,
                     'price': float(obj.price or 0), 'name': obj.name})
    return rows, round(sum(r['price'] * r['qty'] for r in rows), 2)


def _add_items(appt, rows):
    for r in rows:
        if r['type'] == 'product':
            db.session.add(AppointmentAddon(
                appointment_id=appt.id, product_id=r['id'],
                quantity=r['qty'], price=r['price']))
        else:
            db.session.add(AppointmentItem(
                appointment_id=appt.id, item_type=r['type'],
                service_id=r['id'] if r['type'] == 'service' else None,
                package_id=r['id'] if r['type'] == 'package' else None,
                quantity=r['qty'], price=r['price']))


def _notify_new_booking(appt, paid=False):
    pusher_trigger('appointments', 'new-booking', {
        'client': current_user.full_name,
        'date': str(appt.appointment_date)})
    if paid:
        title = 'New Booking (Reservation Paid)'
        body = '%s paid a reservation fee and is confirmed for %s.' % (
            current_user.full_name, appt.appointment_date.strftime('%b %d, %Y'))
        status = 'confirmed'
    else:
        title = 'New Booking Request'
        body = '%s booked %s and is waiting for approval.' % (
            current_user.full_name, appt.appointment_date)
        status = 'pending'
    notify_staff('appointment', title, body,
                 link=url_for('appointments.index', status=status))


def _client_only():
    if current_user.role != 'client':
        flash('Only clients can access this page.', 'warning')
        return True
    return False


@bp.route('/')
@login_required
def index():
    if _client_only():
        return redirect(url_for('dashboard.index'))

    status_filter = request.args.get('status', 'my')
    query = Appointment.query.filter_by(client_id=current_user.id)

    if status_filter == 'my':
        query = query.filter(Appointment.status == 'pending')
    elif status_filter == 'approved':
        query = query.filter(Appointment.status == 'confirmed')
    elif status_filter == 'complete':
        query = query.filter(Appointment.status == 'completed')
    else:
        query = query.filter(Appointment.status.in_(['pending', 'confirmed', 'completed']))

    appts = query.order_by(Appointment.appointment_date.desc()).all()
    return render_template('booking/index.html', appts=appts, status_filter=status_filter)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    if _client_only():
        return redirect(url_for('dashboard.index'))

    today = date.today()
    end = today + timedelta(days=30)

    # dates where the client already has an active booking
    booked_dates = {a.appointment_date for a in
                    Appointment.query.filter(
                        Appointment.client_id == current_user.id,
                        Appointment.status.in_(['pending', 'confirmed']),
                        Appointment.appointment_date >= today).all()}

    slots = SalonAvailability.query.filter(
        SalonAvailability.date >= today,
        SalonAvailability.date <= end,
        SalonAvailability.slot_type == 'client'
    ).order_by(SalonAvailability.date).all()

    slots_dict = {}
    for s in slots:
        key = s.date.strftime('%Y-%m-%d')
        slots_dict[key] = {
            'date': str(s.date),
            'remaining': s.remaining,
            'max': s.max_slots
        }

    if request.method == 'POST':
        date_str = request.form['date']
        try:
            slot_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date.', 'danger')
            return redirect(url_for('booking.new'))

        if slot_date in booked_dates:
            flash('You already have an active booking on %s — pick another date.' %
                  slot_date.strftime('%b %d, %Y'), 'warning')
            return redirect(url_for('booking.new'))

        slot = SalonAvailability.query.filter_by(date=slot_date, slot_type='client').first()
        if not slot or slot.remaining <= 0:
            flash('No client slots available for that date.', 'danger')
            return redirect(url_for('booking.new'))

        rows, total = _collect_items()
        if not rows:
            flash('Please choose at least one service, package, or product.', 'warning')
            return redirect(url_for('booking.new'))

        interested = _interested_from_request()

        if request.form.get('payment') == 'gcash':
            fee = reservation_fee_for(total)
            if fee >= 1:
                session.pop('pay_done', None)
                session['pending_booking'] = {
                    'date': slot_date.isoformat(), 'items': rows, 'fee': fee,
                    'interested': ({'type': interested['type'], 'id': interested['id'],
                                    'name': interested['name']} if interested else None)}
                link = create_payment_link(
                    amount=fee,
                    description='InventorPro reservation fee - %s' % slot_date.strftime('%b %d, %Y'),
                    reference='RESERVE-%s-%s' % (current_user.id, uuid.uuid4().hex[:8]),
                    success_url=url_for('booking.gcash_success', _external=True),
                    failure_url=url_for('booking.new', _external=True))
                if not link.get('success'):
                    session.pop('pending_booking', None)
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({'error': 'GCash error: %s' % link.get('error', 'Unknown')}), 400
                    flash('GCash error: %s' % link.get('error', 'Unknown'), 'danger')
                    return redirect(url_for('booking.new'))
                session['pending_booking']['link_id'] = link['link_id']
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'checkout_url': link['checkout_url'], 'fee': fee})
                return redirect(link['checkout_url'])
            flash('Reservation fee is not configured. Your booking was submitted without payment.', 'info')

        # free booking -> pending, admin may approve or decline
        appt = Appointment(
            client_id=current_user.id,
            appointment_date=slot_date,
            status='pending',
            is_walkin=0
        )
        db.session.add(appt)
        slot.used_slots += 1
        db.session.flush()
        _add_items(appt, rows)
        db.session.commit()
        log('booking.create', 'New booking for %s — %d item(s)' % (
            appt.appointment_date.strftime('%b %d, %Y'), len(rows)))

        if interested:
            _post_interested_msg(appt, interested)

        _notify_new_booking(appt)

        sender = current_app.config.get('MAIL_DEFAULT_SENDER')
        if sender and current_user.email:
            try:
                send_email(
                    recipients=[current_user.email],
                    subject='Booking Received - InventorPro',
                    html_body=(
                        f'<p>Hi {current_user.full_name},</p>'
                        f'<p>Your booking for {appt.appointment_date} has been received.</p>'
                        f'<p>Status: <strong>Pending Approval</strong></p>'
                        f'<p>We will notify you once it is confirmed.</p>'
                    )
                )
            except Exception:
                pass

        flash('Booking submitted. Waiting for admin approval.', 'success')
        return redirect(url_for('booking.index', status='my'))

    session.pop('pending_booking', None)
    session.pop('pay_done', None)
    return render_template('booking/new.html',
                           slots_dict=slots_dict,
                           interested=_interested_from_request(),
                           services=Service.query.filter_by(status=1)
                                     .order_by(Service.category, Service.name).all(),
                           fee_mode=get_setting('reservation_mode', 'fixed'),
                           fee_amount=float(get_setting('reservation_amount', '150') or 0),
                           fee_percent=float(get_setting('reservation_percent', '50') or 50),
                           min_date=today.strftime('%Y-%m-%d'),
                           max_date=end.strftime('%Y-%m-%d'))


def _create_paid_booking(pending):
    """Create the confirmed appointment + items from a paid session intent."""
    slot_date = datetime.strptime(pending['date'], '%Y-%m-%d').date()
    slot = SalonAvailability.query.filter_by(date=slot_date, slot_type='client').first()
    if slot:
        slot.used_slots += 1

    appt = Appointment(
        client_id=current_user.id,
        appointment_date=slot_date,
        status='confirmed',
        is_walkin=0,
        reservation_fee=pending['fee'],
        reservation_method='gcash',
        reservation_reference=pending['link_id'],
        reservation_paid_at=datetime.now()
    )
    db.session.add(appt)
    db.session.flush()
    _add_items(appt, pending['items'])
    db.session.commit()
    log('booking.paid', 'Paid booking for %s — reservation ₱%.2f GCash' % (
        appt.appointment_date.strftime('%b %d, %Y'), float(pending.get('fee') or 0)))

    if pending.get('interested'):
        _post_interested_msg(appt, pending['interested'])

    _notify_new_booking(appt, paid=True)
    return appt, slot_date


@bp.route('/gcash-success')
@login_required
def gcash_success():
    """GCash reservation payment verified -> confirmed booking + items."""
    if _client_only():
        return redirect(url_for('dashboard.index'))

    pending = session.get('pending_booking')
    if not pending:
        if session.get('pay_done'):
            return redirect(url_for('booking.index', status='approved'))
        flash('No pending booking found.', 'warning')
        return redirect(url_for('booking.new'))
    if not pending.get('link_id') or not is_link_paid(pending['link_id']):
        flash('Payment was not completed. Your slot was not reserved.', 'warning')
        return redirect(url_for('booking.new'))
    session.pop('pending_booking', None)

    appt, slot_date = _create_paid_booking(pending)
    session['pay_done'] = True
    flash('Reservation paid — your slot is confirmed for %s!' %
          slot_date.strftime('%b %d, %Y'), 'success')
    return redirect(url_for('booking.index', status='approved'))


@bp.route('/pay-status')
@login_required
def pay_status():
    """Polled by the booking page while the GCash tab is open.

    PayMongo does not always redirect to our success URL, so this endpoint
    verifies the link directly and finalizes the booking itself.
    """
    if session.pop('pay_done', False):
        return jsonify({'paid': True,
                        'redirect': url_for('booking.index', status='approved')})

    pending = session.get('pending_booking')
    if pending and pending.get('link_id') and is_link_paid(pending['link_id']):
        session.pop('pending_booking', None)
        _create_paid_booking(pending)
        session['pay_done'] = True
        return jsonify({'paid': True,
                        'redirect': url_for('booking.index', status='approved')})

    return jsonify({'paid': False})


@bp.route('/<int:id>/detail')
@login_required
def detail(id):
    if _client_only():
        return redirect(url_for('dashboard.index'))
    appt = Appointment.query.get_or_404(id)
    if appt.client_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('booking.index'))
    grand_total = sum(float(i.price) * i.quantity for i in appt.items) + sum(float(a.price) * a.quantity for a in appt.addons)
    return render_template('booking/detail.html', appt=appt, grand_total=grand_total)


@bp.route('/<int:id>/cancel', methods=['POST'])
@login_required
def cancel(id):
    if _client_only():
        return redirect(url_for('dashboard.index'))

    appt = Appointment.query.get_or_404(id)
    if appt.client_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('booking.index'))

    if appt.status not in ['pending', 'confirmed']:
        flash('This booking cannot be cancelled.', 'danger')
        return redirect(url_for('booking.index'))

    if appt.appointment_date <= date.today():
        flash('Bookings can no longer be cancelled on the appointment day.', 'warning')
        return redirect(url_for('booking.index'))

    slot = SalonAvailability.query.filter_by(date=appt.appointment_date, slot_type='client').first()
    if slot and slot.used_slots > 0:
        slot.used_slots -= 1

    if appt.reservation_paid_at:
        # paid bookings keep their record for the ledger; fee is forfeited
        appt.status = 'cancelled'
        db.session.commit()
        log('booking.cancel', 'Cancelled paid booking %s — fee forfeited' %
            appt.appointment_date.strftime('%b %d, %Y'))
        flash('Booking cancelled. The reservation fee is non-refundable.', 'warning')
    else:
        appt_date = appt.appointment_date.strftime('%b %d, %Y')
        db.session.delete(appt)
        db.session.commit()
        log('booking.cancel', 'Cancelled booking %s' % appt_date)
        flash('Booking cancelled. Your slot has been returned.', 'success')
    return redirect(url_for('booking.index', status='my'))
