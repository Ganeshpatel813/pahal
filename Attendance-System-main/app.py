"""
CDGI Faculty Attendance System - Flask Backend (MySQL)
=======================================================
All DB calls use mysql-connector-python cursor API.
Placeholders: %s  (NOT ? like SQLite)
Date filter:  DATE_FORMAT(col,'%Y-%m')  (NOT strftime)
"""

from flask import (Flask, request, jsonify, render_template,
                   session, redirect, send_from_directory, make_response)
from flask.json.provider import DefaultJSONProvider
from database import get_db, init_db, REGISTERED_FACES_DIR, ATTENDANCE_FACES_DIR
import bcrypt, json, os, base64, re, math, calendar
from datetime import datetime, date, timedelta
from functools import wraps
import pytz


class MySQLJSONProvider(DefaultJSONProvider):
    """Serialize MySQL-specific types that Flask's default encoder can't handle."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(obj, date):
            return obj.strftime("%Y-%m-%d")
        if isinstance(obj, timedelta):
            # MySQL TIME columns come back as timedelta
            total = int(obj.total_seconds())
            h, rem = divmod(abs(total), 3600)
            m, s   = divmod(rem, 60)
            return f"{h:02d}:{m:02d}:{s:02d}"
        return super().default(obj)


app = Flask(__name__)
app.json_provider_class = MySQLJSONProvider
app.json = MySQLJSONProvider(app)
app.secret_key = os.environ.get("SECRET_KEY", "CDGI_ATT_xK9mP3qLvR7nZ2jQ_2025")
app.config.update(
    SESSION_COOKIE_HTTPONLY  = True,
    SESSION_COOKIE_SAMESITE  = "Lax",
    SESSION_COOKIE_SECURE    = False,
    PERMANENT_SESSION_LIFETIME = 28800,
)

IST = pytz.timezone("Asia/Kolkata")

COLLEGE_NAME   = "Chameli Devi Group of Institutions"
COLLEGE_ADDR   = "Gram Umrikheda, Khandwa Road, Indore - 452020"
COLLEGE_LAT    = 22.6149   # Verified from live faculty GPS on campus
COLLEGE_LNG    = 75.8889
MAX_RADIUS_M   = 50         # 50m strict — only inside campus gate

# Campus polygon — CDGI campus boundary
# SW corner = main gate — pulled inward to stop at gate, not outside
CAMPUS_POLYGON = [
    (22.6164, 75.8872),   # NW corner
    (22.6164, 75.8906),   # NE corner
    (22.6134, 75.8906),   # SE corner
    (22.6140, 75.8878),   # SW — main gate (pulled inward from 22.6134,75.8872)
    (22.6164, 75.8872),   # close polygon (back to NW)
]

HALF_DAY_HOURS = 4.0
_login_attempts: dict = {}

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
PHONE_RE = re.compile(r'^[6-9]\d{9}$')
EMP_RE   = re.compile(r'^[A-Z0-9]{3,15}$')


# ── helpers ──────────────────────────────────────────────────────────────────

def qry(conn, sql, params=None):
    """Execute a query and return (cursor, rows). Caller must close cursor."""
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params or [])
    return cur

def fetchone(conn, sql, params=None):
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params or [])
    row = cur.fetchone()
    cur.close()
    return row

def fetchall(conn, sql, params=None):
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params or [])
    rows = cur.fetchall()
    cur.close()
    return rows

def execute(conn, sql, params=None):
    """Execute DML; returns lastrowid."""
    cur = conn.cursor()
    cur.execute(sql, params or [])
    lid = cur.lastrowid
    cur.close()
    return lid

def scalar(conn, sql, params=None):
    """Return first column of first row."""
    cur = conn.cursor()
    cur.execute(sql, params or [])
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def validate_email(e):
    if not e: return "Email is required."
    if not EMAIL_RE.match(e): return "Invalid email. Use format: name@domain.com"
    return None

def validate_phone(p):
    if p and not PHONE_RE.match(p): return "Invalid phone. Enter 10-digit Indian mobile (6-9 start)."
    return None

def validate_employee_id(e):
    if not e: return "Employee ID is required."
    if not EMP_RE.match(e): return f"Employee ID must be 3-15 uppercase letters/digits. '{e}' is invalid."
    return None

def validate_password(p):
    if len(p) < 8:                    return "Password must be at least 8 characters."
    if not re.search(r'[A-Z]', p):    return "Password must contain at least one uppercase letter."
    if not re.search(r'[0-9]', p):    return "Password must contain at least one digit."
    return None

def ist_now():   return datetime.now(IST)
def ist_today(): return ist_now().strftime("%Y-%m-%d")
def ist_hms():   return ist_now().strftime("%H:%M:%S")

def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def point_in_polygon(lat: float, lng: float, polygon: list) -> bool:
    """Ray-casting algorithm — checks if point is inside polygon."""
    x, y = lat, lng
    n = len(polygon)
    inside = False
    px, py = polygon[0]
    for i in range(1, n + 1):
        qx, qy = polygon[i % n]
        if ((py > y) != (qy > y)) and (x < (qx - px) * (y - py) / (qy - py) + px):
            inside = not inside
        px, py = qx, qy
    return inside


def inside_campus(lat: float, lng: float) -> tuple:
    """Returns (distance_metres, is_inside_campus). Radius-only check."""
    try:
        dist = round(haversine(lat, lng, COLLEGE_LAT, COLLEGE_LNG), 1)
    except Exception:
        dist = 0.0
    return dist, dist <= MAX_RADIUS_M

def save_b64_image(b64: str, filepath: str) -> bool:
    try:
        data = b64.split(",", 1)[1] if "," in b64 else b64
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(data))
        return True
    except Exception as e:
        print(f"[IMG] {e}")
        return False

def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)

def working_hours(ci: str, co: str) -> float:
    if not ci or not co:
        return 0.0
    try:
        fmt = "%H:%M:%S"
        secs = (datetime.strptime(co, fmt) - datetime.strptime(ci, fmt)).total_seconds()
        return round(max(secs / 3600, 0), 2)
    except Exception:
        return 0.0

def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"]        = "no-cache"
    response.headers["Expires"]       = "0"
    response.headers["X-Frame-Options"] = "DENY"
    return response

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "faculty_id" not in session:
            if request.is_json:
                return jsonify({"error": "Authentication required."}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "faculty_id" not in session or session.get("role") != "admin":
            if request.is_json:
                return jsonify({"error": "Admin access required."}), 403
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper


# ── PAGE ROUTES ──────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return no_cache(make_response(render_template("index.html")))

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")

@app.route("/login")
def login_page():
    if "faculty_id" in session:
        return redirect("/admin" if session.get("role") == "admin" else "/dashboard")
    return no_cache(make_response(render_template("login.html")))

@app.route("/register")
def register_page():
    return render_template("register.html")

@app.route("/dashboard")
@login_required
def dashboard():
    if session.get("role") == "admin":
        return redirect("/admin")
    return no_cache(make_response(render_template("dashboard.html")))

@app.route("/admin")
@admin_required
def admin_page():
    return no_cache(make_response(render_template("admin.html")))

@app.route("/scan")
@login_required
def scan():
    return no_cache(make_response(render_template("scan.html")))

@app.route("/reports")
@login_required
def reports():
    return render_template("reports.html")

@app.route("/logout")
def logout():
    session.clear()
    resp = make_response(redirect("/login"))
    resp.delete_cookie("session")
    return no_cache(resp)

@app.route("/static/faces/<folder>/<filename>")
@login_required
def serve_face(folder, filename):
    return send_from_directory(os.path.join("static", "faces", folder), filename)


# ── AUTH APIS ────────────────────────────────────────────────────────────────

@app.route("/api/auth/check-empid")
def check_empid():
    """Real-time Employee ID availability check used by register & admin-add forms."""
    emp_id = request.args.get("id", "").strip().upper()
    if not emp_id:
        return jsonify({"available": False, "status": "error", "message": "Employee ID is required."})

    conn = get_db()
    try:
        # 1. Check permanent blacklist (deleted faculty)
        bl = fetchone(conn,
            "SELECT employee_id, former_name FROM emp_id_blacklist WHERE UPPER(employee_id)=%s",
            [emp_id])
        if bl:
            return jsonify({
                "available": False,
                "status":    "reserved",
                "message":   (
                    f"🚫 Employee ID '{emp_id}' is permanently reserved. "
                    f"It was previously assigned to {bl['former_name']} who has left the institution. "
                    f"This ID cannot be reused. Please use a different ID."
                )
            }), 409

        # 2. Check active/inactive faculty table
        row = fetchone(conn,
            "SELECT employee_id, name, is_active FROM faculty WHERE UPPER(employee_id)=%s",
            [emp_id])
    finally:
        conn.close()

    if not row:
        return jsonify({"available": True, "status": "available",
                        "message": f"✅ Employee ID '{emp_id}' is available."})

    if row["is_active"] == 0:
        return jsonify({
            "available": False,
            "status":    "reserved",
            "message":   (
                f"🚫 Employee ID '{emp_id}' is reserved. "
                f"This ID belongs to {row['name']} who is currently deactivated. "
                f"Reserved IDs cannot be reused. Please use a different ID."
            )
        }), 409

    return jsonify({
        "available": False,
        "status":    "taken",
        "message":   f"❌ Employee ID '{emp_id}' is already registered to an active faculty member."
    }), 409


@app.route("/api/auth/register", methods=["POST"])
def register():
    d           = request.json or {}
    name        = d.get("name", "").strip()
    employee_id = d.get("employeeId", "").strip().upper()
    email       = d.get("email", "").strip().lower()
    phone       = d.get("phone", "").strip()
    department  = d.get("department", "").strip()
    designation = d.get("designation", "").strip()
    password    = d.get("password", "")
    face_desc   = d.get("faceDescriptor")
    face_b64    = d.get("faceImage", "")
    college_id  = d.get("collegeId") or None
    program_id  = d.get("programId") or None

    errors = {}
    if not name or len(name) < 2:               errors["name"]        = "Full name must be at least 2 characters."
    if not department:                           errors["department"]  = "Please select a department."
    if not designation or len(designation) < 2: errors["designation"] = "Designation is required."

    emp_err   = validate_employee_id(employee_id)
    email_err = validate_email(email)
    phone_err = validate_phone(phone)
    pwd_err   = validate_password(password)
    if emp_err:   errors["employeeId"] = emp_err
    if email_err: errors["email"]      = email_err
    if phone_err: errors["phone"]      = phone_err
    if pwd_err:   errors["password"]   = pwd_err

    if not face_desc or not isinstance(face_desc, list) or len(face_desc) != 128:
        errors["face"] = "Face registration required. Please scan your face properly."

    if errors:
        return jsonify({"errors": errors}), 422

    conn = get_db()
    try:
        # Check permanent blacklist first
        bl = fetchone(conn,
            "SELECT former_name FROM emp_id_blacklist WHERE UPPER(employee_id)=%s", [employee_id])
        if bl:
            return jsonify({"errors": {"employeeId":
                f"🚫 Employee ID '{employee_id}' is permanently reserved. "
                f"It was previously assigned to {bl['former_name']} who has left the institution. "
                f"This ID cannot be reused. Please use a different ID."
            }}), 409

        existing = fetchone(conn, "SELECT name, is_active FROM faculty WHERE UPPER(employee_id)=%s", [employee_id])
        if existing:
            if existing["is_active"] == 0:
                return jsonify({"errors": {"employeeId":
                    f"🚫 Employee ID '{employee_id}' is reserved. "
                    f"This ID belongs to {existing['name']} who is currently deactivated. "
                    f"Reserved IDs cannot be reused. Please use a different ID."
                }}), 409
            return jsonify({"errors": {"employeeId":
                f"❌ Employee ID '{employee_id}' is already registered."
            }}), 409
        if fetchone(conn, "SELECT id FROM faculty WHERE LOWER(email)=%s", [email]):
            return jsonify({"errors": {"email": f"Email '{email}' is already registered."}}), 409

        existing_faces = fetchall(conn,
            "SELECT employee_id, name, face_descriptor FROM faculty "
            "WHERE face_descriptor IS NOT NULL AND has_face_registered=1")

        def _euclid(a, b):
            return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

        for ef in existing_faces:
            try:
                stored = json.loads(ef["face_descriptor"])
                if _euclid(face_desc, stored) < 0.50:
                    return jsonify({"errors": {"face": (
                        f"This face is already registered to another account "
                        f"({ef['name']} / {ef['employee_id']}). "
                        f"Each faculty member must use their own unique face.")}}), 409
            except Exception:
                continue

        pw_hash   = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        desc_json = json.dumps(face_desc)

        img_path = ""
        if face_b64:
            fn = f"{safe_name(employee_id)}.jpg"
            fp = os.path.join(REGISTERED_FACES_DIR, fn)
            if save_b64_image(face_b64, fp):
                img_path = "/" + fp.replace("\\", "/")

        fid = execute(conn,
            """INSERT INTO faculty
               (employee_id,name,email,phone,department,designation,
                password_hash,face_descriptor,face_image_path,has_face_registered,
                college_id,program_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)""",
            [employee_id, name, email, phone, department, designation,
             pw_hash, desc_json, img_path, college_id, program_id])
        conn.commit()
    finally:
        conn.close()

    return jsonify({"id": fid, "name": name, "employeeId": employee_id,
                    "message": "Registration successful!"}), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    d           = request.json or {}
    employee_id = d.get("employeeId", "").strip().upper()
    password    = d.get("password", "")
    ip          = request.remote_addr

    now    = datetime.utcnow()
    bucket = _login_attempts.get(ip, {"count": 0, "reset": now + timedelta(minutes=10)})
    if now > bucket["reset"]:
        bucket = {"count": 0, "reset": now + timedelta(minutes=10)}
    bucket["count"] += 1
    _login_attempts[ip] = bucket
    if bucket["count"] > 5:
        wait = int((bucket["reset"] - now).total_seconds() / 60) + 1
        return jsonify({"error": f"Too many login attempts. Please wait {wait} minute(s)."}), 429

    if not employee_id or not password:
        return jsonify({"error": "Employee ID and password are required."}), 400

    conn = get_db()
    try:
        row = fetchone(conn,
            "SELECT * FROM faculty WHERE UPPER(employee_id)=%s AND is_active=1", [employee_id])
    finally:
        conn.close()

    if not row or not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return jsonify({"error": "Invalid Employee ID or Password."}), 401

    _login_attempts.pop(ip, None)
    session.permanent = True
    session["faculty_id"]   = row["id"]
    session["employee_id"]  = row["employee_id"]
    session["faculty_name"] = row["name"]
    session["role"]         = row["role"]

    return jsonify({"id": row["id"], "name": row["name"],
                    "employeeId": row["employee_id"], "role": row["role"]})


@app.route("/api/auth/me")
@login_required
def get_me():
    conn = get_db()
    try:
        row = fetchone(conn,
            "SELECT * FROM faculty WHERE id=%s AND is_active=1", [session["faculty_id"]])
    finally:
        conn.close()
    if not row:
        session.clear()
        return jsonify({"error": "User not found."}), 404
    desc = json.loads(row["face_descriptor"]) if row["face_descriptor"] else None
    return jsonify({
        "id":               row["id"],
        "employeeId":       row["employee_id"],
        "name":             row["name"],
        "email":            row["email"],
        "phone":            row["phone"],
        "department":       row["department"],
        "designation":      row["designation"],
        "faceDescriptor":   desc,
        "faceImagePath":    row["face_image_path"],
        "hasFaceRegistered": bool(row["has_face_registered"]),
        "role":             row["role"],
        "createdAt":        str(row["created_at"]),
    })


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"message": "Logged out."})


@app.route("/api/college/info")
def college_info():
    return jsonify({"name": COLLEGE_NAME, "address": COLLEGE_ADDR,
                    "lat": COLLEGE_LAT, "lng": COLLEGE_LNG, "radius": MAX_RADIUS_M})


@app.route("/api/location/validate", methods=["POST"])
@login_required
def validate_location():
    d = request.json or {}
    try:
        lat = float(d.get("latitude",  0))
        lng = float(d.get("longitude", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid coordinates."}), 400
    dist, ok = inside_campus(lat, lng)
    if not ok:
        return jsonify({
            "insideCampus": False,
            "distance":     dist,
            "maxRadius":    MAX_RADIUS_M,
            "college":      COLLEGE_NAME,
            "message":      f"You are {dist}m away from {COLLEGE_NAME}. Must be within {MAX_RADIUS_M}m of campus."
        }), 403
    return jsonify({
        "insideCampus": True,
        "distance":     dist,
        "maxRadius":    MAX_RADIUS_M,
        "college":      COLLEGE_NAME,
        "message":      f"Location verified — {COLLEGE_NAME}"
    })


# ── ATTENDANCE CHECK-IN ───────────────────────────────────────────────────────

@app.route("/api/attendance/check-in", methods=["POST"])
@login_required
def check_in():
    d             = request.json or {}
    faculty_id    = session["faculty_id"]
    employee_id   = session["employee_id"]
    face_verified = bool(d.get("faceVerified", False))
    image_b64     = d.get("capturedImage", "")

    try:
        lat = float(d.get("latitude",  0))
        lng = float(d.get("longitude", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid GPS coordinates."}), 400

    if not face_verified:
        return jsonify({"error": "Face verification failed. Please scan again."}), 400

    dist, ok = inside_campus(lat, lng)
    if not ok:
        return jsonify({"error": (
            f"Location not verified. You are {dist}m from {COLLEGE_NAME}. "
            f"Must be inside campus (within {MAX_RADIUS_M}m).")}), 403

    today = ist_today()
    conn  = get_db()
    try:
        # Check if there's an open session (checked in but not checked out)
        open_session = fetchone(conn,
            "SELECT id FROM attendance_sessions WHERE faculty_id=%s AND session_date=%s "
            "AND check_out_time IS NULL ORDER BY created_at DESC LIMIT 1",
            [faculty_id, today])
        if open_session:
            return jsonify({"error": "You have an open session. Please check out first before checking in again."}), 409

        # Get or create today's attendance record
        att = fetchone(conn,
            "SELECT id FROM attendance WHERE faculty_id=%s AND attendance_date=%s",
            [faculty_id, today])
        if not att:
            att_id = execute(conn,
                """INSERT INTO attendance
                   (faculty_id,employee_id,attendance_date,status)
                   VALUES (%s,%s,%s,'present')""",
                [faculty_id, employee_id, today])
        else:
            att_id = att["id"]

        # Create new session
        time_now = ist_hms()
        img_path = ""
        if image_b64:
            ts = ist_now().strftime("%Y%m%d_%H%M%S")
            fn = f"{safe_name(employee_id)}_in_{ts}.jpg"
            fp = os.path.join(ATTENDANCE_FACES_DIR, fn)
            if save_b64_image(image_b64, fp):
                img_path = "/" + fp.replace("\\", "/")

        execute(conn,
            """INSERT INTO attendance_sessions
               (attendance_id,faculty_id,session_date,check_in_time,
                check_in_image_path,check_in_lat,check_in_lng)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            [att_id, faculty_id, today, time_now, img_path, lat, lng])

        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": "Check-in recorded!", "time": time_now, "date": today}), 201


