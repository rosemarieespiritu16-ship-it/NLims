"""Reset the catalog: truncate services/products/packages and seed the
AIKELANTIK price list (Lashes + Nails only).

Run once:  venv\\Scripts\\python.exe reset_catalog.py
"""
from app import create_app
from app.extensions import db
from app.models import (Service, Product, Package, PackageItem, ServiceProduct,
                        StockHistory, Appointment, AppointmentItem,
                        AppointmentAddon, Message)
from seed_catalog import SERVICES


def reset():
    app = create_app()
    with app.app_context():
        # 1. detach historical references so FK checks stay clean
        AppointmentItem.query.update({AppointmentItem.service_id: None,
                                      AppointmentItem.package_id: None})
        Appointment.query.update({Appointment.service_id: None,
                                  Appointment.package_id: None})
        AppointmentAddon.query.update({AppointmentAddon.product_id: None})
        Message.query.update({Message.service_id: None,
                              Message.package_id: None})
        db.session.flush()

        # 2. truncate catalog tables
        db.session.execute(db.text('SET FOREIGN_KEY_CHECKS=0'))
        for table in ('stock_history', 'service_products', 'package_items',
                      'packages', 'products', 'services'):
            db.session.execute(db.text('TRUNCATE TABLE %s' % table))
        db.session.execute(db.text('SET FOREIGN_KEY_CHECKS=1'))

        # 3. seed the new service list
        for name, category, dur, price in SERVICES:
            db.session.add(Service(name=name, category=category,
                                   duration_minutes=dur, price=price,
                                   status=1))
        db.session.commit()
        print('Catalog reset: %d services seeded; products, packages, '
              'stock history cleared.' % len(SERVICES))


if __name__ == '__main__':
    reset()
