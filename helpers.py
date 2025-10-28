import os
from datetime import datetime, timedelta
import pandas as pd
import qrcode
import numpy as np
from PIL import Image
import streamlit as st

try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

try:
    import barcode
    from barcode.writer import ImageWriter
    BARCODE_GENERATION_AVAILABLE = True
except ImportError:
    BARCODE_GENERATION_AVAILABLE = False

from auth import generate_secure_token

# Folders for QR and barcodes
QR_FOLDER = os.path.join(os.path.dirname(__file__), "qrcodes")
BARCODE_FOLDER = os.path.join(os.path.dirname(__file__), "barcodes")
os.makedirs(QR_FOLDER, exist_ok=True)
os.makedirs(BARCODE_FOLDER, exist_ok=True)


# -------------------- User Isolation Helpers --------------------
def get_user_filter():
    """Returns MongoDB filter dict for current user's data isolation"""
    if "auth" not in st.session_state or not st.session_state.auth.get("logged_in"):
        return {"created_by": None}  # Safety: match nothing if not authenticated

    role = st.session_state.auth.get("role")
    if role == "admin":
        return {}  # Admins see all data

    username = st.session_state.auth.get("username")
    return {"created_by": username}  # Teachers see only their data


def is_admin():
    """Check if current user is admin"""
    if "auth" not in st.session_state:
        return False
    return st.session_state.auth.get("role") == "admin"


# -------------------- QR and Barcode Generation --------------------
def make_qr(student_id):
    """Generate QR code for student"""
    img = qrcode.make(student_id, box_size=10, border=4)
    path = os.path.join(QR_FOLDER, f"{student_id}_qr.png")
    img.save(path)
    return path


def make_barcode(student_id):
    """Generate barcode for student"""
    try:
        code128 = barcode.get_barcode_class('code128')
        barcode_img = code128(student_id, writer=ImageWriter())
        path = os.path.join(BARCODE_FOLDER, f"{student_id}_barcode")
        barcode_img.save(path)
        return f"{path}.png"
    except Exception as e:
        st.error(f"Error generating barcode: {e}")
        return None


def decode_from_camera(pil_img):
    """Decode QR code or barcode from camera image using pyzbar"""
    try:
        img_array = np.array(pil_img)
        codes = pyzbar.decode(img_array)
        if codes:
            return codes[0].data.decode('utf-8'), codes[0].type
        return None, None
    except Exception as e:
        st.error(f"Decode error: {e}")
        return None, None


# -------------------- Attendance Functions --------------------
def mark_attendance(att_col, use_mongo, student_id, status, when_dt=None, course=None, method="manual", created_by_override=None):
    """Mark attendance for a student"""
    when_dt = when_dt or datetime.now()
    date_str = when_dt.date().isoformat()

    # Determine who is marking this attendance
    if created_by_override:
        created_by = created_by_override
    elif "auth" in st.session_state and st.session_state.auth.get("logged_in"):
        created_by = st.session_state.auth.get("username")
    else:
        created_by = "anonymous"  # Fallback for unauthenticated access

    if use_mongo:
        if att_col.find_one({"student_id": student_id, "date": date_str}):
            return {"error": "already"}
        doc = {
            "student_id": student_id,
            "date": date_str,
            "time": when_dt.strftime("%H:%M:%S"),
            "status": int(status),
            "course": course,
            "method": method,
            "ts": when_dt,
            "created_by": created_by
        }
        att_col.insert_one(doc)
        return {"ok": True, **doc}
    else:
        existing = att_col.find_one({"student_id": student_id, "date": date_str})
        if existing:
            return {"error": "already"}
        doc = {
            "student_id": student_id,
            "date": date_str,
            "time": when_dt.strftime("%H:%M:%S"),
            "status": int(status),
            "course": course,
            "method": method,
            "ts": when_dt.isoformat(),
            "created_by": created_by
        }
        att_col.insert_one(doc)
        return {"ok": True, **doc}