# ── ATTENDANCE CHECK-OUT ──────────────────────────────────────────────────────

@app.route("/api/attendance/check-out", methods=["POST"])
@login_required
def check_out():
    d             = request.json or {}
    faculty_id    = session["faculty_id"]
    employee_id   = session["employee_id"]
    face_verified = bool(d.get("faceVerified", False))
    image_b64     = d.get("capturedImage", "")

    try:
        lat = float(d.get("latitude",  0))
        lng = float(d.get("longitude", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid GPS coordinates."}), 400

    if not face_verified:
        return jsonify({"error": "Face verification failed. Please scan again."}), 400

    dist, ok = inside_campus(lat, lng)
    if not ok:
        return jsonify({"error": (
            f"Location not verified. You are {dist}m from {COLLEGE_NAME}. "
            f"Check-out must be done inside campus.")}), 403

    today = ist_today()
    conn  = get_db()
    try:
        # Find the latest open session
        open_session = fetchone(conn,
            "SELECT * FROM attendance_sessions WHERE faculty_id=%s AND session_date=%s "
            "AND check_out_time IS NULL ORDER BY created_at DESC LIMIT 1",
            [faculty_id, today])

        if not open_session:
            return jsonify({"error": "No open check-in found. Please check in first."}), 400

        time_now  = ist_hms()
        sess_hrs  = working_hours(str(open_session["check_in_time"]), time_now)

        img_path = ""
        if image_b64:
            ts = ist_now().strftime("%Y%m%d_%H%M%S")
            fn = f"{safe_name(employee_id)}_out_{ts}.jpg"
            fp = os.path.join(ATTENDANCE_FACES_DIR, fn)
            if save_b64_image(image_b64, fp):
                img_path = "/" + fp.replace("\\", "/")

        # Close the session
        execute(conn,
            """UPDATE attendance_sessions SET
               check_out_time=%s, check_out_image_path=%s,
               check_out_lat=%s, check_out_lng=%s, session_hours=%s
               WHERE id=%s""",
            [time_now, img_path, lat, lng, sess_hrs, open_session["id"]])

        # Recalculate total working hours for today across all sessions
        total_hrs = scalar(conn,
            "SELECT COALESCE(SUM(session_hours),0) FROM attendance_sessions "
            "WHERE faculty_id=%s AND session_date=%s AND check_out_time IS NOT NULL",
            [faculty_id, today]) or 0
        total_hrs = round(float(total_hrs) + sess_hrs, 2)

        status = "half_day" if total_hrs < HALF_DAY_HOURS else "present"

        # Update daily attendance summary
        execute(conn,
            "UPDATE attendance SET working_hours=%s, status=%s WHERE id=%s",
            [total_hrs, status, open_session["attendance_id"]])

        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "message":        "Check-out recorded!",
        "time":           time_now,
        "sessionHours":   sess_hrs,
        "totalHours":     total_hrs,
        "status":         status,
    })


