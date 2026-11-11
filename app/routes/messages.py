"""Client <-> admin messaging. Staff see per-client threads; clients see one admin thread."""
import os
import uuid
from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, session
from flask_login import login_required, current_user
from ..models import (Message, User, Appointment, Package, SalonAvailability,
                      Service, AppointmentItem)
from ..extensions import db
from ..notifications import push_to, notify_staff

bp = Blueprint('messages', __name__, url_prefix='/messages')

STAFF_ROLES = ['superadmin', 'staff']


@bp.before_request
def _block_impersonated():
    """Client messages are private — hidden while an admin previews a client."""
    if session.get('impersonator_id'):
        flash('Messages are private while previewing a client.', 'info')
        return redirect(url_for('dashboard.index'))


def _staff_users(exclude_id=None):
    q = User.query.filter(User.role.in_(STAFF_ROLES), User.status == 1)
    if exclude_id:
        q = q.filter(User.id != exclude_id)
    return q.all()


def _pkg_payload(pkg):
    if not pkg:
        return None
    return {
        'id': pkg.id,
        'name': pkg.name,
        'price': float(pkg.price or 0),
        'items': [{
            'name': (pi.service.name if pi.item_type == 'service' and pi.service else
                     pi.product.name if pi.item_type == 'product' and pi.product else 'Item'),
            'qty': pi.quantity
        } for pi in pkg.items]
    }


def _svc_payload(svc):
    if not svc:
        return None
    return {'id': svc.id, 'name': svc.name, 'price': float(svc.price or 0)}


def _avail_slots():
    """Upcoming client slots with remaining capacity."""
    rows = SalonAvailability.query.filter(
        SalonAvailability.date >= date.today(),
        SalonAvailability.slot_type == 'client'
    ).order_by(SalonAvailability.date).limit(45).all()
    return [s for s in rows if s.remaining > 0][:30]


def _appt_payload(appt):
    if not appt:
        return None
    return {
        'id': appt.id,
        'code': appt.appointment_code or '',
        'date': appt.appointment_date.strftime('%b %d, %Y') if appt.appointment_date else '',
        'status': appt.status
    }


def _save_image(file_storage):
    """Save a chat image upload; returns filename or None."""
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return None
    fname = 'chat-%s.%s' % (uuid.uuid4().hex, ext)
    up_dir = os.path.join(current_app.root_path, '..', 'assets', 'uploads')
    os.makedirs(up_dir, exist_ok=True)
    file_storage.save(os.path.join(up_dir, fname))
    return fname


def _is_ajax():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _dedupe(msgs):
    """Message copies share group_id - show each logical message once."""
    seen = set()
    out = []
    for m in msgs:
        key = m.group_id or ('_%s' % m.id)
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


