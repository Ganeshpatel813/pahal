"""
CDGI Faculty Attendance System — MySQL Database
"""

import os
import mysql.connector
from mysql.connector import Error

REGISTERED_FACES_DIR = os.path.join("static", "faces", "registered")
ATTENDANCE_FACES_DIR = os.path.join("static", "faces", "attendance")

# ── MySQL Config — change these to match your MySQL setup ──────────────────
DB_CONFIG = {
    "host":     os.environ.get("MYSQL_HOST",     "localhost"),
    "port":     int(os.environ.get("MYSQL_PORT", 3306)),
    "user":     os.environ.get("MYSQL_USER",     "root"),
    "password": os.environ.get("MYSQL_PASSWORD", "root"),
    "database": os.environ.get("MYSQL_DATABASE", "cdgi_attendance"),
    "charset":  "utf8mb4",
    "collation":"utf8mb4_unicode_ci",
    "autocommit": False,
}


def ensure_dirs():
    os.makedirs(REGISTERED_FACES_DIR, exist_ok=True)
    os.makedirs(ATTENDANCE_FACES_DIR, exist_ok=True)
    os.makedirs(os.path.join("static", "images"), exist_ok=True)


def get_db():
    """Return a MySQL connection with dict cursor."""
    conn = mysql.connector.connect(**DB_CONFIG)
    return conn


def dict_row(cursor, row):
    """Convert a row tuple to a dict using cursor column names."""
    if row is None:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def dict_rows(cursor, rows):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


