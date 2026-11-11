"""Seed services, products, and packages for AIKELANTIK nail & lash business."""
from app import create_app
from app.extensions import db
from app.models import Service, Product, Package, PackageItem

SERVICES = [
    # Lashes
    ("Korean Lash Lift with Tint", "Lashes", 60, 300),
    ("Classic", "Lashes", 90, 350),
    ("Mascara Look", "Lashes", 90, 350),
    ("Hybrid", "Lashes", 105, 450),
    ("Cateye", "Lashes", 105, 450),
    ("Dolleye", "Lashes", 105, 450),
    ("Volume", "Lashes", 120, 450),
    ("Wispy", "Lashes", 120, 500),
    ("Mega Volume", "Lashes", 150, 600),
    ("Removal", "Lashes", 30, 100),
    ("Retouch", "Lashes", 60, 200),
    # Nails
    ("Gel Plain", "Nails", 45, 300),
    ("Gel with Design", "Nails", 60, 500),
    ("Extension Plain", "Nails", 75, 500),
    ("Minimal", "Nails", 90, 600),
    ("Full", "Nails", 90, 650),
    ("Removal Gel", "Nails", 20, 50),
    ("Removal Soft Gel", "Nails", 30, 150),
]

PRODUCTS = []

PACKAGES = []


def seed():
    app = create_app()
    with app.app_context():
        # Services
        for name, category, dur, price in SERVICES:
            if not Service.query.filter_by(name=name).first():
                db.session.add(Service(name=name, category=category, duration_minutes=dur, price=price, status=1))
        # Products
        for name, category, unit, price in PRODUCTS:
            if not Product.query.filter_by(name=name).first():
                db.session.add(Product(name=name, category=category, unit=unit, quantity=0, low_stock_threshold=5, price=price, status=1))
        db.session.commit()

        # Packages
        for pkg_name, desc, price, items in PACKAGES:
            if not Package.query.filter_by(name=pkg_name).first():
                p = Package(name=pkg_name, description=desc, price=price, status=1)
                db.session.add(p)
                db.session.flush()
                for itype, item_name, qty in items:
                    pi = PackageItem(package_id=p.id, item_type=itype, quantity=qty)
                    if itype == 'service':
                        s = Service.query.filter_by(name=item_name).first()
                        if s:
                            pi.service_id = s.id
                    else:
                        pr = Product.query.filter_by(name=item_name).first()
                        if pr:
                            pi.product_id = pr.id
                    db.session.add(pi)
        db.session.commit()
        print("Seed complete: services, products, packages added.")


if __name__ == '__main__':
    seed()
