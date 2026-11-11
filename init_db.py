import sys
from app import create_app
from app.extensions import db
from app.models import User, Product

app = create_app()

with app.app_context():
    if '--reset' in sys.argv:
        db.drop_all()
        print('Dropped all tables.')
    db.create_all()

    u = User.query.filter_by(email='admin@inventorpro.local').first()
    if not u:
        u = User(
            name='Administrator',
            first_name='Admin',
            last_name='Administrator',
            email='admin@inventorpro.local',
            role='superadmin',
            status=1
        )
        db.session.add(u)
    u.set_password('admin123')
    db.session.commit()
    print('Superadmin ready.')

    if Product.query.count() == 0:
        db.session.add_all([
            Product(name='Gel Nail Polish', category='Nail', unit='bottle', quantity=50, low_stock_threshold=10, price=150, status=1),
            Product(name='Lash Extensions', category='Lash', unit='box', quantity=30, low_stock_threshold=5, price=1200, status=1),
        ])
        db.session.commit()
        print('Sample products created.')

    print('Done.')