def init_db():
    ensure_dirs()

    # First connect without database to create it if needed
    cfg = {k: v for k, v in DB_CONFIG.items() if k != "database"}
    conn = mysql.connector.connect(**cfg)
    cur  = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_CONFIG['database']}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    conn.commit()
    cur.close()
    conn.close()

    conn = get_db()
    cur  = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS colleges (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        name        VARCHAR(200) NOT NULL UNIQUE,
        short_name  VARCHAR(50)  NOT NULL,
        description TEXT,
        is_active   TINYINT NOT NULL DEFAULT 1,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS programs (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        college_id      INT NOT NULL,
        name            VARCHAR(200) NOT NULL,
        duration        VARCHAR(100),
        intake_capacity INT DEFAULT 0,
        is_active       TINYINT NOT NULL DEFAULT 1,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_college_program (college_id, name),
        FOREIGN KEY (college_id) REFERENCES colleges(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS faculty (
        id                  INT AUTO_INCREMENT PRIMARY KEY,
        employee_id         VARCHAR(50)  NOT NULL UNIQUE,
        name                VARCHAR(200) NOT NULL,
        email               VARCHAR(200) NOT NULL UNIQUE,
        phone               VARCHAR(20),
        department          VARCHAR(200) NOT NULL,
        designation         VARCHAR(200) NOT NULL,
        college_id          INT,
        program_id          INT,
        password_hash       TEXT NOT NULL,
        face_descriptor     LONGTEXT,
        face_image_path     TEXT,
        has_face_registered TINYINT NOT NULL DEFAULT 0,
        role                VARCHAR(20) NOT NULL DEFAULT 'faculty',
        is_active           TINYINT NOT NULL DEFAULT 1,
        notes               TEXT,
        created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (college_id) REFERENCES colleges(id) ON DELETE SET NULL,
        FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id                    INT AUTO_INCREMENT PRIMARY KEY,
        faculty_id            INT NOT NULL,
        employee_id           VARCHAR(50) NOT NULL,
        attendance_date       DATE NOT NULL,
        check_in_time         VARCHAR(20),
        check_out_time        VARCHAR(20),
        check_in_image_path   TEXT,
        check_out_image_path  TEXT,
        check_in_lat          DOUBLE,
        check_in_lng          DOUBLE,
        check_out_lat         DOUBLE,
        check_out_lng         DOUBLE,
        working_hours         DOUBLE DEFAULT 0,
        status                VARCHAR(20) DEFAULT 'present',
        marked_by_admin       TINYINT DEFAULT 0,
        notes                 TEXT,
        created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_faculty_date (faculty_id, attendance_date),
        FOREIGN KEY (faculty_id) REFERENCES faculty(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS leave_log (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        faculty_id  INT NOT NULL,
        employee_id VARCHAR(50) NOT NULL,
        leave_date  DATE NOT NULL,
        leave_type  VARCHAR(50) DEFAULT 'absent',
        reason      TEXT,
        marked_by   INT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_faculty_leave (faculty_id, leave_date),
        FOREIGN KEY (faculty_id) REFERENCES faculty(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS attendance_sessions (
        id                   INT AUTO_INCREMENT PRIMARY KEY,
        attendance_id        INT NOT NULL,
        faculty_id           INT NOT NULL,
        session_date         DATE NOT NULL,
        check_in_time        VARCHAR(20) NOT NULL,
        check_out_time       VARCHAR(20),
        check_in_image_path  TEXT,
        check_out_image_path TEXT,
        check_in_lat         DOUBLE,
        check_in_lng         DOUBLE,
        check_out_lat        DOUBLE,
        check_out_lng        DOUBLE,
        session_hours        DOUBLE DEFAULT 0,
        created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (attendance_id) REFERENCES attendance(id) ON DELETE CASCADE,
        FOREIGN KEY (faculty_id)    REFERENCES faculty(id)    ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS emp_id_blacklist (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        employee_id     VARCHAR(50)  NOT NULL UNIQUE,
        former_name     VARCHAR(200) NOT NULL,
        former_email    VARCHAR(200),
        department      VARCHAR(200),
        designation     VARCHAR(200),
        reason          VARCHAR(100) NOT NULL DEFAULT 'deleted',
        deleted_by      INT,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_log (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        admin_id    INT NOT NULL,
        action      VARCHAR(100) NOT NULL,
        target_id   INT,
        details     TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (admin_id) REFERENCES faculty(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    conn.commit()

    # Seed admin account
    cur.execute("SELECT id FROM faculty WHERE employee_id='ADMIN001'")
    if not cur.fetchone():
        import bcrypt
        pw_hash = bcrypt.hashpw(b"admin@CDGI2025", bcrypt.gensalt()).decode()
        cur.execute("""
            INSERT INTO faculty
              (employee_id, name, email, phone, department, designation,
               password_hash, has_face_registered, role)
            VALUES
              ('ADMIN001','System Administrator','admin@cdgi.edu.in','0731-2970011',
               'Administration','System Admin', %s, 0, 'admin')
        """, [pw_hash])
        conn.commit()
        print("[DB] Admin account created → ID: ADMIN001 | Password: admin@CDGI2025")

    # Seed colleges
    cur.execute("SELECT id FROM colleges WHERE short_name='CDIPS'")
    if not cur.fetchone():
        cur.execute("INSERT INTO colleges (name, short_name, description) VALUES (%s,%s,%s)",
                    ["CDIPS College", "CDIPS", "Chameli Devi Institute of Professional Studies"])
        cdips_id = cur.lastrowid
        for prog in ["BBA", "BCOM", "BCA", "BSc", "MCA"]:
            cur.execute("INSERT IGNORE INTO programs (college_id, name) VALUES (%s,%s)", [cdips_id, prog])

    cur.execute("SELECT id FROM colleges WHERE short_name='CDIP'")
    if not cur.fetchone():
        cur.execute("INSERT INTO colleges (name, short_name, description) VALUES (%s,%s,%s)",
                    ["CDIP College", "CDIP", "Chameli Devi Institute of Pharmacy"])
        cdip_id = cur.lastrowid
        for prog in ["B.Pharmacy", "M.Pharmacy"]:
            cur.execute("INSERT IGNORE INTO programs (college_id, name) VALUES (%s,%s)", [cdip_id, prog])

    conn.commit()
    cur.close()
    conn.close()
    print("[DB] MySQL database initialised successfully.")
