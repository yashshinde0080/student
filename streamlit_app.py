
import os
import io
from datetime import datetime, date, timedelta
import base64
import numpy as np
import pandas as pd
import qrcode
from PIL import Image
import cv2

import streamlit as st
from pymongo import MongoClient
from dotenv import load_dotenv

# -----------------------------
# Config & Helpers
# -----------------------------
st.set_page_config(page_title="Smart Attendance (Mini)", page_icon="🧑‍🏫", layout="wide")
load_dotenv()

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_DB", "smart_attendance")

@st.cache_resource
def get_db():
    client = MongoClient(MONGO_URI)
    return client[DB_NAME]

db = get_db()
students_col = db["students"]
att_col = db["attendance"]

def ensure_indexes():
    students_col.create_index("student_id", unique=True)
    att_col.create_index([("student_id", 1), ("date", 1)])

ensure_indexes()

QR_FOLDER = os.path.join(os.path.dirname(__file__), "qrcodes")
os.makedirs(QR_FOLDER, exist_ok=True)

def make_qr(content: str, filename: str) -> str:
    img = qrcode.make(content)
    path = os.path.join(QR_FOLDER, filename)
    img.save(path)
    return path

def decode_qr_from_image(pil_img: Image.Image):
    # Convert PIL image to OpenCV format
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    detector = cv2.QRCodeDetector()
    data, points, _ = detector.detectAndDecode(img)
    if points is not None and data:
        return data
    return None

def mark_attendance(student_id: str, status="Present", when=None, course=None):
    now = when or datetime.now()
    doc = {
        "student_id": student_id,
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "status": status,
        "course": course,
        "ts": now
    }
    att_col.insert_one(doc)
    return doc

def get_students_df():
    data = list(students_col.find({}, {"_id": 0}))
    return pd.DataFrame(data)

def get_attendance_df(start_date=None, end_date=None, course=None):
    query = {}
    if start_date or end_date:
        query["date"] = {}
        if start_date:
            query["date"]["$gte"] = start_date.isoformat()
        if end_date:
            query["date"]["$lte"] = end_date.isoformat()
        if not query["date"]:
            query.pop("date")
    if course and course != "All":
        query["course"] = course
    data = list(att_col.find(query, {"_id": 0}))
    return pd.DataFrame(data)

def download_button_from_bytes(data: bytes, filename: str, label="Download"):
    b64 = base64.b64encode(data).decode()
    href = f'<a href="data:file/octet-stream;base64,{b64}" download="{filename}">{label}</a>'
    st.markdown(href, unsafe_allow_html=True)

# -----------------------------
# Sidebar Navigation
# -----------------------------
st.sidebar.title("Smart Attendance (Mini)")
page = st.sidebar.radio("Go to", ["📊 Dashboard", "👨‍🎓 Students", "📷 Scan QR", "🧾 Attendance Records", "⚙️ Settings"], index=0)

# -----------------------------
# Dashboard
# -----------------------------
if page == "📊 Dashboard":
    st.title("📊 Attendance Dashboard")
    col1, col2, col3 = st.columns(3)
    today = date.today()
    with col1:
        st.metric("Today", today.isoformat())
    with col2:
        total_students = students_col.count_documents({})
        st.metric("Total Students", total_students)
    with col3:
        today_count = att_col.count_documents({"date": today.isoformat()})
        st.metric("Scans Today", today_count)

    st.subheader("Quick Trends")
    df = get_attendance_df(start_date=today - timedelta(days=7), end_date=today)
    if not df.empty:
        # Daily counts
        daily = df.groupby("date").size().reset_index(name="count").sort_values("date")
        st.line_chart(daily.set_index("date")["count"])
    else:
        st.info("No attendance data for the last 7 days.")