@bp.route('/')
@login_required
def index():
    if current_user.role == 'client':
        msgs = _dedupe(Message.query.filter(
            db.or_(Message.sender_id == current_user.id,
                   Message.receiver_id == current_user.id)
        ).order_by(Message.created_at.asc()).all())
        Message.query.filter_by(receiver_id=current_user.id, is_read=0)\
            .update({'is_read': 1}, synchronize_session=False)
        db.session.commit()

        appt = None
        appt_id = request.args.get('appointment')
        if appt_id:
            appt = Appointment.query.get(int(appt_id))
            if appt and appt.client_id != current_user.id:
                appt = None
        booked_dates = {a.appointment_date.isoformat() for a in
                        Appointment.query.filter(
                            Appointment.client_id == current_user.id,
                            Appointment.status.in_(['pending', 'confirmed']),
                            Appointment.appointment_date >= date.today()).all()}
        return render_template('messages/index.html',
                               messages=msgs, appt_ctx=appt,
                               services=Service.query.filter_by(status=1)
                                   .order_by(Service.category, Service.name).all(),
                               slots=_avail_slots(),
                               booked_dates=booked_dates,
                               threads=[], active_client=None)

    # staff view
    client_id = request.args.get('client', type=int)
    mine = Message.query.filter(
        Message.client_id.isnot(None),
        db.or_(Message.sender_id == current_user.id,
               Message.receiver_id == current_user.id)
    ).order_by(Message.created_at.desc()).all()

    threads = {}
    for m in mine:
        if m.client_id not in threads:
            client = User.query.get(m.client_id)
            if not client:
                continue
            threads[m.client_id] = {
                'client': client,
                'last': m,
                'unread': 0
            }
        if m.receiver_id == current_user.id and not m.is_read:
            threads[m.client_id]['unread'] += 1

    active_client = None
    msgs = []
    if client_id:
        active_client = User.query.get(client_id)
        if not active_client or active_client.role != 'client':
            flash('Invalid client.', 'danger')
            return redirect(url_for('messages.index'))
        msgs = _dedupe(Message.query.filter(
            Message.client_id == client_id,
            db.or_(Message.sender_id == current_user.id,
                   Message.receiver_id == current_user.id)
        ).order_by(Message.created_at.asc()).all())
        Message.query.filter_by(receiver_id=current_user.id, client_id=client_id, is_read=0)\
            .update({'is_read': 1}, synchronize_session=False)
        db.session.commit()

    appt = None
    appt_id = request.args.get('appointment')
    if appt_id and active_client:
        appt = Appointment.query.get(int(appt_id))
        if appt and appt.client_id != active_client.id:
            appt = None

    return render_template('messages/index.html',
                           messages=msgs, appt_ctx=appt,
                           services=Service.query.filter_by(status=1)
                               .order_by(Service.category, Service.name).all(),
                           slots=_avail_slots(),
                           threads=sorted(threads.values(), key=lambda t: t['last'].created_at, reverse=True),
                           active_client=active_client)


@bp.route('/thread')
@login_required
def thread():
    """JSON: new messages in the open thread since `after` (message id)."""
    after = request.args.get('after', type=int, default=0)

    if current_user.role == 'client':
        cid = current_user.id
    else:
        cid = request.args.get('client', type=int)
        client = User.query.get(cid) if cid else None
        if not client or client.role != 'client':
            return jsonify({'error': 'invalid client'}), 400

    msgs = _dedupe(Message.query.filter(
        Message.client_id == cid,
        Message.id > after,
        db.or_(Message.sender_id == current_user.id,
               Message.receiver_id == current_user.id)
    ).order_by(Message.id.asc()).all())

    Message.query.filter(
        Message.client_id == cid,
        Message.receiver_id == current_user.id,
        Message.is_read == 0
    ).update({'is_read': 1}, synchronize_session=False)
    db.session.commit()

    return jsonify([{
        'id': m.id,
        'sender_id': m.sender_id,
        'sender_name': (m.sender.full_name or m.sender.name) if m.sender else 'User',
        'sender_img': m.sender.image if m.sender and m.sender.image else '',
        'message': m.message,
        'attachment': ('/assets/uploads/' + m.attachment) if m.attachment else '',
        'package': _pkg_payload(m.package),
        'service': _svc_payload(m.service),
        'system': m.action_data == 'system',
        'appt': _appt_payload(m.appointment),
        'gid': m.group_id or '',
        'when': m.created_at.strftime('%b %d, %I:%M %p') if m.created_at else ''
    } for m in msgs])


@bp.route('/threads')
@login_required
def threads():
    """JSON: staff thread list for live sidebar updates."""
    if current_user.role == 'client':
        return jsonify([])
    mine = Message.query.filter(
        Message.client_id.isnot(None),
        db.or_(Message.sender_id == current_user.id,
               Message.receiver_id == current_user.id)
    ).order_by(Message.created_at.desc()).all()

    out = {}
    seen_groups = set()
    for m in mine:  # desc order -> first-seen client = newest thread
        t = out.get(m.client_id)
        if not t:
            c = User.query.get(m.client_id)
            if not c:
                continue
            t = out[m.client_id] = {
                'client_id': c.id,
                'name': c.full_name or c.name,
                'img': c.image or '',
                'last': '', 'when': '', 'unread': 0
            }
        if not t['last'] and (m.group_id or m.id) not in seen_groups:
            t['last'] = m.message or ('[image]' if m.attachment else '[package]')
            t['when'] = m.created_at.strftime('%b %d, %I:%M %p') if m.created_at else ''
            seen_groups.add(m.group_id or m.id)
        if m.receiver_id == current_user.id and not m.is_read:
            t['unread'] += 1
    return jsonify(list(out.values()))


