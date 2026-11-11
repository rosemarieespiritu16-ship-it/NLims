"""Admin settings: reservation fee configuration."""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..models import Setting
from ..extensions import db
from ..audit import log

bp = Blueprint('settings', __name__, url_prefix='/settings')

DEFAULTS = {
    'reservation_mode': 'fixed',     # fixed | percent
    'reservation_amount': '150',     # pesos, fixed mode
    'reservation_percent': '50',     # 50 | 100, percent mode
}


def get_setting(key, default=None):
    row = Setting.query.get(key)
    if row is None or row.value is None:
        return DEFAULTS.get(key, default)
    return row.value


def set_setting(key, value):
    row = Setting.query.get(key)
    if row is None:
        row = Setting(key=key)
        db.session.add(row)
    row.value = str(value)


def reservation_fee_for(items_total):
    """Reservation fee for a given items total, per admin settings."""
    mode = get_setting('reservation_mode', 'fixed')
    if mode == 'percent':
        pct = float(get_setting('reservation_percent', '50') or 50)
        return round(float(items_total) * pct / 100.0, 2)
    return round(float(get_setting('reservation_amount', '150') or 0), 2)


@bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if current_user.role not in ['superadmin', 'staff']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        mode = request.form.get('reservation_mode', 'fixed')
        if mode not in ('fixed', 'percent'):
            mode = 'fixed'
        set_setting('reservation_mode', mode)
        set_setting('reservation_amount', request.form.get('reservation_amount', '150') or '150')
        pct = request.form.get('reservation_percent', '50')
        set_setting('reservation_percent', pct if pct in ('50', '100') else '50')
        db.session.commit()
        log('settings.save', 'Reservation fee: %s' % (
            mode if mode == 'fixed'
            else '%s%%' % request.form.get('reservation_percent', '50')))
        flash('Settings saved.', 'success')
        return redirect(url_for('settings.index'))

    return render_template('settings/index.html',
                           mode=get_setting('reservation_mode', 'fixed'),
                           amount=get_setting('reservation_amount', '150'),
                           percent=get_setting('reservation_percent', '50'))
