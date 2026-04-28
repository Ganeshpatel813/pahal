<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
# CDGI Faculty Attendance System
**AI Face Recognition + GPS Campus-Lock Attendance**
*Chameli Devi Group of Institutions, Khandwa Road, Umri Khedi, Indore*

---

## Quick Start

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Run the server
python app.py

# 3. Open browser
http://localhost:5000
```

---

## Default Admin Login

> Admin account is auto-created on first run.

---

## Project Structure

```
cdgi_attendance/
├── app.py                  ← Flask backend (all routes + APIs)
├── database.py             ← SQLite setup + schema
├── requirements.txt        ← Python dependencies
├── attendance.db           ← Auto-created SQLite database
├── templates/              ← Jinja2 HTML templates
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│   ├── admin.html
│   ├── scan.html
│   ├── reports.html
│   ├── about.html
│   └── contact.html
└── static/
    ├── css/
    │   └── style.css
    ├── js/
    │   ├── auth.js
    │   └── face-scanner.js
    ├── faces/
    │   ├── registered/     ← Registration photos
    │   └── attendance/     ← Check-in/out photos
    └── images/
        └── cdgi_campus.jpg ← (optional) Campus hero image
```

---

## Security

- bcrypt password hashing (cost 12)
- Server-side GPS validation (cannot be spoofed)
- AI face recognition (128-dim neural descriptor)
- HttpOnly session cookies
- Rate limiting: 5 login attempts per 10 min
- No-cache headers prevent back-button bypass

---

## Admin Credentials

Change the password after first login from Admin Panel.
=======
# Attendance-System
Face recognition based attendance system .......

=======
# minor-project1
faculty attendance system
>>>>>>> 1969e5fe0629a9b9ac15ec265adb87f6a6b35d1c
=======
# Pahal
pahal project
>>>>>>> 8bc64e68e009d46c57064a2520113f1fd022fd3d