# ── TODAY STATUS ──────────────────────────────────────────────────────────────

@app.route("/api/attendance/today-status")
@login_required
def today_status():
    conn = get_db()
    try:
        att = fetchone(conn,
            "SELECT * FROM attendance WHERE faculty_id=%s AND attendance_date=%s",
            [session["faculty_id"], ist_today()])
        sessions = fetchall(conn,
            "SELECT * FROM attendance_sessions WHERE faculty_id=%s AND session_date=%s "
            "ORDER BY created_at ASC",
            [session["faculty_id"], ist_today()]) if att else []
    finally:
        conn.close()

    if not att:
        resp = jsonify({"hasRecord": False, "date": ist_today(), "sessions": []})
    else:
        # Is there an open session right now?
        has_open = any(s["check_out_time"] is None for s in sessions)
        resp = jsonify({
            "hasRecord":    True,
            "date":         att["attendance_date"],
            "workingHours": att["working_hours"],
            "status":       att["status"],
            "hasOpenSession": has_open,
            "sessionCount": len(sessions),
            "sessions": [{
                "id":                 s["id"],
                "checkInTime":        str(s["check_in_time"]),
                "checkOutTime":       str(s["check_out_time"]) if s["check_out_time"] else None,
                "sessionHours":       s["session_hours"],
                "checkInImagePath":   s["check_in_image_path"],
                "checkOutImagePath":  s["check_out_image_path"],
            } for s in sessions],
            # Legacy fields for backward compat
            "checkInTime":       str(sessions[0]["check_in_time"]) if sessions else None,
            "checkOutTime":      str(sessions[-1]["check_out_time"]) if sessions and sessions[-1]["check_out_time"] else None,
            "checkInImagePath":  sessions[0]["check_in_image_path"] if sessions else None,
            "checkOutImagePath": sessions[-1]["check_out_image_path"] if sessions else None,
        })
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


