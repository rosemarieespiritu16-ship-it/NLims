"""Admin appointment management + walk-in + POS + receipt + stock."""
import random
import string
import io
import base64
import uuid
from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from ..models import (Appointment, AppointmentAddon, AppointmentItem, SalonAvailability,
                      Service, Package, Product, User, StockHistory, Receipt, Worker, Message)
from ..extensions import db
from ..pusher import pusher_trigger
from ..mail_helper import send_email
from ..notifications import notify, notify_staff, push_to
from ..paymongo import create_payment_link, is_link_paid
from ..audit import log

bp = Blueprint('appointments', __name__, url_prefix='/appointments')


def _staff_only():
    if current_user.role not in ['superadmin', 'staff']:
        flash('Access denied.', 'danger')
        return True
    return False


def _gen_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _busy_worker_ids():
    appts = Appointment.query.filter(
        Appointment.worker_id.isnot(None),
        Appointment.status.in_(['draft', 'confirmed'])
    ).all()
    return [a.worker_id for a in appts]


def _available_workers():
    busy_ids = _busy_worker_ids()
    all_workers = Worker.query.filter_by(status=1).order_by(Worker.name).all()
    return all_workers, busy_ids


def _post_thankyou(appt):
    """Post a 'thank you' system divider into the client chat after checkout."""
    if not appt.client_id:
        return
    text = 'Thank you for choosing InventorPro!'
    gid = uuid.uuid4().hex
    staff = User.query.filter(
        User.role.in_(['superadmin', 'staff']), User.status == 1).all()
    targets = [(appt.client_id, False)] + [(s.id, True) for s in staff]
    msgs = []
    for uid, is_staff_copy in targets:
        m = Message(sender_id=current_user.id, receiver_id=uid,
                    client_id=appt.client_id, appointment_id=appt.id,
                    group_id=gid, message=text,
                    action_data='system',
                    is_read=1 if is_staff_copy else 0)
        db.session.add(m)
        msgs.append((uid, m))
    db.session.commit()
    for uid, m in msgs:
        push_to(uid, 'new-message', {
            'client_id': appt.client_id,
            'sender': 'InventorPro', 'sender_id': current_user.id,
            'message': text, 'system': True, 'gid': gid, 'id': m.id})


def _sweep_stale():
    """Expire past-due records: confirmed no-shows keep their paid fee as
    forfeited profit; stale pending requests and abandoned walk-in sessions
    are cancelled. Runs on page load — no scheduler needed."""
    today = date.today()
    stale = Appointment.query.filter(
        Appointment.appointment_date < today,
        Appointment.status.in_(['pending', 'confirmed', 'draft'])
    ).all()
    if not stale:
        return
    for a in stale:
        if a.is_walkin or a.status != 'confirmed':
            a.status = 'cancelled'
        else:
            a.status = 'no_show'
    db.session.commit()
    for a in stale:
        if a.status == 'no_show':
            log('appt.no_show', 'No-show — %s, %s%s' % (
                a.client.full_name if a.client else 'Walk-in',
                a.appointment_date.strftime('%b %d, %Y'),
                ' — reservation fee forfeited' if a.reservation_paid_at else ''))


# --- Admin appointment management ---

@bp.route('/')
@login_required
def index():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    _sweep_stale()
    status_filter = request.args.get('status', 'pending')
    query = Appointment.query.filter_by(is_walkin=0)
    if status_filter == 'all':
        query = query.filter(Appointment.status.in_(['pending', 'confirmed']))
    elif status_filter in ('pending', 'confirmed', 'draft', 'no_show'):
        query = query.filter_by(status=status_filter)
    else:
        status_filter = 'all'
        query = query.filter(Appointment.status.in_(['pending', 'confirmed']))
    appts = query.order_by(Appointment.appointment_date.asc()).all()
    totals = {a.id: _grand_total(a) for a in appts}
    # out-of-stock products block approval until removed
    oos = {a.id: any(ad.product and ad.product.quantity <= 0
                     for ad in a.addons) for a in appts}
    counts = {
        'pending': Appointment.query.filter_by(is_walkin=0, status='pending').count(),
        'confirmed': Appointment.query.filter_by(is_walkin=0, status='confirmed').count(),
        'draft': Appointment.query.filter_by(is_walkin=0, status='draft').count(),
        'today': Appointment.query.filter(
            Appointment.is_walkin == 0,
            Appointment.appointment_date == date.today(),
            Appointment.status.in_(['pending', 'confirmed'])).count(),
    }
    workers, busy_ids = _available_workers()
    return render_template('appointments/index.html', appts=appts, totals=totals,
                           status_filter=status_filter, workers=workers,
                           busy_ids=busy_ids, oos=oos, counts=counts)


@bp.route('/walkins')
@login_required
def walkins():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    _sweep_stale()
    status_filter = request.args.get('status', 'all')
    if status_filter not in ('all', 'draft', 'confirmed'):
        status_filter = 'all'
    query = Appointment.query.filter(
        Appointment.is_walkin == 1,
        Appointment.status.in_(['draft', 'confirmed'])
    )
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    appts = query.order_by(Appointment.appointment_date.desc(), Appointment.id.desc()).all()
    totals = {a.id: _grand_total(a) for a in appts}
    return render_template('appointments/walkins.html', appts=appts, totals=totals, status_filter=status_filter)


@bp.route('/voided')
@login_required
def voided():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    appts = Appointment.query.filter_by(status='voided') \
        .order_by(Appointment.created_at.desc()).all()
    totals = {a.id: _grand_total(a) for a in appts}
    return render_template('appointments/voided.html', appts=appts, totals=totals)


