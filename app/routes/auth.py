import random
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from flask_login import login_user, logout_user, login_required, current_user
from ..models import User, PasswordResetToken
from ..extensions import db
from ..mail_helper import send_email
from ..audit import log

bp = Blueprint('auth', __name__, url_prefix='/auth')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and user.check_password(request.form['password']):
            if user.status == 0:
                flash('Your account is pending admin approval.', 'warning')
                return render_template('auth/login.html')
            login_user(user)
            session['user_role'] = user.role
            log('auth.login', '{} logged in'.format(user.email))
            if user.must_change_password:
                return redirect(url_for('auth.change_password'))
            return redirect(url_for('dashboard.index'))
        log('auth.login_fail', 'Failed login for {}'.format(request.form.get('email', '')))
        flash('Invalid credentials.', 'danger')
    return render_template('auth/login.html')


@bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        pw = request.form['password']
        confirm = request.form['confirm_password']
        if len(pw) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        elif pw != confirm:
            flash('Passwords do not match.', 'danger')
        else:
            current_user.set_password(pw)
            current_user.must_change_password = 0
            db.session.commit()
            log('auth.password_change', 'Changed password')
            flash('Password changed successfully. Welcome to InventorPro.', 'success')
            return redirect(url_for('dashboard.index'))

    return render_template('auth/change_password.html')


@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        user = User.query.filter_by(email=email).first()

        if user:
            PasswordResetToken.query.filter_by(email=email).delete(synchronize_session=False)
            db.session.commit()

            otp = str(random.SystemRandom().randint(100000, 999999))
            token = PasswordResetToken(email=email, otp=otp)
            db.session.add(token)
            db.session.commit()

            app_url = current_app.config.get('APP_URL', 'http://localhost:5000')
            try:
                html = render_template(
                    'emails/otp_reset.html',
                    name=user.first_name or user.name,
                    otp=otp,
                    app_url=app_url
                )
                send_email(
                    recipients=[email],
                    subject='Your InventorPro Password Reset Code',
                    html_body=html
                )
            except Exception as e:
                flash('Could not send reset email. Please contact support.', 'danger')
                return render_template('auth/forgot_password.html', email=email)

            session['reset_email'] = email
            flash('A 6-digit reset code has been sent to your email.', 'success')
            return redirect(url_for('auth.reset_password'))

        flash('If that email is registered, a reset code has been sent.', 'success')
        return render_template('auth/forgot_password.html', email=email)

    return render_template('auth/forgot_password.html')


@bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    email = session.get('reset_email') or ''

    token = PasswordResetToken.query.filter_by(
        email=email, used_at=None
    ).order_by(PasswordResetToken.created_at.desc()).first()

    if request.method == 'POST':
        digits = request.form.getlist('otp[]')
        otp = ''.join(digits).strip()

        token = PasswordResetToken.query.filter_by(
            email=email, otp=otp, used_at=None
        ).first()

        if not token or token.is_expired():
            flash('Invalid or expired reset code.', 'danger')
        else:
            session['reset_otp'] = otp
            flash('Code verified. Please set a new password.', 'success')
            return redirect(url_for('auth.new_password'))

    if not token or token.is_expired():
        flash('Your reset code is invalid or expired. Please request a new one.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    remaining = max(0, int((token.expires_at - datetime.utcnow()).total_seconds()))
    return render_template('auth/reset_password.html', email=email, remaining=remaining)


@bp.route('/new-password', methods=['GET', 'POST'])
def new_password():
    email = session.get('reset_email') or ''
    otp = session.get('reset_otp') or ''

    token = PasswordResetToken.query.filter_by(
        email=email, otp=otp, used_at=None
    ).first()

    if not token or token.is_expired():
        flash('Your reset code is invalid or expired. Please request a new one.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        pw = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if len(pw) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        elif pw != confirm:
            flash('Passwords do not match.', 'danger')
        else:
            user = User.query.filter_by(email=email).first()
            if not user:
                flash('No account found for that email.', 'danger')
                return redirect(url_for('auth.forgot_password'))

            user.set_password(pw)
            token.mark_used()
            db.session.commit()
            session.pop('reset_email', None)
            session.pop('reset_otp', None)
            log('auth.password_reset', 'Password reset via OTP for {}'.format(email))
            flash('Password reset successfully. Please log in.', 'success')
            return redirect(url_for('auth.login'))

    return render_template('auth/new_password.html', email=email)


@bp.route('/logout')
@login_required
def logout():
    log('auth.logout', '{} logged out'.format(current_user.email))
    logout_user()
    return redirect(url_for('auth.login'))