# ── FACULTY REPORTS ───────────────────────────────────────────────────────────

@app.route("/api/report/my")
@login_required
def my_report():
    args      = request.args
    month     = args.get("month", "")
    date_from = args.get("from", "")
    date_to   = args.get("to",   "")

    q = "SELECT * FROM attendance WHERE faculty_id=%s"
    p = [session["faculty_id"]]

    if month:
        q += " AND LEFT(attendance_date,7)=%s"; p.append(month)
    elif date_from and date_to:
        q += " AND attendance_date>=%s AND attendance_date<=%s"; p += [date_from, date_to]

    q += " ORDER BY attendance_date DESC"
    conn = get_db()
    try:
        rows = fetchall(conn, q, p)
        result = []
        for r in rows:
            row = dict(r)
            # Get sessions for this attendance day to pull images
            sessions = fetchall(conn,
                "SELECT check_in_time, check_out_time, check_in_image_path, "
                "check_out_image_path, session_hours FROM attendance_sessions "
                "WHERE attendance_id=%s ORDER BY created_at ASC",
                [row["id"]])
            if sessions:
                # Use first session's check-in image and last session's check-out image
                row["check_in_time"]        = str(sessions[0]["check_in_time"])
                row["check_out_time"]       = str(sessions[-1]["check_out_time"]) if sessions[-1]["check_out_time"] else None
                row["check_in_image_path"]  = sessions[0]["check_in_image_path"]
                row["check_out_image_path"] = sessions[-1]["check_out_image_path"]
                row["sessions"]             = [{
                    "checkInTime":       str(s["check_in_time"]),
                    "checkOutTime":      str(s["check_out_time"]) if s["check_out_time"] else None,
                    "checkInImagePath":  s["check_in_image_path"],
                    "checkOutImagePath": s["check_out_image_path"],
                    "sessionHours":      s["session_hours"],
                } for s in sessions]
            result.append(row)
    finally:
        conn.close()

    resp = jsonify(result)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/api/report/summary")
