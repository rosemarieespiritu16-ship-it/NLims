from datetime import date, datetime, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), default='client')
    name = db.Column(db.String(255))
    first_name = db.Column(db.String(100))
    middle_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    birthday = db.Column(db.Date)
    gender = db.Column(db.String(20))
    email = db.Column(db.String(100), unique=True)
    phone = db.Column(db.String(30))
    address = db.Column(db.Text)
    image = db.Column(db.String(255))
    password = db.Column(db.String(255))
    status = db.Column(db.Integer, default=1)
    must_change_password = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    @property
    def is_active(self):
        return self.status == 1

    @property
    def full_name(self):
        return ' '.join(filter(None, [self.first_name, self.middle_name, self.last_name]))

    def set_password(self, pw):
        self.password = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password, pw)


class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    category = db.Column(db.String(100))
    unit = db.Column(db.String(50))
    quantity = db.Column(db.Integer, default=0)
    low_stock_threshold = db.Column(db.Integer, default=0)
    price = db.Column(db.Numeric(10, 2), default=0)
    status = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


class StockHistory(db.Model):
    __tablename__ = 'stock_history'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    type = db.Column(db.String(20))
    quantity = db.Column(db.Integer)
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    amount = db.Column(db.Numeric(10, 2))
    method = db.Column(db.String(20))
    status = db.Column(db.String(20))
    reference = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'), nullable=True)
    group_id = db.Column(db.String(36), nullable=True, index=True)  # links copies of one logical message
    attachment = db.Column(db.String(255), nullable=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), nullable=True)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=True)
    action_data = db.Column(db.String(255), nullable=True)  # e.g. 'reschedule:2026-09-20' or 'reschedule:done'
    message = db.Column(db.Text)
    is_read = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    sender = db.relationship('User', foreign_keys=[sender_id])
    appointment = db.relationship('Appointment')
    package = db.relationship('Package')
    service = db.relationship('Service')


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    type = db.Column(db.String(20), default='system')  # appointment, message, system
    title = db.Column(db.String(255))
    body = db.Column(db.Text)
    link = db.Column(db.String(255))
    is_read = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    user = db.relationship('User', backref=db.backref('notifications', lazy=True))


class Setting(db.Model):
    __tablename__ = 'settings'
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text)


class Service(db.Model):
    __tablename__ = 'services'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    category = db.Column(db.String(100))
    duration_minutes = db.Column(db.Integer, default=30)
    price = db.Column(db.Numeric(10, 2), default=0)
    status = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    products = db.relationship('ServiceProduct', backref='service', lazy=True, cascade='all, delete-orphan')


class ServiceProduct(db.Model):
    __tablename__ = 'service_products'
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    quantity_used = db.Column(db.Integer, default=1)

    product = db.relationship('Product')


class Worker(db.Model):
    __tablename__ = 'workers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(100))
    phone = db.Column(db.String(30))
    specialty = db.Column(db.String(255))
    notes = db.Column(db.Text)
    status = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class SalonAvailability(db.Model):
    __tablename__ = 'salon_availability'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date)
    slot_type = db.Column(db.String(20))  # 'client' or 'walkin'
    max_slots = db.Column(db.Integer, default=0)
    used_slots = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    @property
    def remaining(self):
        return max(0, self.max_slots - self.used_slots)


class Package(db.Model):
    __tablename__ = 'packages'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    description = db.Column(db.Text)
    price = db.Column(db.Numeric(10, 2), default=0)
    status = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    items = db.relationship('PackageItem', backref='package', lazy=True, cascade='all, delete-orphan')


class PackageItem(db.Model):
    __tablename__ = 'package_items'
    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'))
    item_type = db.Column(db.String(20))  # 'service' or 'product'
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    quantity = db.Column(db.Integer, default=1)

    service = db.relationship('Service')
    product = db.relationship('Product')


class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    worker_id = db.Column(db.Integer, db.ForeignKey('workers.id'), nullable=True)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), nullable=True)
    appointment_date = db.Column(db.Date)
    appointment_code = db.Column(db.String(20))
    status = db.Column(db.String(20), default='pending')  # pending, confirmed, completed, cancelled, voided, no_show
    transaction_number = db.Column(db.String(50))
    total_amount = db.Column(db.Numeric(10, 2), default=0)
    payment_status = db.Column(db.String(20), default='unpaid')  # unpaid, paid, voided
    payment_method = db.Column(db.String(20))  # cash, gcash
    payment_reference = db.Column(db.String(255))
    reservation_fee = db.Column(db.Numeric(10, 2), default=0)
    reservation_method = db.Column(db.String(20))
    reservation_reference = db.Column(db.String(64))
    reservation_paid_at = db.Column(db.DateTime)
    is_walkin = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    client = db.relationship('User', backref=db.backref('appointments', lazy=True))
    worker = db.relationship('Worker', backref=db.backref('appointments', lazy=True))
    service = db.relationship('Service', backref=db.backref('appointments', lazy=True))
    package = db.relationship('Package', backref=db.backref('appointments', lazy=True))
    addons = db.relationship('AppointmentAddon', backref='appointment', lazy=True, cascade='all, delete-orphan')


class AppointmentAddon(db.Model):
    __tablename__ = 'appointment_addons'
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    quantity = db.Column(db.Integer, default=1)
    price = db.Column(db.Numeric(10, 2), default=0)
    is_reserved = db.Column(db.Integer, default=1)  # 1 = reserved/deducted, 0 = voided/restored

    product = db.relationship('Product')


class AppointmentItem(db.Model):
    __tablename__ = 'appointment_items'
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'))
    item_type = db.Column(db.String(20))  # 'service' or 'package'
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), nullable=True)
    quantity = db.Column(db.Integer, default=1)
    price = db.Column(db.Numeric(10, 2), default=0)

    appointment = db.relationship('Appointment', backref=db.backref('items', lazy=True, cascade='all, delete-orphan'))
    service = db.relationship('Service')
    package = db.relationship('Package')


class Receipt(db.Model):
    __tablename__ = 'receipts'
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'))
    transaction_number = db.Column(db.String(50))
    total = db.Column(db.Numeric(10, 2))
    amount_paid = db.Column(db.Numeric(10, 2), default=0)
    change_amount = db.Column(db.Numeric(10, 2), default=0)
    payment_method = db.Column(db.String(20))
    reference = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    appointment = db.relationship('Appointment', backref=db.backref('receipt', uselist=False, lazy=True))


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False, index=True)
    otp = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(minutes=15))
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def is_expired(self):
        return datetime.utcnow() > self.expires_at

    def mark_used(self):
        self.used_at = datetime.utcnow()


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    user_name = db.Column(db.String(255))
    action = db.Column(db.String(60))
    details = db.Column(db.Text)
    ip = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    user = db.relationship('User')