@bp.route('/send', methods=['POST'])
@login_required
def send():
    body = (request.form.get('message') or '').strip()
    package_id = request.form.get('package_id', type=int)
    service_id = request.form.get('service_id', type=int)
    pkg = Package.query.get(package_id) if package_id else None
    svc = Service.query.get(service_id) if service_id else None
    if pkg and pkg.status != 1:
        pkg = None
    if svc and svc.status != 1:
        svc = None
    attachment = _save_image(request.files.get('image'))

    if not body and not attachment and not pkg and not svc:
        if _is_ajax():
            return jsonify({'error': 'empty'}), 400
        return redirect(url_for('messages.index'))
    if pkg and not body:
        body = 'Interested in package: %s' % pkg.name
    if svc and not body:
        body = 'Interested in service: %s' % svc.name

    appt_id = request.form.get('appointment_id', type=int)
    appt = Appointment.query.get(appt_id) if appt_id else None

    def msg_kwargs():
        return dict(
            appointment_id=appt.id if appt else None,
            package_id=pkg.id if pkg else None,
            service_id=svc.id if svc else None,
            attachment=attachment,
            message=body)

    if current_user.role == 'client':
        appt = appt if appt and appt.client_id == current_user.id else None
        staff = _staff_users()
        if not staff:
            flash('No staff available to message.', 'danger')
            return redirect(url_for('messages.index'))
        gid = uuid.uuid4().hex
        msgs = [Message(sender_id=current_user.id, receiver_id=s.id,
                        client_id=current_user.id, group_id=gid, **msg_kwargs())
                for s in staff]
        db.session.add_all(msgs)
        db.session.commit()
        payload = {'client_id': current_user.id, 'sender': current_user.full_name or current_user.name,
                   'sender_id': current_user.id,
                   'message': body, 'appt': _appt_payload(appt), 'gid': gid,
                   'attachment': ('/assets/uploads/' + attachment) if attachment else '',
                   'package': _pkg_payload(pkg), 'service': _svc_payload(svc)}
        for s, msg in zip(staff, msgs):
            p = dict(payload)
            p['id'] = msg.id
            push_to(s.id, 'new-message', p)
        if _is_ajax():
            return jsonify({'ok': True, 'id': msgs[0].id})
        return redirect(url_for('messages.index'))

    # staff -> client
    client_id = request.form.get('client_id', type=int)
    client = User.query.get(client_id) if client_id else None
    if not client or client.role != 'client':
        flash('Invalid client.', 'danger')
        return redirect(url_for('messages.index'))

    appt = appt if appt and appt.client_id == client.id else None
    gid = uuid.uuid4().hex
    msgs = [Message(sender_id=current_user.id, receiver_id=client.id,
                    client_id=client.id, group_id=gid, **msg_kwargs())]
    msgs += [Message(sender_id=current_user.id, receiver_id=s.id,
                     client_id=client.id, group_id=gid, is_read=1, **msg_kwargs())
             for s in _staff_users(exclude_id=current_user.id)]
    db.session.add_all(msgs)
    db.session.commit()

    targets = [(client.id, msgs[0].id)]
    for s, m in zip(_staff_users(exclude_id=current_user.id), msgs[1:]):
        targets.append((s.id, m.id))
    for uid, mid in targets:
        push_to(uid, 'new-message', {
            'client_id': client.id, 'sender': 'Admin', 'sender_id': current_user.id,
            'message': body, 'appt': _appt_payload(appt), 'gid': gid, 'id': mid,
            'attachment': ('/assets/uploads/' + attachment) if attachment else '',
            'package': _pkg_payload(pkg), 'service': _svc_payload(svc)})

    if _is_ajax():
        return jsonify({'ok': True, 'id': msgs[0].id})
    return redirect(url_for('messages.index', client=client.id))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    """Delete one message. Sender deletes all copies (unsend); receiver deletes only their copy."""
    m = Message.query.get_or_404(id)
    if m.sender_id != current_user.id and m.receiver_id != current_user.id:
        if _is_ajax():
            return jsonify({'error': 'forbidden'}), 403
        flash('Access denied.', 'danger')
        return redirect(url_for('messages.index'))
    if m.group_id and m.sender_id == current_user.id:
        rows = Message.query.filter_by(group_id=m.group_id).all()
        targets = {r.receiver_id for r in rows} | {r.sender_id for r in rows}
        for r in rows:
            db.session.delete(r)
        db.session.commit()
        for uid in targets:
            if uid != current_user.id:
                push_to(uid, 'delete-message', {'group_id': m.group_id})
    else:
        db.session.delete(m)
        db.session.commit()
    if _is_ajax():
        return jsonify({'ok': True})
    return redirect(request.referrer or url_for('messages.index'))