@login_required
def my_summary():
    month  = request.args.get("month", ist_now().strftime("%Y-%m"))
    yr, mo = map(int, month.split("-"))

    today_str = ist_today()
    _, dim    = calendar.monthrange(yr, mo)
    wdays     = 0
    for d in range(1, dim + 1):
        ds = f"{yr}-{mo:02d}-{d:02d}"
        if ds > today_str:
            break
        if date(yr, mo, d).weekday() != 6:
            wdays += 1

    conn = get_db()
    try:
        rows = fetchall(conn,
            "SELECT status, COUNT(*) as cnt FROM attendance "
            "WHERE faculty_id=%s AND LEFT(attendance_date,7)=%s GROUP BY status",
            [session["faculty_id"], month])
        stats = {"present": 0, "half_day": 0}
        for r in rows:
            stats[r["status"]] = r["cnt"]
        stats["absent"]       = max(0, wdays - stats["present"] - stats["half_day"])
        stats["working_days"] = wdays

        avg = scalar(conn,
            "SELECT AVG(working_hours) FROM attendance "
            "WHERE faculty_id=%s AND LEFT(attendance_date,7)=%s",
            [session["faculty_id"], month]) or 0
    finally:
        conn.close()

    stats["avg_working_hours"] = round(float(avg), 2)
    stats["month"]             = month
    resp = jsonify(stats)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/api/report/calendar")
@login_required
def my_calendar():
    month = request.args.get("month", ist_now().strftime("%Y-%m"))
    conn  = get_db()
    try:
        rows = fetchall(conn,
            "SELECT attendance_date, status, check_in_time, check_out_time, working_hours "
            "FROM attendance WHERE faculty_id=%s AND LEFT(attendance_date,7)=%s "
            "ORDER BY attendance_date",
            [session["faculty_id"], month])
    finally:
        conn.close()

    result = [dict(r) for r in rows]

    resp = jsonify(result)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


# ── ADMIN APIS ────────────────────────────────────────────────────────────────

@app.route("/api/admin/stats")
@admin_required
def admin_stats():
    today = ist_today()
    conn  = get_db()
    try:
        total     = scalar(conn, "SELECT COUNT(*) FROM faculty WHERE role='faculty' AND is_active=1")
        present   = scalar(conn, "SELECT COUNT(*) FROM attendance WHERE attendance_date=%s AND check_in_time IS NOT NULL", [today])
        checkout  = scalar(conn, "SELECT COUNT(*) FROM attendance WHERE attendance_date=%s AND check_out_time IS NOT NULL", [today])
        halfday   = scalar(conn, "SELECT COUNT(*) FROM attendance WHERE attendance_date=%s AND status='half_day'", [today])
        depts     = fetchall(conn,
            "SELECT department, COUNT(*) as cnt FROM faculty "
            "WHERE role='faculty' AND is_active=1 GROUP BY department ORDER BY cnt DESC")
        total_att = scalar(conn, "SELECT COUNT(*) FROM attendance")
    finally:
        conn.close()

    return jsonify({
        "totalFaculty":    total,
        "todayPresent":    present,
        "todayAbsent":     max(0, total - present),
        "todayCheckout":   checkout,
        "todayHalfDay":    halfday,
        "totalAttendance": total_att,
        "departments":     [dict(d) for d in depts],
    })


@app.route("/api/admin/faculty")
@admin_required
def admin_faculty():
    dept   = request.args.get("department", "")
    search = request.args.get("search", "").strip()
    q      = "SELECT * FROM faculty WHERE role='faculty'"
    p      = []
    if dept:   q += " AND department=%s";                          p.append(dept)
    if search: q += " AND (name LIKE %s OR employee_id LIKE %s)"; p += [f"%{search}%", f"%{search}%"]
    q += " ORDER BY department, name"

    conn = get_db()
    try:
        rows = fetchall(conn, q, p)
    finally:
        conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d.pop("password_hash",   None)
        d.pop("face_descriptor", None)
        result.append(d)
    return jsonify(result)


@app.route("/api/admin/faculty/<int:fid>", methods=["DELETE"])
@admin_required
def admin_delete_faculty(fid):
    conn = get_db()
    try:
        f = fetchone(conn,
            "SELECT name, employee_id, email, department, designation FROM faculty "
            "WHERE id=%s AND role='faculty'", [fid])
        if not f:
            return jsonify({"error": "Faculty not found."}), 404

        # ── Permanently blacklist the Employee ID before deleting ──────────
        execute(conn,
            """INSERT INTO emp_id_blacklist
               (employee_id, former_name, former_email, department, designation,
                reason, deleted_by)
               VALUES (%s,%s,%s,%s,%s,'deleted',%s)
               ON DUPLICATE KEY UPDATE
                 former_name=VALUES(former_name),
                 former_email=VALUES(former_email),
                 department=VALUES(department),
                 designation=VALUES(designation),
                 reason='deleted',
                 deleted_by=VALUES(deleted_by),
                 created_at=NOW()""",
            [f["employee_id"], f["name"], f["email"],
             f["department"], f["designation"], session["faculty_id"]])

        execute(conn, "DELETE FROM faculty WHERE id=%s", [fid])
        execute(conn,
            "INSERT INTO admin_log (admin_id,action,target_id,details) VALUES (%s,%s,%s,%s)",
            [session["faculty_id"], "DELETE_FACULTY", fid,
             f"Deleted & blacklisted: {f['name']} ({f['employee_id']})"])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": f"Faculty '{f['name']}' deleted. Employee ID '{f['employee_id']}' has been permanently reserved."})


