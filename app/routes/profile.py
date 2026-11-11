import os
import uuid
from datetime import datetime
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, current_app)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from ..extensions import db
from ..audit import log

bp = Blueprint('profile', __name__, url_prefix='/profile')


def _save_image(image):
    if not image or not image.filename:
        return None
    ext = secure_filename(image.filename.rsplit('.', 1)[-1].lower())
    if ext not in ('png', 'jpg', 'jpeg', 'gif'):
        return None
    fname = f"{uuid.uuid4().hex}.{ext}"
    upload_dir = os.path.join(current_app.root_path, '..', 'assets', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    image.save(os.path.join(upload_dir, fname))
    return fname


@bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    u = current_user
    if request.method == 'POST':
        u.first_name = request.form.get('first_name', u.first_name)
        u.middle_name = request.form.get('middle_name', u.middle_name)
        u.last_name = request.form.get('last_name', u.last_name)
        u.phone = request.form.get('phone', u.phone)
        u.address = request.form.get('address', u.address)
        u.gender = request.form.get('gender', u.gender)
        bd = request.form.get('birthday', '').strip()
        if bd:
            try:
                u.birthday = datetime.strptime(bd, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid birthday format.', 'danger')
                return redirect(url_for('profile.index'))
        u.name = u.full_name or u.name
        fname = _save_image(request.files.get('image'))
        if fname:
            u.image = fname
        db.session.commit()
        log('profile.update', 'Updated own profile')
        flash('Profile updated.', 'success')
        return redirect(url_for('profile.index'))
    return render_template('profile/index.html', u=u)
