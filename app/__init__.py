import os
from datetime import datetime
from flask import Flask
from .config import Config
from .extensions import db, login_manager, mail
from .models import User, Message, Notification
from .routes import (audit, auth, appointments, booking, client, clients,
                     dashboard, messages, notifications,
                     profile, schedule, services, settings, users, workers)


def _ensure_columns():
    """db.create_all() does not ALTER existing tables - add new columns if missing."""
    from sqlalchemy import text
    try:
        db_name = db.engine.url.database
        existing = {r[0] for r in db.session.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=:db AND TABLE_NAME='messages'"
        ), {'db': db_name})}
        if 'client_id' not in existing:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN client_id INT NULL"))
        if 'appointment_id' not in existing:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN appointment_id INT NULL"))
        if 'group_id' not in existing:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN group_id VARCHAR(36) NULL"))
        if 'attachment' not in existing:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN attachment VARCHAR(255) NULL"))
        if 'package_id' not in existing:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN package_id INT NULL"))
        if 'action_data' not in existing:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN action_data VARCHAR(255) NULL"))
        if 'service_id' not in existing:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN service_id INT NULL"))

        appt_cols = {r[0] for r in db.session.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=:db AND TABLE_NAME='appointments'"
        ), {'db': db_name})}
        if 'reservation_fee' not in appt_cols:
            db.session.execute(text("ALTER TABLE appointments ADD COLUMN reservation_fee DECIMAL(10,2) DEFAULT 0"))
        if 'reservation_method' not in appt_cols:
            db.session.execute(text("ALTER TABLE appointments ADD COLUMN reservation_method VARCHAR(20) NULL"))
        if 'reservation_reference' not in appt_cols:
            db.session.execute(text("ALTER TABLE appointments ADD COLUMN reservation_reference VARCHAR(64) NULL"))
        if 'reservation_paid_at' not in appt_cols:
            db.session.execute(text("ALTER TABLE appointments ADD COLUMN reservation_paid_at DATETIME NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='../assets', static_url_path='/assets')
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)

    with app.app_context():
        db.create_all()
        _ensure_columns()

    app.register_blueprint(audit.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(appointments.bp)
    app.register_blueprint(booking.bp)
    app.register_blueprint(client.bp)
    app.register_blueprint(clients.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(messages.bp)
    app.register_blueprint(notifications.bp)
    app.register_blueprint(profile.bp)
    app.register_blueprint(services.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(schedule.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(workers.bp)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        from flask import redirect, url_for
        return redirect(url_for('auth.login'))

    @app.context_processor
    def inject_now():
        from datetime import timezone, timedelta
        return {'now': datetime.now(timezone(timedelta(hours=8)))}

    @app.context_processor
    def inject_alerts():
        from flask_login import current_user
        from .models import Appointment
        if not current_user.is_authenticated:
            return {}
        data = {
            'unread_notif_count': Notification.query.filter_by(user_id=current_user.id, is_read=0).count(),
            'unread_msg_count': Message.query.filter_by(receiver_id=current_user.id, is_read=0).count(),
            'recent_notifs': Notification.query.filter_by(user_id=current_user.id)
                .order_by(Notification.created_at.desc()).limit(6).all()
        }
        if current_user.role != 'client':
            data['pending_appt_count'] = Appointment.query.filter_by(
                is_walkin=0, status='pending').count()
        return data

    @app.template_filter('ph_datetime')
    def ph_datetime_filter(value):
        if value is None:
            return ''
        from datetime import timezone, timedelta
        ph = timezone(timedelta(hours=8))
        return value.replace(tzinfo=ph).strftime('%Y-%m-%d %I:%M %p')

    return app