@bp.route('/clear', methods=['POST'])
@login_required
def clear():
    """Clear a whole conversation from the current user's view."""
    if current_user.role == 'client':
        Message.query.filter(
            db.or_(Message.sender_id == current_user.id,
                   Message.receiver_id == current_user.id)
        ).delete(synchronize_session=False)
        db.session.commit()
        flash('Conversation cleared.', 'success')
        return redirect(url_for('messages.index'))

    client_id = request.form.get('client_id', type=int)
    Message.query.filter(
        Message.client_id == client_id,
        db.or_(Message.sender_id == current_user.id,
               Message.receiver_id == current_user.id)
    ).delete(synchronize_session=False)
    db.session.commit()
    flash('Conversation cleared.', 'success')
    return redirect(url_for('messages.index'))


@bp.route('/quick-book', methods=['POST'])
@login_required
def quick_book():
    """Client picks an available slot from chat -> pending appointment + message to staff."""
    if current_user.role != 'client':
        return jsonify({'error': 'Only clients can quick-book.'}), 403

    try:
        slot_date = datetime.strptime(request.form.get('date', ''), '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date.'}), 400

    existing = Appointment.query.filter(
        Appointment.client_id == current_user.id,
        Appointment.status.in_(['pending', 'confirmed']),
        Appointment.appointment_date == slot_date
    ).first()
    if existing:
        return jsonify({'error': 'You already have a booking on %s — pick another date.' %
                        slot_date.strftime('%b %d, %Y')}), 400

    slot = SalonAvailability.query.filter_by(date=slot_date, slot_type='client').first()
    if not slot or slot.remaining <= 0:
        return jsonify({'error': 'No client slots available for that date.'}), 400

    # booking now requires items + optional reservation payment -> send to the form
    return jsonify({'redirect': url_for('booking.new', date=slot_date.isoformat())})


