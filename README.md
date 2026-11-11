# InventorPro - Flask Setup Guide

A Python/Flask inventory management system for AIKELANTIK nail and lash supplies, using SB Admin 2 for the admin UI.

## Requirements

- Python 3.10 or newer
- pip
- MySQL/MariaDB (XAMPP or any local server)
- A web browser

## Installation

1. **Place the project folder**

   Copy or move this folder so the project root is `C:\xampp\htdocs\NLims` or any folder you prefer.

2. **Create a virtual environment**

   Open a terminal in the project root and run:

   ```powershell
   python -m venv venv
   ```

3. **Install dependencies**

   ```powershell
   venv\Scripts\pip install -r requirements.txt
   ```

4. **Configure the environment**

   Copy `.env.example` to `.env` and fill in your real values:

   ```powershell
   copy .env.example .env
   ```

   At minimum set these:

   ```env
   SECRET_KEY=your-secret-key-here
   DATABASE_URL=mysql+pymysql://root:@localhost/inventorpro
   ```

   Optional but recommended:

   - `SMTP_*` for Gmail alerts
   - `PUSHER_*` for real-time chat
   - `PAYMONGO_*` for GCash test payments

5. **Create the database**

   Open phpMyAdmin or MySQL CLI and create a database called `inventorpro`.

6. **Create tables and seed the superadmin**

   ```powershell
   venv\Scripts\python init_db.py
   ```

   This creates the superadmin account:

   - Email: `admin@inventorpro.local`
   - Password: `admin123`

7. **Run the app**

   ```powershell
   venv\Scripts\python app.py
   ```
cd /c/xampp/htdocs/NLims
source venv/Scripts/activate
python app.py 

venv/Scripts/python app.py

8. **Open in browser**

   Go to `http://localhost:5000`.

## Project Structure

- `app.py` — Flask entry point
- `init_db.py` — create tables and seed data
- `app/__init__.py` — app factory
- `app/config.py` — environment config
- `app/models.py` — SQLAlchemy models
- `app/extensions.py` — Flask extensions
- `app/routes/` — blueprints (auth, dashboard, products, stock, pos, messages)
- `app/templates/` — Jinja2 templates
- `assets/sb-admin-2/` — Bootstrap admin theme

## Optional Integrations

### Gmail SMTP

Use a Gmail app password, not your normal Gmail password, and set:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
SMTP_FROM=noreply@yourdomain.com
```

### Pusher Real-Time Chat

Set your Pusher dev credentials in `.env` to enable live chat.

### PayMongo GCash Test Payments

Add your PayMongo test secret key and use the POS page to create a sandbox GCash checkout.

## Troubleshooting

- **Database connection error**: Make sure MySQL is running and `DATABASE_URL` is correct.
- **venv not found**: Make sure the path is `venv\Scripts\python` on Windows or `venv/bin/python` on Linux/macOS.
- **Port 5000 in use**: Change `app.run(port=...)` in `app.py` or set `FLASK_RUN_PORT`.


TRUNCATE table appointments;
TRUNCATE table appointment_items;
TRUNCATE table audit_logs;
TRUNCATE table messages;
TRUNCATE table notifications;
TRUNCATE table password_reset_tokens;
TRUNCATE TABLE receipts;
TRUNCATE TABLE salon_availability;