@bp.route('/<int:id>/confirm', methods=['POST'])
@login_required
def confirm(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    appt = Appointment.query.get_or_404(id)
    if appt.status == 'pending':
        oos = [ad.product.name for ad in appt.addons
               if ad.product and ad.product.quantity <= 0]
        if oos:
            flash('Cannot approve — out of stock: %s. Remove those items first.'
                  % ', '.join(oos), 'warning')
            return redirect(url_for('appointments.index', status='pending'))
        code = _gen_code()
        while Appointment.query.filter_by(appointment_code=code).first():
            code = _gen_code()
        appt.status = 'confirmed'
        appt.appointment_code = code
        db.session.commit()
        log('appt.confirm', 'Approved booking %s — %s, %s' % (
            code, appt.client.full_name if appt.client else 'Walk-in',
            appt.appointment_date.strftime('%b %d, %Y')))

        pusher_trigger('appointments', 'confirmed', {
            'appointment_code': appt.appointment_code,
            'client_id': appt.client_id
        })

        if appt.client:
            notify(appt.client_id, 'appointment', 'Appointment Approved',
                   f'Your appointment on {appt.appointment_date} was approved. Code: {appt.appointment_code}',
                   link=url_for('booking.detail', id=appt.id))
            sender = current_app.config.get('MAIL_DEFAULT_SENDER')
            if sender and appt.client.email:
                try:
                    html = render_template(
                        'emails/appointment_status.html',
                        name=appt.client.full_name,
                        status='approved',
                        appt_date=appt.appointment_date.strftime('%b %d, %Y'),
                        code=appt.appointment_code,
                        app_url=current_app.config.get('APP_URL', 'http://localhost:5000')
                    )
                    send_email(
                        recipients=[appt.client.email],
                        subject='Appointment Approved - InventorPro',
                        html_body=html
                    )
                except Exception:
                    pass
        flash(f'Appointment confirmed. Code: {appt.appointment_code}', 'success')
    nxt = request.args.get('next')
    if nxt and nxt.startswith('/'):
        return redirect(nxt)
    return redirect(url_for('appointments.index'))


@bp.route('/<int:id>/remove-item', methods=['POST'])
@login_required
def remove_item(id):
    """Admin removes an unavailable item from a booking before approving."""
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    appt = Appointment.query.get_or_404(id)
    kind = request.form.get('kind')
    row_id = request.form.get('row_id', type=int)
    name = None
    if kind == 'item':
        row = AppointmentItem.query.filter_by(id=row_id, appointment_id=appt.id).first()
        if row:
            name = row.service.name if row.service else row.package.name if row.package else 'Item'
            db.session.delete(row)
    elif kind == 'addon':
        row = AppointmentAddon.query.filter_by(id=row_id, appointment_id=appt.id).first()
        if row:
            name = row.product.name if row.product else 'Product'
            # restore reserved stock
            if row.product and row.is_reserved:
                row.product.quantity += row.quantity
                db.session.add(StockHistory(
                    product_id=row.product_id, type='in', quantity=row.quantity,
                    notes=f'Removed from booking (Code {appt.appointment_code})',
                    user_id=current_user.id))
            db.session.delete(row)
    if name:
        db.session.commit()
        log('appt.remove_item', 'Removed %s from booking #%s' % (
            name, appt.appointment_code or appt.id))
        if appt.client_id:
            notify(appt.client_id, 'appointment', 'Item Not Available',
                   f'{name} was removed from your booking on '
                   f'{appt.appointment_date.strftime("%b %d, %Y")} — it is currently unavailable.',
                   link=url_for('booking.detail', id=appt.id))
        flash(f'{name} removed — the client has been notified.', 'info')
    else:
        flash('Item not found.', 'danger')
    nxt = request.args.get('next')
    if nxt and nxt.startswith('/'):
        return redirect(nxt)
    return redirect(url_for('appointments.index', status='pending'))


@bp.route('/<int:id>/reject', methods=['POST'])
@login_required
def reject(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    appt = Appointment.query.get_or_404(id)
    if appt.reservation_paid_at:
        flash('This booking has a paid reservation — it cannot be declined. Reschedule instead.', 'warning')
        nxt = request.args.get('next')
        if nxt and nxt.startswith('/'):
            return redirect(nxt)
        return redirect(url_for('appointments.index'))
    if appt.status == 'pending':
        appt.status = 'cancelled'
        slot = SalonAvailability.query.filter_by(date=appt.appointment_date, slot_type='client').first()
        if slot and slot.used_slots > 0:
            slot.used_slots -= 1
        db.session.commit()
        log('appt.reject', 'Declined booking — %s, %s' % (
            appt.client.full_name if appt.client else 'Walk-in',
            appt.appointment_date.strftime('%b %d, %Y')))

        if appt.client:
            notify(appt.client_id, 'appointment', 'Appointment Declined',
                   f'Your appointment on {appt.appointment_date} was declined.',
                   link=url_for('booking.index'))
            sender = current_app.config.get('MAIL_DEFAULT_SENDER')
            if sender and appt.client.email:
                try:
                    html = render_template(
                        'emails/appointment_status.html',
                        name=appt.client.full_name,
                        status='declined',
                        appt_date=appt.appointment_date.strftime('%b %d, %Y'),
                        code='-',
                        app_url=current_app.config.get('APP_URL', 'http://localhost:5000')
                    )
                    send_email(
                        recipients=[appt.client.email],
                        subject='Appointment Update - InventorPro',
                        html_body=html
                    )
                except Exception:
                    pass
        flash('Appointment rejected.', 'success')
    nxt = request.args.get('next')
    if nxt and nxt.startswith('/'):
        return redirect(nxt)
    return redirect(url_for('appointments.index'))


@bp.route('/<int:id>/no-show', methods=['POST'])
@login_required
def no_show(id):
    """Mark a confirmed booking as a no-show. Any paid reservation fee is
    retained as forfeited revenue (non-refundable) — no balance is due."""
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    appt = Appointment.query.get_or_404(id)
    if appt.status == 'confirmed' and not appt.is_walkin:
        appt.status = 'no_show'
        db.session.commit()
        log('appt.no_show', 'Marked no-show — %s, %s%s' % (
            appt.client.full_name if appt.client else 'Walk-in',
            appt.appointment_date.strftime('%b %d, %Y'),
            ' — reservation fee forfeited' if appt.reservation_paid_at else ''))
        if appt.client_id:
            notify(appt.client_id, 'appointment', 'Appointment Marked No-Show',
                   'Your appointment on %s was marked as a no-show.%s' % (
                       appt.appointment_date.strftime('%b %d, %Y'),
                       ' Your reservation fee is non-refundable.'
                       if appt.reservation_paid_at else ''),
                   link=url_for('booking.index'))
        flash('Marked as no-show.%s' % (
            ' Reservation fee forfeited.' if appt.reservation_paid_at else ''),
            'warning')
    nxt = request.args.get('next')
    if nxt and nxt.startswith('/'):
        return redirect(nxt)
    return redirect(url_for('appointments.index'))


@bp.route('/<int:id>/assign-worker', methods=['POST'])
@login_required
def assign_worker(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    appt = Appointment.query.get_or_404(id)
    worker_id = request.form.get('worker_id', type=int)
    worker = Worker.query.get(worker_id) if worker_id else None
    if not worker or worker.status != 1:
        flash('Select a valid worker.', 'danger')
    elif worker.id in _busy_worker_ids() and appt.worker_id != worker.id:
        flash('That worker is already assigned to another transaction.', 'danger')
    else:
        appt.worker_id = worker.id
        db.session.commit()
        log('appt.assign_worker', '%s assigned to booking #%s' % (
            worker.name, appt.appointment_code or appt.id))
        flash(f'Worker assigned: {worker.name}', 'success')
    nxt = request.args.get('next')
    if nxt and nxt.startswith('/'):
        return redirect(nxt)
    return redirect(url_for('appointments.index'))


@bp.route('/<int:id>/cancel-draft', methods=['POST'])
@login_required
def cancel_draft(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    appt = Appointment.query.get_or_404(id)
    if appt.status == 'draft':
        _restore_addons(appt, current_user.id)
        AppointmentAddon.query.filter_by(appointment_id=id).delete()
        db.session.delete(appt)
        db.session.commit()
        log('appt.cancel_draft', 'Discarded draft #%s' % id)
        flash('Draft deleted.', 'success')
    return redirect(url_for('appointments.index', status='draft'))


@bp.route('/<int:id>/void', methods=['POST'])
@login_required
def void(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    appt = Appointment.query.get_or_404(id)
    if appt.status in ['draft', 'confirmed', 'completed']:
        was_completed = appt.status == 'completed' or appt.payment_status == 'paid'
        appt.status = 'voided'
        appt.payment_status = 'voided'
        if appt.receipt:
            db.session.delete(appt.receipt)
        _restore_addons(appt, current_user.id)
        if was_completed:
            _restore_service_products(appt, current_user.id)
        slot = SalonAvailability.query.filter_by(date=appt.appointment_date, slot_type='client').first()
        if slot and slot.used_slots > 0 and appt.is_walkin == 0:
            slot.used_slots -= 1
        db.session.commit()
        log('appt.void', 'Voided txn #%s — %s' % (
            appt.appointment_code or appt.id,
            appt.client.full_name if appt.client else 'Walk-in'))
        flash('Transaction voided. Stock restored.', 'success')
    return redirect(url_for('appointments.index'))


# --- Walk-in (today only) ---

@bp.route('/clients/search')
@login_required
def clients_search():
    if _staff_only():
        return jsonify({'results': []}), 403
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify({'results': []})
    like = f'%{q}%'
    clients = User.query.filter(
        User.role == 'client',
        User.status == 1,
        db.or_(
            User.name.ilike(like),
            User.first_name.ilike(like),
            User.last_name.ilike(like),
            User.email.ilike(like),
            User.phone.ilike(like)
        )
    ).order_by(User.first_name).limit(20).all()
    return jsonify({'results': [
        {
            'id': c.id,
            'name': c.full_name or c.name or c.email,
            'email': c.email,
            'phone': c.phone or ''
        } for c in clients
    ]})


@bp.route('/walkin', methods=['GET', 'POST'])
@login_required
def walkin():
    # merged into the unified New Transaction form
    return redirect(url_for('appointments.new_transaction'))


# --- New POS session (walk-in guest or registered client) ---

@bp.route('/new-transaction', methods=['GET', 'POST'])
@login_required
def new_transaction():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    workers, busy_ids = _available_workers()

    if request.method == 'POST':
        client_id = request.form.get('client_id') or None
        worker_id = request.form.get('worker_id') or None
        worker_id = int(worker_id) if worker_id else None
        if worker_id and worker_id in _busy_worker_ids():
            flash('Selected worker is already assigned to another transaction.', 'danger')
            return redirect(url_for('appointments.new_transaction'))

        slot = SalonAvailability.query.filter_by(date=date.today(), slot_type='walkin').first()
        if not slot or slot.remaining <= 0:
            flash('No walk-in slots available for today.', 'danger')
            return redirect(url_for('appointments.new_transaction'))

        appt = Appointment(
            client_id=int(client_id) if client_id else None,
            worker_id=worker_id,
            appointment_date=date.today(),
            status='confirmed',
            is_walkin=1,
            appointment_code=_gen_code()
        )
        db.session.add(appt)
        slot.used_slots += 1
        db.session.commit()
        log('walkin.create', 'POS session %s — %s' % (
            appt.appointment_code,
            appt.client.full_name if appt.client else 'Walk-in guest'))
        flash(f'Session created. Code: {appt.appointment_code}', 'success')
        return redirect(url_for('appointments.pos', id=appt.id))

    return render_template('appointments/new_transaction.html', workers=workers, busy_ids=busy_ids)


# --- POS lookup by code ---

@bp.route('/pos-lookup', methods=['GET', 'POST'])
@login_required
def pos_lookup():
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    if request.method == 'POST':
        code = request.form['appointment_code'].strip().upper()
        appt = Appointment.query.filter_by(appointment_code=code).first()
        if not appt:
            flash('Invalid appointment code.', 'danger')
        elif appt.status == 'pending':
            flash('This appointment is pending approval. Please confirm it first.', 'warning')
        elif appt.status == 'voided':
            flash('This transaction has been voided.', 'danger')
        else:
            return redirect(url_for('appointments.pos', id=appt.id))
    return render_template('appointments/pos_lookup.html')


# --- POS ---

@bp.route('/<int:id>/pos', methods=['GET', 'POST'])
@login_required
def pos(id):
    if _staff_only():
        return redirect(url_for('dashboard.index'))
    appt = Appointment.query.get_or_404(id)
    if request.method == 'GET' and not appt.worker_id and appt.status in ('draft', 'confirmed'):
        workers, busy_ids = _available_workers()
        return render_template('appointments/assign_worker.html',
                               appt=appt, workers=workers, busy_ids=busy_ids)
    services = Service.query.filter_by(status=1).order_by(Service.category, Service.name).all()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_item':
            item_type = request.form.get('item_type')
            item_id = int(request.form['item_id'])
            qty = int(request.form.get('quantity', 1))
            if item_type == 'service':
                s = Service.query.get(item_id)
                if not s:
                    flash('Service not found.', 'danger')
                    return redirect(url_for('appointments.pos', id=id))
                db.session.add(AppointmentItem(
                    appointment_id=id,
                    item_type='service',
                    service_id=item_id,
                    quantity=qty,
                    price=s.price
                ))
            elif item_type == 'package':
                p = Package.query.get(item_id)
                if not p:
                    flash('Package not found.', 'danger')
                    return redirect(url_for('appointments.pos', id=id))
                db.session.add(AppointmentItem(
                    appointment_id=id,
                    item_type='package',
                    package_id=item_id,
                    quantity=qty,
                    price=p.price
                ))
            else:
                flash('Invalid item type.', 'danger')
                return redirect(url_for('appointments.pos', id=id))
            db.session.commit()
            return redirect(url_for('appointments.pos', id=id))

        if action == 'remove_item':
            item_id = int(request.form['item_id'])
            item = AppointmentItem.query.get_or_404(item_id)
            db.session.delete(item)
            db.session.commit()
            flash('Item removed.', 'success')
            return redirect(url_for('appointments.pos', id=id))

        if action == 'add_addon':
            product_id = int(request.form['product_id'])
            qty = int(request.form.get('quantity', 1))
            p = Product.query.get_or_404(product_id)
            if p.quantity < qty:
                flash(f'Insufficient stock for {p.name}.', 'danger')
                return redirect(url_for('appointments.pos', id=id))
            p.quantity -= qty
            db.session.add(StockHistory(
                product_id=p.id,
                type='reserve',
                quantity=qty,
                notes=f'Reserved for POS (Code {appt.appointment_code})',
                user_id=current_user.id
            ))
            addon = AppointmentAddon(
                appointment_id=id,
                product_id=product_id,
                quantity=qty,
                price=p.price,
                is_reserved=1
            )
            db.session.add(addon)
            db.session.commit()
            return redirect(url_for('appointments.pos', id=id))

        if action == 'remove_addon':
            addon_id = int(request.form['addon_id'])
            addon = AppointmentAddon.query.get_or_404(addon_id)
            p = addon.product
            p.quantity += addon.quantity
            db.session.add(StockHistory(
                product_id=p.id,
                type='in',
                quantity=addon.quantity,
                notes=f'POS add-on returned (Code {appt.appointment_code})',
                user_id=current_user.id
            ))
            db.session.delete(addon)
            db.session.commit()
            flash('Add-on removed and stock restored.', 'success')
            return redirect(url_for('appointments.pos', id=id))

        if action == 'save_draft':
            appt.status = 'draft'
            db.session.commit()
            log('pos.draft', 'Saved draft #%s' % (appt.appointment_code or appt.id))
            flash('Transaction saved as draft.', 'success')
            return redirect(url_for('appointments.index', status='draft'))

        if action == 'pay':
            method = request.form['payment_method']
            grand_total = _grand_total(appt)
            payable = max(0.0, grand_total - float(appt.reservation_fee or 0))

            if method == 'cash':
                amount_paid = float(request.form.get('amount_paid', 0) or 0)
                if amount_paid < payable:
                    flash('Payment amount is less than the balance due.', 'danger')
                    return redirect(url_for('appointments.pos', id=id))
                change = amount_paid - payable

                # Deduct service/package product consumption
                _deduct_service_products(appt, current_user.id)

                appt.payment_method = method
                appt.payment_status = 'paid'
                appt.status = 'completed'

                # Convert reservation history to use history
                for addon in appt.addons:
                    db.session.add(StockHistory(
                        product_id=addon.product_id,
                        type='use',
                        quantity=addon.quantity,
                        notes=f'POS add-on sold (Code {appt.appointment_code})',
                        user_id=current_user.id
                    ))

                receipt = _create_receipt(appt, grand_total, 'cash', amount_paid, change)
                db.session.commit()
                log('pos.complete', 'txn %s — ₱%.2f cash — %s' % (
                    receipt.transaction_number or '', grand_total,
                    appt.client.full_name if appt.client else 'Walk-in'))
                _post_thankyou(appt)
                flash(f'Payment received. Change: ₱{change:.2f}. Receipt generated.', 'success')
                return redirect(url_for('appointments.receipt', id=receipt.id))

            elif method == 'gcash':
                result = create_payment_link(
                    amount=payable,
                    description=f'InventorPro Code {appt.appointment_code}',
                    reference=appt.appointment_code or 'APPT-000',
                    success_url=url_for('appointments.gcash_success', id=id, _external=True),
                    failure_url=url_for('appointments.pos', id=id, _external=True)
                )
                if result.get('success'):
                    appt.payment_method = method
                    appt.payment_reference = result.get('link_id')
                    db.session.commit()
                    qr_img = _qr_image(result['checkout_url'])
                    return render_template('appointments/gcash_qr.html',
                                           checkout_url=result['checkout_url'],
                                           qr_img=qr_img,
                                           appt=appt,
                                           grand_total=grand_total)
                else:
                    flash('GCash error: ' + result.get('error', 'Unknown'), 'danger')
                    return redirect(url_for('appointments.pos', id=id))

    grand_total = _grand_total(appt)
    item_total = sum(float(i.price) * i.quantity for i in appt.items)
    addon_total = sum(float(a.price) * a.quantity for a in appt.addons)
    reservation_credit = float(appt.reservation_fee or 0) if appt.reservation_paid_at else 0.0
    payable = max(0.0, grand_total - reservation_credit)
    return render_template('appointments/pos.html', appt=appt, services=services,
                           grand_total=grand_total,
                           item_total=item_total, addon_total=addon_total,
                           reservation_credit=reservation_credit, payable=payable)


@bp.route('/<int:id>/gcash-success', methods=['GET', 'POST'])
@login_required
def gcash_success(id):
    appt = Appointment.query.get_or_404(id)
    grand_total = _grand_total(appt)

    # Already completed (e.g. user refreshed, or PayMongo redirected again)
    if appt.status == 'completed' and appt.receipt:
        return redirect(url_for('appointments.receipt', id=appt.receipt.id))

    _deduct_service_products(appt, current_user.id)
    for addon in appt.addons:
        db.session.add(StockHistory(
            product_id=addon.product_id,
            type='use',
            quantity=addon.quantity,
            notes=f'POS add-on sold (Code {appt.appointment_code})',
            user_id=current_user.id
        ))

    appt.payment_status = 'paid'
    appt.payment_method = 'gcash'
    appt.status = 'completed'
    payable = max(0.0, grand_total - float(appt.reservation_fee or 0))
    receipt = _create_receipt(appt, grand_total, 'gcash', payable, 0)
    db.session.commit()
    log('pos.complete', 'txn %s — ₱%.2f gcash — %s' % (
        receipt.transaction_number or '', grand_total,
        appt.client.full_name if appt.client else 'Walk-in'))
    _post_thankyou(appt)
    flash('GCash payment successful. Receipt generated.', 'success')
    return redirect(url_for('appointments.receipt', id=receipt.id))


# --- Receipt ---

@bp.route('/transactions')
@login_required
def transactions():
    if _staff_only():
        return redirect(url_for('dashboard.index'))

    method = request.args.get('method', 'all')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    query = Receipt.query.join(Appointment).order_by(Receipt.created_at.desc())

    if method == 'cash':
        query = query.filter(Receipt.payment_method == 'cash')
    elif method == 'gcash':
        query = query.filter(Receipt.payment_method == 'gcash')

    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').date()
            query = query.filter(db.func.date(Receipt.created_at) >= df)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d').date()
            query = query.filter(db.func.date(Receipt.created_at) <= dt)
        except ValueError:
            pass

    receipts = query.all()

    # totals
    total_count = len(receipts)
    cash_total = sum(r.total for r in receipts if r.payment_method == 'cash')
    gcash_total = sum(r.total for r in receipts if r.payment_method == 'gcash')
    cash_count = sum(1 for r in receipts if r.payment_method == 'cash')
    gcash_count = sum(1 for r in receipts if r.payment_method == 'gcash')
    grand_total = sum(r.total for r in receipts)

    # per day
    daily = {}
    for r in receipts:
        d = r.created_at.date() if r.created_at else None
        if not d:
            continue
        if d not in daily:
            daily[d] = {'cash_count': 0, 'gcash_count': 0, 'cash_total': 0.0, 'gcash_total': 0.0}
        if r.payment_method == 'cash':
            daily[d]['cash_count'] += 1
            daily[d]['cash_total'] += float(r.total or 0)
        elif r.payment_method == 'gcash':
            daily[d]['gcash_count'] += 1
            daily[d]['gcash_total'] += float(r.total or 0)

    # per week (ISO year-week)
    weekly = {}
    for r in receipts:
        if not r.created_at:
            continue
        year, week, _ = r.created_at.isocalendar()
        key = f'{year}-W{week:02d}'
        if key not in weekly:
            weekly[key] = {'cash_count': 0, 'gcash_count': 0, 'cash_total': 0.0, 'gcash_total': 0.0}
        if r.payment_method == 'cash':
            weekly[key]['cash_count'] += 1
            weekly[key]['cash_total'] += float(r.total or 0)
        elif r.payment_method == 'gcash':
            weekly[key]['gcash_count'] += 1
            weekly[key]['gcash_total'] += float(r.total or 0)

    return render_template('appointments/transactions.html',
                           receipts=receipts,
                           rows=[_txn_row(r) for r in receipts],
                           total_count=total_count,
                           cash_count=cash_count,
                           gcash_count=gcash_count,
                           cash_total=cash_total,
                           gcash_total=gcash_total,
                           grand_total=grand_total,
                           daily=sorted(daily.items(), reverse=True),
                           weekly=sorted(weekly.items(), reverse=True),
                           method=method,
                           date_from=date_from,
                           date_to=date_to)


def _txn_row(r):
    """Serialize a Receipt + its appointment into a detail-modal row dict."""
    a = r.appointment
    res = float(a.reservation_fee or 0) if a and a.reservation_paid_at else 0.0
    lines = []
    if a:
        for it in a.items:
            nm = (it.service.name if it.service else
                  it.package.name if it.package else 'Item')
            lines.append({'name': nm, 'qty': it.quantity, 'price': float(it.price or 0)})
        for ad in a.addons:
            nm = ad.product.name if ad.product else 'Product'
            lines.append({'name': nm, 'qty': ad.quantity, 'price': float(ad.price or 0)})
    total = float(r.total or 0)
    collected = max(0.0, total - res)
    method = (r.payment_method or '').upper()
    res_method = (a.reservation_method or 'gcash').upper() if a else 'GCASH'
    # --- journal entries (debits accrue, credits draw down) ---
    entries = []
    bal = 0.0
    if a:
        for it in a.items:
            nm = (it.service.name if it.service else
                  it.package.name if it.package else 'Item')
            amt = float(it.price or 0) * it.quantity
            bal += amt
            entries.append({'dt': r.created_at, 'account': 'Service Revenue',
                            'desc': '{} ({})'.format(nm, it.quantity), 'ref': 'SRV',
                            'debit': amt, 'credit': None, 'balance': bal})
        for ad in a.addons:
            nm = ad.product.name if ad.product else 'Product'
            amt = float(ad.price or 0) * ad.quantity
            bal += amt
            entries.append({'dt': r.created_at, 'account': 'Product Sales',
                            'desc': '{} ({})'.format(nm, ad.quantity), 'ref': 'PRD',
                            'debit': amt, 'credit': None, 'balance': bal})
    if res:
        bal -= res
        entries.append({'dt': a.reservation_paid_at, 'account': 'Customer Deposits',
                        'desc': 'Reservation fee — {}'.format(res_method), 'ref': 'PAY',
                        'debit': None, 'credit': res, 'balance': bal})
    bal -= collected
    entries.append({'dt': r.created_at,
                    'account': 'Cash Receipts' if method == 'CASH' else 'GCash Receipts',
                    'desc': 'Balance — {}'.format(method), 'ref': 'PAY',
                    'debit': None, 'credit': collected, 'balance': bal})
    return {
        'id': r.id,
        'kind': 'receipt',
        'status_label': 'Completed',
        'client_id': a.client_id if a else None,
        'when': r.created_at,
        'name': ((a.client.full_name or a.client.name) if a and a.client else 'Walk-in'),
        'phone': (a.client.phone or '—') if a and a.client else '—',
        'email': (a.client.email or '—') if a and a.client else '—',
        'type': 'Walk-in' if (a and a.is_walkin) else 'Appointment',
        'cashier': (a.worker.name if a and a.worker else 'Admin'),
        'ref': r.transaction_number or '',
        'method': method,
        'res': res,
        'res_when': a.reservation_paid_at if a else None,
        'res_method': res_method,
        'total': total,
        'collected': collected,
        'lines': lines,
        'entries': entries,
    }


def _res_row(a):
    """Serialize a reservation-paid Appointment (no receipt yet) into a ledger row."""
    res = float(a.reservation_fee or 0)
    res_method = (a.reservation_method or 'gcash').upper()
    forfeited = a.status in ('no_show', 'cancelled')
    lines = []
    entries = []
    bal = 0.0
    if forfeited:
        # nothing billed — the paid fee is retained as profit, balance 0
        entries.append({'dt': a.reservation_paid_at, 'account': 'Customer Deposits',
                        'desc': 'Reservation fee — {}'.format(res_method), 'ref': 'PAY',
                        'debit': None, 'credit': res, 'balance': -res})
        entries.append({'dt': a.reservation_paid_at, 'account': 'Forfeited Reservation Fees',
                        'desc': '%s — fee retained as revenue (non-refundable)' % (
                            'No-show' if a.status == 'no_show' else 'Cancelled'),
                        'ref': 'REV', 'debit': res, 'credit': None, 'balance': 0.0})
    else:
        for it in a.items:
            nm = (it.service.name if it.service else
                  it.package.name if it.package else 'Item')
            lines.append({'name': nm, 'qty': it.quantity, 'price': float(it.price or 0)})
            amt = float(it.price or 0) * it.quantity
            bal += amt
            entries.append({'dt': a.reservation_paid_at, 'account': 'Service Revenue',
                            'desc': '{} ({})'.format(nm, it.quantity), 'ref': 'SRV',
                            'debit': amt, 'credit': None, 'balance': bal})
        for ad in a.addons:
            nm = ad.product.name if ad.product else 'Product'
            lines.append({'name': nm, 'qty': ad.quantity, 'price': float(ad.price or 0)})
            amt = float(ad.price or 0) * ad.quantity
            bal += amt
            entries.append({'dt': a.reservation_paid_at, 'account': 'Product Sales',
                            'desc': '{} ({})'.format(nm, ad.quantity), 'ref': 'PRD',
                            'debit': amt, 'credit': None, 'balance': bal})
        if res:
            bal -= res
            entries.append({'dt': a.reservation_paid_at, 'account': 'Customer Deposits',
                            'desc': 'Reservation fee — {}'.format(res_method), 'ref': 'PAY',
                            'debit': None, 'credit': res, 'balance': bal})
    return {
        'id': 'res%d' % a.id,
        'kind': 'reservation',
        'status_label': ('No-Show' if a.status == 'no_show' else
                         'Cancelled' if a.status == 'cancelled' else 'Booked'),
        'forfeited': forfeited,
        'client_id': a.client_id,
        'when': a.reservation_paid_at,
        'name': ((a.client.full_name or a.client.name) if a.client else 'Walk-in'),
        'phone': (a.client.phone or '—') if a.client else '—',
        'email': (a.client.email or '—') if a.client else '—',
        'type': 'Booking',
        'cashier': (a.worker.name if a.worker else 'Admin'),
        'ref': a.reservation_reference or a.appointment_code or '',
        'method': res_method,
        'res': res,
        'res_when': a.reservation_paid_at,
        'res_method': res_method,
        'total': _grand_total(a),
        'collected': 0.0,
        'lines': lines,
        'entries': entries,
    }


@bp.route('/ledger')
@login_required
def ledger():
    """Financial ledger: reservation fees and POS transaction revenue."""
    if _staff_only():
        return redirect(url_for('dashboard.index'))

    # --- filters ---
    q = request.args.get('q', '').strip()
    method = request.args.get('method', 'all')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = request.args.get('page', 1, type=int)

    df = dt = None
    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').date()
        except ValueError:
            date_from = ''
    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d').date()
        except ValueError:
            date_to = ''

    query = (Receipt.query.join(Appointment)
             .outerjoin(User, Appointment.client_id == User.id))
    if method in ('cash', 'gcash'):
        query = query.filter(Receipt.payment_method == method)
    if q:
        like = '%' + q + '%'
        query = query.filter(db.or_(
            db.func.concat(User.first_name, ' ', User.last_name).ilike(like),
            User.name.ilike(like),
            Receipt.transaction_number.ilike(like)))
    if df:
        query = query.filter(db.func.date(Receipt.created_at) >= df)
    if dt:
        query = query.filter(db.func.date(Receipt.created_at) <= dt)

    receipts = query.all()

    # --- reservation fees paid on confirmed bookings not yet checked out ---
    res_query = (Appointment.query.outerjoin(User, Appointment.client_id == User.id)
                 .outerjoin(Receipt, Receipt.appointment_id == Appointment.id)
                 .filter(Appointment.reservation_paid_at.isnot(None),
                         Appointment.status.in_(['confirmed', 'no_show', 'cancelled']),
                         Receipt.id.is_(None)))
    if method in ('cash', 'gcash'):
        res_query = res_query.filter(Appointment.reservation_method == method)
    if q:
        like = '%' + q + '%'
        res_query = res_query.filter(db.or_(
            db.func.concat(User.first_name, ' ', User.last_name).ilike(like),
            User.name.ilike(like),
            Appointment.reservation_reference.ilike(like),
            Appointment.appointment_code.ilike(like)))
    if df:
        res_query = res_query.filter(db.func.date(Appointment.reservation_paid_at) >= df)
    if dt:
        res_query = res_query.filter(db.func.date(Appointment.reservation_paid_at) <= dt)

    entries_all = ([(r.created_at, 'receipt', r) for r in receipts] +
                   [(a.reservation_paid_at, 'reservation', a)
                    for a in res_query.all()])
    entries_all.sort(key=lambda e: e[0] or datetime.min, reverse=True)

    all_rows = [_txn_row(obj) if kind == 'receipt' else _res_row(obj)
                for _, kind, obj in entries_all]

    # --- one row per client; walk-ins grouped under a single 'Walk-in' row ---
    grouped = {}
    for r in all_rows:
        key = 'c%d' % r['client_id'] if r['client_id'] else 'walkin'
        g = grouped.setdefault(key, {
            'key': key,
            'name': r['name'] if r['client_id'] else 'Walk-in',
            'phone': r['phone'] if r['client_id'] else '',
            'count': 0, 'total': 0.0, 'last': None, 'rows': []})
        g['rows'].append(r)
        g['count'] += 1
        g['total'] += r['res'] if r['kind'] == 'reservation' else r['total']
        if r['when'] and (not g['last'] or r['when'] > g['last']):
            g['last'] = r['when']
    groups = sorted(grouped.values(),
                    key=lambda g: g['last'] or datetime.min, reverse=True)

    # --- ledger math per client: debit = billed, credit = paid (oldest -> newest) ---
    for g in groups:
        g['rows'].reverse()
        bal = 0.0
        for r in g['rows']:
            # debit = billed services total; credit = payments received
            # (reservation fee credits the booked amount -> balance = due at checkout)
            # forfeited reservations: fee is pure profit -> debit = credit, no balance
            r['debit'] = r['res'] if r.get('forfeited') else r['total']
            r['credit'] = ((r['res'] + r['collected'])
                           if r['kind'] == 'receipt' else r['res'])
            bal += (r['debit'] or 0) - (r['credit'] or 0)
            r['balance'] = bal
        g['debits'] = sum(r['debit'] or 0 for r in g['rows'])
        g['credits'] = sum(r['credit'] or 0 for r in g['rows'])
        g['balance'] = bal

    # --- manual pagination over clients (aggregates span the whole filtered set) ---
    per_page = 10
    total_count = len(groups)
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    page_groups = groups[(page - 1) * per_page: page * per_page]
    rows = [r for g in page_groups for r in g['rows']]
    # reservation fees are revenue received — count all paid reservations
    res_total = sum(float(a.reservation_fee or 0) for a in
                    Appointment.query.filter(Appointment.reservation_paid_at.isnot(None)).all())
    txn_total = sum(
        max(0.0, float(r.total or 0) - (
            float(r.appointment.reservation_fee or 0)
            if r.appointment and r.appointment.reservation_paid_at else 0.0))
        for r in receipts)
    return render_template('appointments/ledger.html', groups=page_groups, rows=rows,
                           res_total=res_total, txn_total=txn_total,
                           q=q, method=method, date_from=date_from,
                           date_to=date_to, page=page,
                           total_pages=total_pages, total_count=total_count,
                           start_i=(page - 1) * per_page + 1 if total_count else 0,
                           end_i=min(page * per_page, total_count))


@bp.route('/<int:id>/link-status')
@login_required
def link_status(id):
    appt = Appointment.query.get_or_404(id)
    if not appt.payment_reference:
        return {'paid': False}
    is_paid = is_link_paid(appt.payment_reference)
    return {'paid': is_paid}


@bp.route('/receipt/<int:id>')
@login_required
def receipt(id):
    r = Receipt.query.get_or_404(id)
    return render_template('appointments/receipt.html', receipt=r)


# --- Helpers ---

def _grand_total(appt):
    item_total = sum(float(i.price) * i.quantity for i in appt.items)
    addon_total = sum(float(a.price) * a.quantity for a in appt.addons)
    return item_total + addon_total


def _deduct_service_products(appt, user_id):
    for i in appt.items:
        if i.item_type == 'service' and i.service:
            for sp in i.service.products:
                p = sp.product
                p.quantity = max(0, p.quantity - sp.quantity_used)
                db.session.add(StockHistory(
                    product_id=p.id,
                    type='use',
                    quantity=sp.quantity_used,
                    notes=f'Service: {i.service.name} (Code {appt.appointment_code})',
                    user_id=user_id
                ))
        elif i.item_type == 'package' and i.package:
            for item in i.package.items:
                if item.item_type == 'service' and item.service:
                    for sp in item.service.products:
                        p = sp.product
                        p.quantity = max(0, p.quantity - sp.quantity_used)
                        db.session.add(StockHistory(
                            product_id=p.id,
                            type='use',
                            quantity=sp.quantity_used,
                            notes=f'Package: {i.package.name} / {item.service.name} (Code {appt.appointment_code})',
                            user_id=user_id
                        ))


def _restore_addons(appt, user_id):
    for addon in appt.addons:
        if addon.is_reserved:
            p = addon.product
            p.quantity += addon.quantity
            db.session.add(StockHistory(
                product_id=p.id,
                type='in',
                quantity=addon.quantity,
                notes=f'POS add-on returned (Code {appt.appointment_code})',
                user_id=user_id
            ))
            addon.is_reserved = 0


def _restore_service_products(appt, user_id):
    for i in appt.items:
        if i.item_type == 'service' and i.service:
            for sp in i.service.products:
                p = sp.product
                p.quantity += sp.quantity_used
                db.session.add(StockHistory(
                    product_id=p.id,
                    type='in',
                    quantity=sp.quantity_used,
                    notes=f'Service voided: {i.service.name} (Code {appt.appointment_code})',
                    user_id=user_id
                ))
        elif i.item_type == 'package' and i.package:
            for item in i.package.items:
                if item.item_type == 'service' and item.service:
                    for sp in item.service.products:
                        p = sp.product
                        p.quantity += sp.quantity_used
                        db.session.add(StockHistory(
                            product_id=p.id,
                            type='in',
                            quantity=sp.quantity_used,
                            notes=f'Package voided: {i.package.name} / {item.service.name} (Code {appt.appointment_code})',
                            user_id=user_id
                        ))


def _gen_txn_number():
    """YYYYMM-0001 — sequential transaction number per month."""
    prefix = datetime.now().strftime('%Y%m')
    last = (Receipt.query
            .filter(Receipt.transaction_number.like(prefix + '-%'))
            .order_by(Receipt.id.desc()).first())
    seq = 1
    if last and last.transaction_number:
        try:
            seq = int(last.transaction_number.split('-')[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return '%s-%04d' % (prefix, seq)


def _create_receipt(appt, total, method, amount_paid, change):
    from datetime import timezone, timedelta
    txn = _gen_txn_number()
    appt.transaction_number = txn
    r = Receipt(
        appointment_id=appt.id,
        transaction_number=txn,
        total=total,
        amount_paid=amount_paid,
        change_amount=change,
        payment_method=method,
        reference=appt.payment_reference or '',
        created_at=datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    )
    db.session.add(r)
    db.session.flush()
    return r


def _qr_image(url):
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None