@bp.route('/propose-date', methods=['POST'])
@login_required
def propose_date():
    """Staff proposes a new date for a pending booking -> client confirms in chat."""
    if current_user.role == 'client':
        return jsonify({'error': 'forbidden'}), 403

    client = User.query.get(request.form.get('client_id', type=int))
    appt = Appointment.query.get(request.form.get('appointment_id', type=int))
    if not client or client.role != 'client' or not appt or appt.client_id != client.id:
        return jsonify({'error': 'Invalid booking.'}), 400
    if appt.status != 'pending':
        return jsonify({'error': 'Booking is no longer pending.'}), 400
    try:
        new_date = datetime.strptime(request.form.get('date', ''), '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date.'}), 400
    slot = SalonAvailability.query.filter_by(date=new_date, slot_type='client').first()
    if not slot or slot.remaining <= 0:
        return jsonify({'error': 'No slots available on that date.'}), 400

    body = 'Reschedule request: move your booking from %s to %s?' % (
        appt.appointment_date.strftime('%b %d, %Y'), new_date.strftime('%b %d, %Y'))
    gid = uuid.uuid4().hex
    action = 'reschedule:%s' % new_date.isoformat()
    msgs = [Message(sender_id=current_user.id, receiver_id=client.id,
                    client_id=client.id, appointment_id=appt.id,
                    group_id=gid, action_data=action, message=body)]
    msgs += [Message(sender_id=current_user.id, receiver_id=s.id,
                     client_id=client.id, appointment_id=appt.id,
                     group_id=gid, action_data=action, message=body, is_read=1)
             for s in _staff_users(exclude_id=current_user.id)]
    db.session.add_all(msgs)
    db.session.commit()

    targets = [(client.id, msgs[0].id)] + [
        (s.id, m.id) for s, m in zip(_staff_users(exclude_id=current_user.id), msgs[1:])]
    for uid, mid in targets:
        push_to(uid, 'new-message', {
            'client_id': client.id, 'sender': 'Admin', 'sender_id': current_user.id,
            'message': body, 'appt': _appt_payload(appt), 'gid': gid, 'id': mid,
            'action_data': action})

    return jsonify({'ok': True, 'id': msgs[0].id, 'gid': gid})


@bp.route('/respond/<int:id>/<decision>', methods=['POST'])
@login_required
def respond(id, decision):
    """Client accepts/declines a reschedule proposal in chat."""
    m = Message.query.get_or_404(id)
    if current_user.role != 'client' or m.receiver_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    if not m.action_data or not m.action_data.startswith('reschedule:'):
        return jsonify({'error': 'Nothing to respond to.'}), 400
    raw = m.action_data.split(':', 1)[1]
    if raw == 'done':
        return jsonify({'error': 'Already answered.'}), 400

    appt = m.appointment
    if not appt or appt.client_id != current_user.id or appt.status != 'pending':
        return jsonify({'error': 'Booking is no longer pending.'}), 400
    new_date = date.fromisoformat(raw)

    if decision == 'accept':
        new_slot = SalonAvailability.query.filter_by(date=new_date, slot_type='client').first()
        if not new_slot or new_slot.remaining <= 0:
            return jsonify({'error': 'That date is now full.'}), 400
        old_slot = SalonAvailability.query.filter_by(
            date=appt.appointment_date, slot_type='client').first()
        if old_slot and old_slot.used_slots > 0:
            old_slot.used_slots -= 1
        appt.appointment_date = new_date
        new_slot.used_slots += 1
        result = 'accepted'
    elif decision == 'decline':
        result = 'declined'
    else:
        return jsonify({'error': 'Invalid response.'}), 400

    # mark the proposal answered on every copy
    copies = Message.query.filter_by(group_id=m.group_id).all() if m.group_id else [m]
    for r in copies:
        r.action_data = 'reschedule:done'

    body = 'Client %s the reschedule to %s.' % (result, new_date.strftime('%b %d, %Y'))
    gid = uuid.uuid4().hex
    staff = _staff_users()
    msgs = [Message(sender_id=current_user.id, receiver_id=s.id,
                    client_id=current_user.id, appointment_id=appt.id,
                    group_id=gid, message=body) for s in staff]
    db.session.add_all(msgs)
    db.session.commit()

    payload = {'client_id': current_user.id,
               'sender': current_user.full_name or current_user.name,
               'sender_id': current_user.id,
               'message': body, 'appt': _appt_payload(appt), 'gid': gid}
    for s, msg in zip(staff, msgs):
        p = dict(payload)
        p['id'] = msg.id
        push_to(s.id, 'new-message', p)
    notify_staff('appointment', 'Reschedule %s' % result.capitalize(),
                 '%s %s the reschedule to %s.' % (
                     current_user.full_name or current_user.name, result,
                     new_date.strftime('%b %d, %Y')),
                 link=url_for('appointments.index', status='pending'))

    return jsonify({'ok': True})