@app.route("/api/admin/faculty/<int:fid>/toggle", methods=["POST"])
@admin_required
def admin_toggle_faculty(fid):
    conn = get_db()
    try:
        row = fetchone(conn, "SELECT name,is_active FROM faculty WHERE id=%s AND role='faculty'", [fid])
        if not row:
            return jsonify({"error": "Faculty not found."}), 404
        new_state = 0 if row["is_active"] else 1
        execute(conn, "UPDATE faculty SET is_active=%s WHERE id=%s", [new_state, fid])
        execute(conn,
            "INSERT INTO admin_log (admin_id,action,target_id,details) VALUES (%s,%s,%s,%s)",
            [session["faculty_id"], "TOGGLE_FACULTY", fid,
             f"{'Activated' if new_state else 'Deactivated'}: {row['name']}"])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"isActive": bool(new_state),
                    "message": f"Faculty {'activated' if new_state else 'deactivated'}."})


@app.route("/api/admin/faculty/add", methods=["POST"])
@admin_required
def admin_add_faculty():
    d           = request.json or {}
    employee_id = d.get("employeeId", "").strip().upper()
    name        = d.get("name", "").strip()
    email       = d.get("email", "").strip().lower()
    phone       = d.get("phone", "").strip()
    department  = d.get("department", "").strip()
    designation = d.get("designation", "").strip()
    password    = d.get("password", "")
    face_desc   = d.get("faceDescriptor")
    face_b64    = d.get("faceImage", "")
    college_id  = d.get("collegeId") or None
    program_id  = d.get("programId") or None

    errors = {}
    emp_err   = validate_employee_id(employee_id)
    email_err = validate_email(email)
    pwd_err   = validate_password(password) if password else None
    if emp_err:        errors["employeeId"] = emp_err
    if email_err:      errors["email"]      = email_err
    if pwd_err:        errors["password"]   = pwd_err
    if not name:       errors["name"]       = "Name is required."
    if not department: errors["department"] = "Department is required."
    if not designation or len(designation) < 2:
                       errors["designation"] = "Designation is required."
    if not face_desc or not isinstance(face_desc, list) or len(face_desc) != 128:
                       errors["face"] = "Face registration required. Please scan the faculty's face."
    if errors:
        return jsonify({"errors": errors}), 422

    conn = get_db()
    try:
        # Check permanent blacklist first (deleted faculty)
        bl = fetchone(conn,
            "SELECT former_name FROM emp_id_blacklist WHERE UPPER(employee_id)=%s", [employee_id])
        if bl:
            return jsonify({"errors": {"employeeId":
                f"🚫 Employee ID '{employee_id}' is permanently reserved. "
                f"It was previously assigned to {bl['former_name']} who has left the institution. "
                f"This ID cannot be reused. Please use a different ID."
            }}), 409

        existing = fetchone(conn, "SELECT name, is_active FROM faculty WHERE UPPER(employee_id)=%s", [employee_id])
        if existing:
            if existing["is_active"] == 0:
                return jsonify({"errors": {"employeeId":
                    f"🚫 Employee ID '{employee_id}' is reserved. "
                    f"This ID belongs to {existing['name']} who is currently deactivated. "
                    f"Reserved IDs cannot be reused. Please use a different ID."
                }}), 409
            return jsonify({"errors": {"employeeId":
                f"❌ Employee ID '{employee_id}' already exists."
            }}), 409
        if fetchone(conn, "SELECT id FROM faculty WHERE LOWER(email)=%s", [email]):
            return jsonify({"errors": {"email": f"Email '{email}' already exists."}}), 409

        # Duplicate face check
        existing_faces = fetchall(conn,
            "SELECT employee_id, name, face_descriptor FROM faculty "
            "WHERE face_descriptor IS NOT NULL AND has_face_registered=1")
        for ef in existing_faces:
            try:
                stored = json.loads(ef["face_descriptor"])
                dist = math.sqrt(sum((x - y) ** 2 for x, y in zip(face_desc, stored)))
                if dist < 0.50:
                    return jsonify({"errors": {"face": (
                        f"This face is already registered to {ef['name']} ({ef['employee_id']}). "
                        f"Each faculty member must use their own unique face.")}}), 409
            except Exception:
                continue

        pw_hash   = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode() if password else ""
        desc_json = json.dumps(face_desc)

        img_path = ""
        if face_b64:
            fn = f"{safe_name(employee_id)}.jpg"
            fp = os.path.join(REGISTERED_FACES_DIR, fn)
            if save_b64_image(face_b64, fp):
                img_path = "/" + fp.replace("\\", "/")

        fid = execute(conn,
            """INSERT INTO faculty
               (employee_id,name,email,phone,department,designation,password_hash,
                face_descriptor,face_image_path,has_face_registered,college_id,program_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)""",
            [employee_id, name, email, phone, department, designation, pw_hash,
             desc_json, img_path, college_id, program_id])
        execute(conn,
            "INSERT INTO admin_log (admin_id,action,target_id,details) VALUES (%s,%s,%s,%s)",
            [session["faculty_id"], "ADD_FACULTY", fid, f"Added: {name} ({employee_id})"])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": fid, "name": name, "employeeId": employee_id,
                    "message": f"Faculty '{name}' registered successfully!"}), 201


@app.route("/api/admin/attendance")
@admin_required
def admin_attendance():
    args      = request.args
    dept      = args.get("department", "")
    date_from = args.get("from", "")
    date_to   = args.get("to",   "")
    single_dt = args.get("date", "")
    month     = args.get("month", "")
    fac_id    = args.get("facultyId", "")

    q = """SELECT a.*, f.name, f.employee_id, f.department, f.designation,
                  f.email, f.phone, f.face_image_path
           FROM attendance a JOIN faculty f ON a.faculty_id=f.id WHERE 1=1"""
    p = []
    if dept:      q += " AND f.department=%s";                                    p.append(dept)
    if fac_id:    q += " AND a.faculty_id=%s";                                    p.append(fac_id)
    if single_dt: q += " AND a.attendance_date=%s";                               p.append(single_dt)
    elif month:   q += " AND LEFT(a.attendance_date,7)=%s";        p.append(month)
    elif date_from and date_to:
        q += " AND a.attendance_date>=%s AND a.attendance_date<=%s";              p += [date_from, date_to]
    q += " ORDER BY a.attendance_date DESC, f.name ASC"

    conn = get_db()
    try:
        rows = fetchall(conn, q, p)
    finally:
        conn.close()

    result = [dict(r) for r in rows]
    return jsonify(result)


