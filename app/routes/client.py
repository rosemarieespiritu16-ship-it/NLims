import os
import uuid
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from werkzeug.utils import secure_filename
from ..models import User
from ..extensions import db

bp = Blueprint('client', __name__)


@bp.route('/client/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        existing = User.query.filter_by(email=request.form['email']).first()
        if existing:
            flash('Email already registered.', 'warning')
            return redirect(url_for('client.register'))

        first_name = request.form['first_name']
        last_name = request.form['last_name']
        middle_name = request.form.get('middle_name', '')
        name = ' '.join(filter(None, [first_name, middle_name, last_name]))

        image_filename = None
        image = request.files.get('image')
        if image and image.filename:
            ext = secure_filename(image.filename.rsplit('.', 1)[-1].lower())
            if ext in ('png', 'jpg', 'jpeg', 'gif'):
                image_filename = f"{uuid.uuid4().hex}.{ext}"
                upload_dir = os.path.join(current_app.root_path, '..', 'assets', 'uploads')
                os.makedirs(upload_dir, exist_ok=True)
                image.save(os.path.join(upload_dir, image_filename))

        u = User(
            role='client',
            status=0,
            name=name,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            birthday=datetime.strptime(request.form['birthday'], '%Y-%m-%d').date() if request.form.get('birthday') else None,
            gender=request.form.get('gender'),
            email=request.form['email'],
            phone=request.form.get('phone'),
            address=request.form.get('address'),
            image=image_filename
        )
        # placeholder password until admin approves and sends real one
        u.set_password(str(uuid.uuid4()))
        db.session.add(u)
        db.session.commit()
        flash('Registration submitted. Your account is pending admin approval.', 'success')
        return redirect(url_for('client.register'))

    return render_template('client/register.html')