def _target_appointment(client, appt_id=None):
    """Context appointment, else the client's nearest upcoming active booking."""
    q = Appointment.query.filter(
        Appointment.client_id == client.id,
        Appointment.status.in_(['pending', 'confirmed']),
        Appointment.appointment_date >= date.today())
    if appt_id:
        a = q.filter(Appointment.id == appt_id).first()
        if a:
            return a
    return q.order_by(Appointment.appointment_date.asc()).first()


@bp.route('/interested', methods=['POST'])
@login_required
def interested():
    """Client marks a package/service as interesting -> attach to their booking."""
    if current_user.role != 'client':
        return jsonify({'error': 'forbidden'}), 403

    pkg = Package.query.get(request.form.get('package_id', type=int) or 0)
    svc = Service.query.get(request.form.get('service_id', type=int) or 0)
    if pkg and pkg.status != 1:
        pkg = None
    if svc and svc.status != 1:
        svc = None
    if not pkg and not svc:
        return jsonify({'error': 'Pick a package or service.'}), 400

    label = pkg.name if pkg else svc.name
    appt = _target_appointment(current_user,
                               request.form.get('appointment_id', type=int))
    body = 'Interested in %s: %s' % ('package' if pkg else 'service', label)
    staff = _staff_users()
    if not staff:
        return jsonify({'error': 'No staff available.'}), 400
    gid = uuid.uuid4().hex
    mk = dict(sender_id=current_user.id, client_id=current_user.id,
              appointment_id=None, group_id=gid, message=body,
              package_id=pkg.id if pkg else None,
              service_id=svc.id if svc else None)

    if not appt:
        # post the card so staff see the interest, then offer a booking
        msgs = [Message(receiver_id=s.id, **mk) for s in staff]
        db.session.add_all(msgs)
        db.session.commit()
        args = {'package': pkg.id} if pkg else {'service': svc.id}
        payload = {'client_id': current_user.id,
                   'sender': current_user.full_name or current_user.name,
                   'sender_id': current_user.id,
                   'message': body, 'appt': None, 'gid': gid,
                   'package': _pkg_payload(pkg), 'service': _svc_payload(svc)}
        for s, msg in zip(staff, msgs):
            p = dict(payload)
            p['id'] = msg.id
            push_to(s.id, 'new-message', p)
        return jsonify({'needs_booking': True, 'id': msgs[0].id, 'gid': gid,
                        'redirect': url_for('booking.new', **args)})
    if appt.status == 'confirmed':
        return jsonify({'locked': True})

    # attach item to the appointment (skip if the same item is already there)
    if pkg:
        exists = AppointmentItem.query.filter_by(
            appointment_id=appt.id, item_type='package', package_id=pkg.id).first()
        if not exists:
            db.session.add(AppointmentItem(
                appointment_id=appt.id, item_type='package',
                package_id=pkg.id, quantity=1, price=pkg.price))
    else:
        exists = AppointmentItem.query.filter_by(
            appointment_id=appt.id, item_type='service', service_id=svc.id).first()
        if not exists:
            db.session.add(AppointmentItem(
                appointment_id=appt.id, item_type='service',
                service_id=svc.id, quantity=1, price=svc.price))

    mk['appointment_id'] = appt.id
    msgs = [Message(receiver_id=s.id, **mk) for s in staff]
    db.session.add_all(msgs)
    db.session.commit()

    payload = {'client_id': current_user.id,
               'sender': current_user.full_name or current_user.name,
               'sender_id': current_user.id,
               'message': body, 'appt': _appt_payload(appt), 'gid': gid,
               'package': _pkg_payload(pkg), 'service': _svc_payload(svc)}
    for s, msg in zip(staff, msgs):
        p = dict(payload)
        p['id'] = msg.id
        push_to(s.id, 'new-message', p)

    return jsonify({'ok': True, 'id': msgs[0].id, 'gid': gid,
                    'appt': _appt_payload(appt), 'added_to': appt.id,
                    'already': bool(exists)})