@app.route("/api/admin/report/summary")
@admin_required
def admin_report_summary():
    month = request.args.get("month", ist_now().strftime("%Y-%m"))
    dept  = request.args.get("department", "")
    yr, mo = map(int, month.split("-"))

    today_str = ist_today()
    _, dim    = calendar.monthrange(yr, mo)
    wdays     = 0
    for d in range(1, dim + 1):
        ds = f"{yr}-{mo:02d}-{d:02d}"
        if ds > today_str:
            break
        if date(yr, mo, d).weekday() != 6:
            wdays += 1

    q = "SELECT id,employee_id,name,department,designation FROM faculty WHERE role='faculty' AND is_active=1"
    p = []
    if dept: q += " AND department=%s"; p.append(dept)

    conn = get_db()
    try:
        flist  = fetchall(conn, q, p)
        result = []
        for f in flist:
            rows = fetchall(conn,
                "SELECT status, COUNT(*) as cnt FROM attendance "
                "WHERE faculty_id=%s AND LEFT(attendance_date,7)=%s GROUP BY status",
                [f["id"], month])
            stat = {"present": 0, "half_day": 0}
            for r in rows:
                stat[r["status"]] = r["cnt"]
            stat["absent"]       = max(0, wdays - stat["present"] - stat["half_day"])
            stat["working_days"] = wdays
            avg = scalar(conn,
                "SELECT AVG(working_hours) FROM attendance "
                "WHERE faculty_id=%s AND LEFT(attendance_date,7)=%s",
                [f["id"], month]) or 0
            result.append({
                "id": f["id"], "employeeId": f["employee_id"], "name": f["name"],
                "department": f["department"], "designation": f["designation"],
                **stat, "avgHours": round(float(avg), 2),
            })
    finally:
        conn.close()
    return jsonify({"month": month, "workingDays": wdays, "faculty": result})


@app.route("/api/admin/mark-leave", methods=["POST"])
@admin_required
def mark_leave():
    d   = request.json or {}
    fid = d.get("facultyId")
    dt  = d.get("date", ist_today())
    lt  = d.get("type", "absent")
    rsn = d.get("reason", "")

    conn = get_db()
    try:
        f = fetchone(conn, "SELECT employee_id FROM faculty WHERE id=%s", [fid])
        if not f:
            return jsonify({"error": "Faculty not found."}), 404
        execute(conn,
            "INSERT IGNORE INTO leave_log "
            "(faculty_id,employee_id,leave_date,leave_type,reason,marked_by) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            [fid, f["employee_id"], dt, lt, rsn, session["faculty_id"]])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": f"{lt.title()} recorded for {dt}."})


@app.route("/api/admin/log")
@admin_required
def admin_log():
    conn = get_db()
    try:
        rows = fetchall(conn,
            "SELECT al.*, f.name as admin_name FROM admin_log al "
            "JOIN faculty f ON al.admin_id=f.id ORDER BY al.created_at DESC LIMIT 100")
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])


# ── COLLEGE & PROGRAM APIS ───────────────────────────────────────────────────

@app.route("/api/colleges")
def get_colleges():
    conn = get_db()
    try:
        colleges = fetchall(conn, "SELECT * FROM colleges ORDER BY name")
        result   = []
        for c in colleges:
            programs = fetchall(conn,
                "SELECT p.*, (SELECT COUNT(*) FROM faculty f "
                " WHERE f.program_id=p.id AND f.role='faculty' AND f.is_active=1) as student_count "
                "FROM programs p WHERE p.college_id=%s ORDER BY p.name", [c["id"]])
            d = dict(c)
            d["programs"] = [dict(p) for p in programs]
            result.append(d)
    finally:
        conn.close()
    return jsonify(result)


@app.route("/api/colleges", methods=["POST"])
@admin_required
def add_college():
    d           = request.json or {}
    name        = d.get("name", "").strip()
    short_name  = d.get("shortName", "").strip().upper()
    description = d.get("description", "").strip()
    if not name or not short_name:
        return jsonify({"error": "Name and short name are required."}), 422
    conn = get_db()
    try:
        try:
            cid = execute(conn,
                "INSERT INTO colleges (name, short_name, description) VALUES (%s,%s,%s)",
                [name, short_name, description])
            conn.commit()
        except Exception:
            conn.rollback()
            return jsonify({"error": "College name already exists."}), 409
    finally:
        conn.close()
    return jsonify({"id": cid, "message": f"College '{name}' added."}), 201


@app.route("/api/colleges/<int:cid>", methods=["PUT"])
@admin_required
def update_college(cid):
    d           = request.json or {}
    name        = d.get("name", "").strip()
    short_name  = d.get("shortName", "").strip().upper()
    description = d.get("description", "").strip()
    if not name or not short_name:
        return jsonify({"error": "Name and short name are required."}), 422
    conn = get_db()
    try:
        execute(conn,
            "UPDATE colleges SET name=%s, short_name=%s, description=%s WHERE id=%s",
            [name, short_name, description, cid])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": "College updated."})


@app.route("/api/colleges/<int:cid>", methods=["DELETE"])
@admin_required
def delete_college(cid):
    conn = get_db()
    try:
        c = fetchone(conn, "SELECT name FROM colleges WHERE id=%s", [cid])
        if not c:
            return jsonify({"error": "College not found."}), 404
        execute(conn, "DELETE FROM colleges WHERE id=%s", [cid])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": f"College '{c['name']}' deleted."})


@app.route("/api/programs", methods=["POST"])
@admin_required
def add_program():
    d          = request.json or {}
    college_id = d.get("collegeId")
    name       = d.get("name", "").strip()
    duration   = d.get("duration", "").strip()
    intake     = d.get("intakeCapacity", 0)
    if not college_id or not name:
        return jsonify({"error": "College and program name are required."}), 422
    conn = get_db()
    try:
        try:
            pid = execute(conn,
                "INSERT INTO programs (college_id, name, duration, intake_capacity) VALUES (%s,%s,%s,%s)",
                [college_id, name, duration, intake])
            conn.commit()
        except Exception:
            conn.rollback()
            return jsonify({"error": "Program already exists for this college."}), 409
    finally:
        conn.close()
    return jsonify({"id": pid, "message": f"Program '{name}' added."}), 201


@app.route("/api/programs/<int:pid>", methods=["DELETE"])
@admin_required
def delete_program(pid):
    conn = get_db()
    try:
        p = fetchone(conn, "SELECT name FROM programs WHERE id=%s", [pid])
        if not p:
            return jsonify({"error": "Program not found."}), 404
        execute(conn, "DELETE FROM programs WHERE id=%s", [pid])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": f"Program '{p['name']}' deleted."})