# -----------------------------
# Students
# -----------------------------
elif page == "👨‍🎓 Students":
    st.title("👨‍🎓 Manage Students")
    with st.expander("➕ Add Student"):
        with st.form("add_student_form", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                student_id = st.text_input("Student ID (USN/Roll)", placeholder="e.g., 1JC22AI001", max_chars=32)
            with col2:
                name = st.text_input("Name", placeholder="e.g., Yash Shinde")
            with col3:
                course = st.text_input("Course / Section", placeholder="e.g., AIML-A")
            submitted = st.form_submit_button("Add Student")
        if submitted:
            if not student_id or not name:
                st.error("Student ID and Name are required.")
            else:
                if students_col.find_one({"student_id": student_id}):
                    st.warning("Student ID already exists.")
                else:
                    # QR payload is just the student_id; could be JSON as well
                    qr_path = make_qr(student_id, f"{student_id}.png")
                    students_col.insert_one({
                        "student_id": student_id,
                        "name": name,
                        "course": course,
                        "qr_path": qr_path
                    })
                    st.success(f"Added {name} ({student_id}) and generated QR.")

    st.subheader("All Students")
    df = get_students_df()
    if df.empty:
        st.info("No students yet. Add a few to get started.")
    else:
        st.dataframe(df, use_container_width=True)
        # Download QR bundle
        if st.button("📦 Download All QRs (ZIP)"):
            mem = io.BytesIO()
            with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for _, row in df.iterrows():
                    if row.get("qr_path") and os.path.exists(row["qr_path"]):
                        zf.write(row["qr_path"], arcname=os.path.join("qrcodes", os.path.basename(row["qr_path"])))
            mem.seek(0)
            st.download_button("Download ZIP", mem, file_name="qrcodes.zip")

# -----------------------------
# Scan QR
# -----------------------------
elif page == "📷 Scan QR":
    st.title("📷 Scan QR to Mark Attendance")
    st.write("Use your webcam to capture a QR code.")

    snap = st.camera_input("Take a photo of the student's QR code")
    course = st.text_input("Course / Section (optional)", placeholder="e.g., AIML-A")
    chosen_date = st.date_input("Select Date", value=date.today())

    if snap is not None:
        img = Image.open(snap)
        data = decode_qr_from_image(img)
        if data:
            st.success(f"Decoded QR: {data}")
            student = students_col.find_one({"student_id": data})
            if not student:
                st.error("Student not found in database. Please add the student first.")
            else:
                doc = mark_attendance(
                    data,
                    status=1,   # Present
                    when=datetime.combine(chosen_date, datetime.now().time()),
                    course=course or student.get("course")
                )
                st.success(f"Attendance marked for {student.get('name')} ({doc['date']})")
        else:
            st.warning("Could not decode a QR. Try again.")

    st.markdown("---")
    st.subheader("✍️ Manual Entry (Single Student)")
    with st.form("manual_mark"):
        sid = st.text_input("Student ID", placeholder="e.g., 1JC22AI001")
        status_val = st.selectbox("Status", [("Present", 1), ("Absent", 0)], format_func=lambda x: x[0])
        chosen_date2 = st.date_input("Date", value=date.today(), key="manual_date")
        course2 = st.text_input("Course / Section", placeholder="e.g., AIML-A")
        submitted = st.form_submit_button("Mark Attendance")
        if submitted:
            if not sid:
                st.error("Student ID is required.")
            else:
                student = students_col.find_one({"student_id": sid})
                if not student:
                    st.error("Student not found in database.")
                else:
                    doc = mark_attendance(
                        sid,
                        status=status_val[1],
                        when=datetime.combine(chosen_date2, datetime.now().time()),
                        course=course2 or student.get("course")
                    )
                    st.success(f"Attendance marked for {student.get('name')} ({doc['date']})")

# -----------------------------
# Bulk Manual Entry (New Mode)
# -----------------------------
elif page == "🧾 Attendance Records":
    st.title("🧾 Attendance Records")

    tab1, tab2 = st.tabs(["📋 View Records", "✍️ Bulk Manual Entry"])
    
    with tab1:
        col1, col2, col3 = st.columns(3)
        with col1:
            start = st.date_input("Start date", value=date.today() - timedelta(days=7))
        with col2:
            end = st.date_input("End date", value=date.today())
        with col3:
            courses = sorted({doc.get("course") for doc in students_col.find({}, {"course": 1}) if doc.get("course")})
            course = st.selectbox("Course / Section", options=["All"] + courses)

        df = get_attendance_df(start, end, course)
        if df.empty:
            st.info("No records found for the selected filters.")
        else:
            st.dataframe(df, use_container_width=True)

            csv = df.to_csv(index=False).encode()
            st.download_button("⬇️ Download CSV", data=csv, file_name="attendance_records.csv", mime="text/csv")

    with tab2:
        st.subheader("✍️ Bulk Manual Entry for a Date")
        selected_date = st.date_input("Select Date for Attendance", value=date.today(), key="bulk_date")
        students = list(students_col.find({}, {"_id": 0}))
        if not students:
            st.warning("No students found. Add students first.")
        else:
            attendance_data = {}
            for s in students:
                col1, col2 = st.columns([3,1])
                with col1:
                    st.text(s["name"])
                with col2:
                    attendance_data[s["student_id"]] = st.checkbox(
                        "Present", value=True, key=f"chk_{s['student_id']}"
                    )
            if st.button("Save Attendance"):
                for sid, present in attendance_data.items():
                    mark_attendance(
                        sid,
                        status=1 if present else 0,
                        when=datetime.combine(selected_date, datetime.now().time()),
                        course=students_col.find_one({"student_id": sid}).get("course")
                    )
                st.success("Bulk attendance saved successfully ✅")

# -----------------------------
# Attendance Records
# -----------------------------
elif page == "🧾 Attendance Records":
    st.title("🧾 Attendance Records")
    col1, col2, col3 = st.columns(3)
    with col1:
        start = st.date_input("Start date", value=date.today() - timedelta(days=7))
    with col2:
        end = st.date_input("End date", value=date.today())
    with col3:
        # collect course list
        courses = sorted({doc.get("course") for doc in students_col.find({}, {"course": 1}) if doc.get("course")})
        course = st.selectbox("Course / Section", options=["All"] + courses)

    df = get_attendance_df(start, end, course)
    if df.empty:
        st.info("No records found for the selected filters.")
    else:
        # Basic metrics
        st.write(f"Total scans: **{len(df)}**")
        # Attendance percentage per student
        totals = df.groupby("student_id").size().rename("marked").to_frame()
        # Try to estimate sessions by counting unique dates in range (approx for mini project)
        unique_days = df["date"].nunique()
        students_df = get_students_df().set_index("student_id")
        summary = totals.join(students_df[["name", "course"]], how="left")
        if unique_days > 0:
            summary["approx_%"] = (summary["marked"] / unique_days * 100).round(2)
        else:
            summary["approx_%"] = 0.0
        st.dataframe(summary.reset_index(), use_container_width=True)

        csv = df.to_csv(index=False).encode()
        st.download_button("⬇️ Download CSV", data=csv, file_name="attendance_records.csv", mime="text/csv")

# -----------------------------
# Settings
# -----------------------------
elif page == "⚙️ Settings":
    st.title("⚙️ Settings & Utilities")
    st.caption("Configure database connection via environment variables.")
    st.code("MONGODB_URI=mongodb://localhost:27017\nMONGODB_DB=smart_attendance")
    if st.button("🗑️ Clear ALL data (students + attendance)"):
        students_col.delete_many({})
        att_col.delete_many({})
        st.success("Database cleared.")

    st.markdown("### How to print/download QR codes")
    st.write("Use **Students → Download All QRs (ZIP)** to get a ZIP with all generated QR codes.")

    st.markdown("### Tip: Unique QR Payloads")
    st.write("For higher security, encode a JSON payload like `{student_id, nonce}` and validate on scan.")