def create_attendance_session(sessions_col, use_mongo, course=None, duration_hours=24, description=""):
    """Create a new attendance session with shareable link"""
    session_id = generate_secure_token()
    expires_at = datetime.now() + timedelta(hours=duration_hours)

    session_data = {
        "session_id": session_id,
        "course": course,
        "description": description,
        "created_by": st.session_state.auth.get("username"),
        "created_at": datetime.now() if use_mongo else datetime.now().isoformat(),
        "expires_at": expires_at if use_mongo else expires_at.isoformat(),
        "is_active": True,
        "attendance_count": 0
    }

    sessions_col.insert_one(session_data)
    return session_id, expires_at


def create_student_attendance_link(links_col, use_mongo, student_id, duration_hours=168):
    """Create a personal attendance link for a student"""
    link_id = generate_secure_token()
    expires_at = datetime.now() + timedelta(hours=duration_hours)

    link_data = {
        "link_id": link_id,
        "student_id": student_id,
        "created_by": st.session_state.auth.get("username"),
        "created_at": datetime.now() if use_mongo else datetime.now().isoformat(),
        "expires_at": expires_at if use_mongo else expires_at.isoformat(),
        "is_active": True,
        "uses": 0,
        "max_uses": None
    }

    links_col.insert_one(link_data)
    return link_id, expires_at


def get_students_df(students_col):
    """Get students dataframe with user isolation"""
    rows = students_col.find(get_user_filter())
    if not rows:
        return pd.DataFrame(columns=["student_id", "name", "course", "qr_path", "barcode_path"])
    return pd.DataFrame(rows)


def get_attendance_rows(att_col, use_mongo, start=None, end=None, course=None):
    """Get attendance rows with user isolation"""
    if use_mongo:
        q = {}
        if start or end:
            q["date"] = {}
        if start:
            q["date"]["$gte"] = start.isoformat()
        if end:
            q["date"]["$lte"] = end.isoformat()
        if course and course != "All":
            q["course"] = course
        q.update(get_user_filter())  # Add user isolation filter
        rows = list(att_col.find(q))
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["student_id", "date", "status", "time", "course", "method"])
    else:
        user_filter = get_user_filter()
        rows = att_col.find(user_filter)  # Apply user isolation filter
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["student_id", "date", "status", "time", "course", "method"])

        if not df.empty and (start or end):
            df = df.copy()
            if start:
                df = df[df["date"] >= start.isoformat()]
            if end:
                df = df[df["date"] <= end.isoformat()]

        if not df.empty and course and course != "All":
            df = df[df["course"] == course]

        return df


def pivot_attendance(students_col, att_col, use_mongo, start, end, course=None):
    """Create pivot table of attendance"""
    students = get_students_df(students_col)
    if students.empty:
        return pd.DataFrame()

    all_dates = pd.date_range(start=start, end=end, freq="D").date
    date_cols = [d.isoformat() for d in all_dates]
    rows = get_attendance_rows(att_col, use_mongo, start, end, course)

    if rows.empty:
        pivot = pd.DataFrame(0, index=students["student_id"], columns=date_cols)
    else:
        pv = rows.pivot_table(index="student_id", columns="date", values="status", aggfunc="max")
        for c in date_cols:
            if c not in pv.columns:
                pv[c] = 0
        pv = pv[date_cols].fillna(0).astype(int)
        pivot = pv

    pivot = pivot.reset_index()
    out = students[["student_id", "name", "course"]].merge(pivot, on="student_id", how="left")

    for c in date_cols:
        if c in out.columns:
            out[c] = out[c].fillna(0).astype(int)

    return out[["student_id", "name", "course"] + date_cols]


def require_reauth(page, user_manager):
    """Require re-authentication for sensitive pages"""
    if st.session_state.unlocked.get(page):
        return True

    st.warning("⚠️ This section requires re-authentication for security.")

    with st.form(f"reauth_{page}"):
        u = st.text_input("Username (current)")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Unlock"):
            user_data = user_manager.authenticate_user(u, p)
            if u != st.session_state.auth["username"]:
                st.error("Please use the currently logged-in username")
            elif user_data[0]:
                st.session_state.unlocked[page] = True
                st.success("✅ Unlocked for this session")
                st.rerun()
            else:
                st.error(user_data[1])
    st.stop()