@app.route("/api/programs/<int:pid>", methods=["PUT"])
@admin_required
def update_program(pid):
    d        = request.json or {}
    name     = d.get("name", "").strip()
    duration = d.get("duration", "").strip()
    intake   = d.get("intakeCapacity", 0)
    if not name:
        return jsonify({"error": "Program name is required."}), 422
    conn = get_db()
    try:
        p = fetchone(conn, "SELECT name FROM programs WHERE id=%s", [pid])
        if not p:
            return jsonify({"error": "Program not found."}), 404
        execute(conn,
            "UPDATE programs SET name=%s, duration=%s, intake_capacity=%s WHERE id=%s",
            [name, duration, intake, pid])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": f"Program '{name}' updated."})


# ── ADVANCED REPORT (admin) ───────────────────────────────────────────────────

@app.route("/api/admin/report/advanced")
@admin_required
def admin_report_advanced():
    args        = request.args
    month       = args.get("month", "")
    date_from   = args.get("from", "")
    date_to     = args.get("to", "")
    college_id  = args.get("collegeId", "")
    program_id  = args.get("programId", "")
    faculty_id  = args.get("facultyId", "")
    designation = args.get("designation", "")
    search      = args.get("search", "").strip()
    mode        = args.get("mode", "detail")

    conn = get_db()
    try:
        # ── summary mode ──────────────────────────────────────────────────────
        if mode == "summary":
            if month:
                yr, mo = map(int, month.split("-"))
                today_str = ist_today()
                _, dim    = calendar.monthrange(yr, mo)
                wdays     = 0
                for d in range(1, dim + 1):
                    ds = f"{yr}-{mo:02d}-{d:02d}"
                    if ds > today_str:
                        break
                    if date(yr, mo, d).weekday() != 6:
                        wdays += 1
                date_cond = "LEFT(a.attendance_date,7)=%s"
                date_val  = month
            elif date_from and date_to:
                d_from = date(*(int(x) for x in date_from.split("-")))
                d_to   = date(*(int(x) for x in date_to.split("-")))
                wdays  = sum(
                    1 for n in range((d_to - d_from).days + 1)
                    if (d_from + timedelta(n)).weekday() != 6
                )
                date_cond = "a.attendance_date>=%s AND a.attendance_date<=%s"
                date_val  = [date_from, date_to]
            else:
                today_str = ist_today()
                wdays     = 1 if date.today().weekday() != 6 else 0
                date_cond = "a.attendance_date=%s"
                date_val  = ist_today()

            fq = ("SELECT id,employee_id,name,department,designation,college_id,program_id,"
                  "face_image_path FROM faculty WHERE role='faculty' AND is_active=1")
            fp = []
            if college_id:  fq += " AND college_id=%s";  fp.append(college_id)
            if program_id:  fq += " AND program_id=%s";  fp.append(program_id)
            if faculty_id:  fq += " AND id=%s";          fp.append(faculty_id)
            if designation: fq += " AND designation=%s"; fp.append(designation)
            if search:      fq += " AND (name LIKE %s OR employee_id LIKE %s)"; fp += [f"%{search}%", f"%{search}%"]
            flist = fetchall(conn, fq, fp)

            result = []
            for f in flist:
                params = [f["id"]] + (date_val if isinstance(date_val, list) else [date_val])
                rows = fetchall(conn,
                    f"SELECT status, COUNT(*) as cnt FROM attendance a "
                    f"WHERE a.faculty_id=%s AND {date_cond} GROUP BY status", params)
                stat = {"present": 0, "half_day": 0}
                for r in rows:
                    stat[r["status"]] = r["cnt"]
                stat["absent"]       = max(0, wdays - stat["present"] - stat["half_day"])
                stat["working_days"] = wdays
                pct = round(((stat["present"] + stat["half_day"] * 0.5) / wdays * 100), 1) if wdays else 0
                college = fetchone(conn, "SELECT name, short_name FROM colleges WHERE id=%s", [f["college_id"]]) if f["college_id"] else None
                program = fetchone(conn, "SELECT name FROM programs WHERE id=%s", [f["program_id"]]) if f["program_id"] else None
                result.append({
                    "id": f["id"], "name": f["name"], "employeeId": f["employee_id"],
                    "department": f["department"], "designation": f["designation"],
                    "college": college["short_name"] if college else "-",
                    "program": program["name"] if program else "-",
                    "faceImagePath": f["face_image_path"],
                    **stat, "attendancePct": pct, "workingDays": wdays,
                })
            return jsonify({"mode": "summary", "workingDays": wdays, "faculty": result})

        # ── detail mode ───────────────────────────────────────────────────────
        q = """SELECT a.*, f.name, f.employee_id, f.department, f.designation,
                      f.face_image_path, f.college_id, f.program_id
               FROM attendance a JOIN faculty f ON a.faculty_id=f.id WHERE 1=1"""
        p = []
        if college_id:  q += " AND f.college_id=%s";  p.append(college_id)
        if program_id:  q += " AND f.program_id=%s";  p.append(program_id)
        if faculty_id:  q += " AND a.faculty_id=%s";  p.append(faculty_id)
        if designation: q += " AND f.designation=%s"; p.append(designation)
        if search:      q += " AND (f.name LIKE %s OR f.employee_id LIKE %s)"; p += [f"%{search}%", f"%{search}%"]
        if month:       q += " AND LEFT(a.attendance_date,7)=%s"; p.append(month)
        elif date_from and date_to:
            q += " AND a.attendance_date>=%s AND a.attendance_date<=%s"; p += [date_from, date_to]
        else:
            q += " AND a.attendance_date=%s"; p.append(ist_today())
        q += " ORDER BY a.attendance_date DESC, f.name ASC"

        rows = fetchall(conn, q, p)
        result = []
        for r in rows:
            row = dict(r)
            college = fetchone(conn, "SELECT short_name FROM colleges WHERE id=%s", [row["college_id"]]) if row.get("college_id") else None
            program = fetchone(conn, "SELECT name FROM programs WHERE id=%s", [row["program_id"]]) if row.get("program_id") else None
            row["college"] = college["short_name"] if college else "-"
            row["program"] = program["name"] if program else "-"
            result.append(row)
    finally:
        conn.close()

    return jsonify({"mode": "detail", "records": result})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
