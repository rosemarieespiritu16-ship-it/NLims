"""Wipe all transactional + schedule data: receipts, appointments
(items/add-ons), payments, and salon availability slots.

Clients, services, workers, and chat messages are preserved
(messages keep their text; appointment links are detached).

Run once:  venv\\Scripts\\python.exe reset_transactions.py
"""
from app import create_app
from app.extensions import db
from app.models import Message


def reset():
    app = create_app()
    with app.app_context():
        # detach chat messages from appointments (history preserved)
        Message.query.update({Message.appointment_id: None})
        db.session.flush()

        db.session.execute(db.text('SET FOREIGN_KEY_CHECKS=0'))
        for table in ('receipts', 'appointment_items', 'appointment_addons',
                      'appointments', 'payments', 'salon_availability'):
            db.session.execute(db.text('TRUNCATE TABLE %s' % table))
        db.session.execute(db.text('SET FOREIGN_KEY_CHECKS=1'))
        db.session.commit()
        print('Transactions, ledger, and schedule wiped.')


if __name__ == '__main__':
    reset()
