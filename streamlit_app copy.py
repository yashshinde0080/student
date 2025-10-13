import os, io, zipfile, base64, json, uuid, hashlib
from datetime import datetime, date, timedelta
from io import BytesIO
import secrets
import string

import pandas as pd
import qrcode
from PIL import Image
import cv2
import numpy as np
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

import streamlit as st
from pymongo import MongoClient, errors as mongo_errors
from werkzeug.security import generate_password_hash, check_password_hash

st.set_page_config(page_title="Smart Attendance — Enhanced", layout="wide")

# -------------------- Storage (MongoDB or JSON fallback) --------------------
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_DB", "smart_attendance_enhanced")

use_mongo = True
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    client.server_info()
    db = client[DB_NAME]
    users_col = db["users"]
    students_col = db["students"]
    att_col = db["attendance"]
    sessions_col = db["attendance_sessions"]
    links_col = db["attendance_links"]
    
    # Create indexes
    students_col.create_index("student_id", unique=True)
    att_col.create_index([("student_id",1),("date",1)], unique=True)
    users_col.create_index("username", unique=True)
    sessions_col.create_index("session_id", unique=True)
    sessions_col.create_index("expires_at", expireAfterSeconds=0)
    links_col.create_index("link_id", unique=True)
    links_col.create_index("expires_at", expireAfterSeconds=0)
    
except Exception as e:
    use_mongo = False
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_dir, exist_ok=True)
    USERS_FILE = os.path.join(data_dir, "users.json")
    STUDENTS_FILE = os.path.join(data_dir, "students.json")
    ATT_FILE = os.path.join(data_dir, "attendance.json")
    SESSIONS_FILE = os.path.join(data_dir, "sessions.json")
    LINKS_FILE = os.path.join(data_dir, "links.json")
    
    for f in (USERS_FILE, STUDENTS_FILE, ATT_FILE, SESSIONS_FILE, LINKS_FILE):
        if not os.path.exists(f):
            with open(f,"w") as fh:
                json.dump([], fh)

    class SimpleCol:
        def __init__(self, path): 
            self.path = path
            
        def _load(self):
            try:
                with open(self.path,"r") as fh:
                    data = json.load(fh)
                # Clean expired records for sessions and links
                if self.path.endswith(("sessions.json", "links.json")):
                    now = datetime.now().isoformat()
                    data = [d for d in data if d.get("expires_at", "9999-12-31") > now]
                    self._save(data)
                return data
            except (FileNotFoundError, json.JSONDecodeError):
                return []
                
        def _save(self, data):
            with open(self.path,"w") as fh:
                json.dump(data, fh, default=str, indent=2)
                
        def find_one(self, filt):
            data = self._load()
            for d in data:
                ok = True
                for k,v in (filt or {}).items():
                    if d.get(k) != v: 
                        ok=False
                        break
                if ok: 
                    return d
            return None
            
        def find(self, filt=None):
            data = self._load()
            if not filt: 
                return data
            out = []
            for d in data:
                ok = True
                for k,v in filt.items():
                    if d.get(k) != v: 
                        ok=False
                        break
                if ok: 
                    out.append(d)
            return out
            
        def insert_one(self, doc):
            data = self._load()
            data.append(doc)
            self._save(data)
            return {"inserted_id": len(data)}
            
        def update_one(self, filt, update, upsert=False):
            data = self._load()
            found=False
            for i,d in enumerate(data):
                ok=True
                for k,v in filt.items():
                    if d.get(k) != v: 
                        ok=False
                        break
                if ok:
                    if "$set" in update:
                        for kk,vv in update["$set"].items(): 
                            d[kk]=vv
                    data[i]=d
                    found=True
                    break
            if not found and upsert:
                new = dict(filt)
                if "$set" in update: 
                    new.update(update["$set"])
                data.append(new)
            self._save(data)
            
        def delete_many(self, filt):
            data = self._load()
            out=[]
            removed=0
            for d in data:
                match=True
                for k,v in (filt or {}).items():
                    if d.get(k) != v: 
                        match=False
                        break
                if not match: 
                    out.append(d)
                else: 
                    removed+=1
            self._save(out)
            return {"deleted_count": removed}
            
        def count_documents(self, filt=None):
            return len(self.find(filt))

    users_col = SimpleCol(USERS_FILE)
    students_col = SimpleCol(STUDENTS_FILE)
    att_col = SimpleCol(ATT_FILE)
    sessions_col = SimpleCol(SESSIONS_FILE)
    links_col = SimpleCol(LINKS_FILE)

# -------------------- Helpers --------------------
QR_FOLDER = os.path.join(os.path.dirname(__file__), "qrcodes")
BARCODE_FOLDER = os.path.join(os.path.dirname(__file__), "barcodes")
os.makedirs(QR_FOLDER, exist_ok=True)
os.makedirs(BARCODE_FOLDER, exist_ok=True)

def generate_secure_token(length=32):
    """Generate a secure random token"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def make_qr(student_id):
    """Generate QR code for student"""
    img = qrcode.make(student_id, box_size=10, border=4)
    path = os.path.join(QR_FOLDER, f"{student_id}_qr.png")
    img.save(path)
    return path

def make_barcode(student_id):
    """Generate barcode for student"""
    try:
        # Use Code128 barcode format
        code128 = barcode.get_barcode_class('code128')
        barcode_img = code128(student_id, writer=ImageWriter())
        path = os.path.join(BARCODE_FOLDER, f"{student_id}_barcode")
        barcode_img.save(path)
        return f"{path}.png"
    except Exception as e:
        st.error(f"Error generating barcode: {e}")
        return None

def decode_qr_from_image(pil_img):
    """Decode QR code from image"""
    try:
        arr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        detector = cv2.QRCodeDetector()
        data, pts, _ = detector.detectAndDecode(arr)
        if pts is not None and data: 
            return data
        return None
    except Exception as e:
        st.error(f"QR decode error: {e}")
        return None

def decode_barcode_from_image(pil_img):
    """Decode barcode from image using pyzbar"""
    try:
        # Convert PIL image to numpy array
        img_array = np.array(pil_img)
        
        # Decode barcodes
        barcodes = pyzbar.decode(img_array)
        
        if barcodes:
            # Return the first barcode found
            return barcodes[0].data.decode('utf-8')
        return None
    except Exception as e:
        st.error(f"Barcode decode error: {e}")
        return None

def decode_any_code_from_image(pil_img):
    """Try to decode both QR codes and barcodes from image"""
    # First try QR code
    qr_data = decode_qr_from_image(pil_img)
    if qr_data:
        return qr_data, "QR Code"
    
    # Then try barcode
    barcode_data = decode_barcode_from_image(pil_img)
    if barcode_data:
        return barcode_data, "Barcode"
    
    return None, None

def mark_attendance(student_id, status, when_dt=None, course=None, method="manual"):
    """Mark attendance for a student"""
    when_dt = when_dt or datetime.now()
    date_str = when_dt.date().isoformat()
    
    # Check existing attendance
    if use_mongo:
        if att_col.find_one({"student_id": student_id, "date": date_str}):
            return {"error":"already"}
        doc = {
            "student_id": student_id, 
            "date": date_str, 
            "time": when_dt.strftime("%H:%M:%S"), 
            "status": int(status), 
            "course": course, 
            "method": method,
            "ts": when_dt
        }
        att_col.insert_one(doc)
        return {"ok":True, **doc}
    else:
        existing = att_col.find_one({"student_id": student_id, "date": date_str})
        if existing: 
            return {"error":"already"}
        doc = {
            "student_id": student_id, 
            "date": date_str, 
            "time": when_dt.strftime("%H:%M:%S"), 
            "status": int(status), 
            "course": course, 
            "method": method,
            "ts": when_dt.isoformat()
        }
        att_col.insert_one(doc)
        return {"ok":True, **doc}

def create_attendance_session(course=None, duration_hours=24, description=""):
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

def create_student_attendance_link(student_id, duration_hours=168):  # 1 week default
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
        "max_uses": None  # Unlimited by default
    }
    
    links_col.insert_one(link_data)
    return link_id, expires_at

def get_students_df():
    rows = students_col.find({})
    if not rows: 
        return pd.DataFrame(columns=["student_id","name","course","qr_path","barcode_path"])
    return pd.DataFrame(rows)

def get_attendance_rows(start=None, end=None, course=None):
    if use_mongo:
        q = {}
        if start or end: 
            q["date"] = {}
        if start: 
            q["date"]["$gte"] = start.isoformat()
        if end: 
            q["date"]["$lte"] = end.isoformat()
        if course and course!="All": 
            q["course"]=course
        rows = list(att_col.find(q))
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["student_id","date","status","time","course","method"])
    else:
        rows = att_col.find({})
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["student_id","date","status","time","course","method"])
        
        # Filter by date range for JSON storage
        if not df.empty and (start or end):
            df = df.copy()
            if start:
                df = df[df["date"] >= start.isoformat()]
            if end:
                df = df[df["date"] <= end.isoformat()]
        
        # Filter by course
        if not df.empty and course and course != "All":
            df = df[df["course"] == course]
            
        return df

def pivot_attendance(start, end, course=None):
    students = get_students_df()
    if students.empty: 
        return pd.DataFrame()
    
    all_dates = pd.date_range(start=start, end=end, freq="D").date
    date_cols = [d.isoformat() for d in all_dates]
    rows = get_attendance_rows(start, end, course)
    
    if rows.empty:
        pivot = pd.DataFrame(0, index=students["student_id"], columns=date_cols)
    else:
        pv = rows.pivot_table(index="student_id", columns="date", values="status", aggfunc="max")
        for c in date_cols:
            if c not in pv.columns: 
                pv[c]=0
        pv = pv[date_cols].fillna(0).astype(int)
        pivot = pv
    
    pivot = pivot.reset_index()
    out = students[["student_id","name","course"]].merge(pivot, on="student_id", how="left")
    
    for c in date_cols:
        if c in out.columns: 
            out[c]=out[c].fillna(0).astype(int)
    
    return out[["student_id","name","course"] + date_cols]

# -------------------- Auth --------------------
if "auth" not in st.session_state:
    st.session_state.auth = {"logged_in": False, "username": None, "role": None}
if "unlocked" not in st.session_state:
    st.session_state.unlocked = {}

def bootstrap_admin():
    if use_mongo:
        if users_col.count_documents({}) == 0:
            pw = generate_password_hash("admin123")
            users_col.insert_one({"username":"admin","password":pw,"role":"admin"})
    else:
        if users_col.count_documents({}) == 0:
            users_col.insert_one({"username":"admin","password":generate_password_hash("admin123"),"role":"admin"})

bootstrap_admin()

def authenticate(username, password):
    user = users_col.find_one({"username": username})
    if not user: 
        return False
    stored = user.get("password")
    try:
        return check_password_hash(stored, password)
    except Exception:
        return stored == password

# -------------------- URL Parameter Handling --------------------
def handle_url_params():
    """Handle URL parameters for attendance links and sessions"""
    query_params = st.query_params
    
    # Handle attendance session
    if "session" in query_params:
        session_id = query_params["session"]
        return handle_attendance_session(session_id)
    
    # Handle student attendance link
    if "student_link" in query_params:
        link_id = query_params["student_link"]
        return handle_student_attendance_link(link_id)
    
    return None

def handle_attendance_session(session_id):
    """Handle attendance session access"""
    session = sessions_col.find_one({"session_id": session_id})
    
    if not session:
        st.error("❌ Invalid or expired attendance session")
        st.stop()
    
    # Check if session is still active
    if use_mongo:
        if session["expires_at"] < datetime.now():
            st.error("❌ This attendance session has expired")
            st.stop()
    else:
        if session["expires_at"] < datetime.now().isoformat():
            st.error("❌ This attendance session has expired")
            st.stop()
    
    if not session.get("is_active", True):
        st.error("❌ This attendance session is no longer active")
        st.stop()
    
    # Display session attendance form
    display_session_attendance_form(session)
    st.stop()

def handle_student_attendance_link(link_id):
    """Handle individual student attendance link"""
    link = links_col.find_one({"link_id": link_id})
    
    if not link:
        st.error("❌ Invalid or expired attendance link")
        st.stop()
    
    # Check if link is still active
    if use_mongo:
        if link["expires_at"] < datetime.now():
            st.error("❌ This attendance link has expired")
            st.stop()
    else:
        if link["expires_at"] < datetime.now().isoformat():
            st.error("❌ This attendance link has expired")
            st.stop()
    
    if not link.get("is_active", True):
        st.error("❌ This attendance link is no longer active")
        st.stop()
    
    # Check usage limits
    if link.get("max_uses") and link.get("uses", 0) >= link["max_uses"]:
        st.error("❌ This attendance link has reached its usage limit")
        st.stop()
    
    # Display student attendance form
    display_student_attendance_form(link)
    st.stop()

def display_session_attendance_form(session):
    """Display attendance form for session-based attendance"""
    st.title("📝 Mark Your Attendance")
    st.success(f"✅ Session: {session.get('description', 'Class Attendance')}")
    
    if session.get('course'):
        st.info(f"📚 Course: {session['course']}")
    
    # Show session expiry
    if use_mongo:
        expires_str = session["expires_at"].strftime("%Y-%m-%d %H:%M")
    else:
        expires_dt = datetime.fromisoformat(session["expires_at"])
        expires_str = expires_dt.strftime("%Y-%m-%d %H:%M")
    
    st.warning(f"⏰ Session expires: {expires_str}")
    
    with st.form("session_attendance"):
        student_id = st.text_input("🆔 Enter your Student ID", placeholder="e.g., STU001")
        student_name = st.text_input("👤 Enter your Name", placeholder="Your full name")
        
        st.markdown("---")
        st.write("📷 **Or scan your QR Code/Barcode:**")
        
        camera_image = st.camera_input("Take a photo of your QR code or barcode")
        
        if camera_image is not None:
            try:
                img = Image.open(camera_image)
                code_data, code_type = decode_any_code_from_image(img)
                
                if code_data:
                    st.success(f"🔍 {code_type} detected: {code_data}")
                    student_id = code_data  # Auto-fill the student ID
                else:
                    st.warning("❌ No QR code or barcode detected. Please try again or enter your ID manually.")
            except Exception as e:
                st.error(f"Error processing image: {str(e)}")
        
        if st.form_submit_button("✅ Mark Present", type="primary"):
            if not student_id:
                st.error("Please enter your Student ID or scan your code")
            elif not student_name:
                st.error("Please enter your name")
            else:
                # Verify student exists
                student = students_col.find_one({"student_id": student_id})
                if not student:
                    st.error("❌ Student ID not found in database. Please contact your teacher.")
                else:
                    # Check if name matches (case insensitive)
                    if student.get("name", "").lower() != student_name.lower():
                        st.warning("⚠️ Name doesn't match our records, but attendance will be marked.")
                    
                    # Mark attendance
                    result = mark_attendance(
                        student_id, 
                        1,  # Present
                        datetime.now(), 
                        course=session.get("course"),
                        method="session_link"
                    )
                    
                    if "error" in result and result["error"] == "already":
                        st.warning(f"⚠️ Attendance already marked for today!")
                    else:
                        st.success(f"🎉 Attendance marked successfully for {student.get('name', student_name)}!")
                        
                        # Update session usage count
                        if use_mongo:
                            sessions_col.update_one(
                                {"session_id": session["session_id"]},
                                {"$inc": {"attendance_count": 1}}
                            )
                        else:
                            # For JSON storage, we need to manually update
                            current_count = session.get("attendance_count", 0)
                            sessions_col.update_one(
                                {"session_id": session["session_id"]},
                                {"$set": {"attendance_count": current_count + 1}}
                            )

def display_student_attendance_form(link):
    """Display attendance form for individual student link"""
    student = students_col.find_one({"student_id": link["student_id"]})
    
    if not student:
        st.error("❌ Student not found")
        st.stop()
    
    st.title("📝 Mark Your Attendance")
    st.success(f"👤 Welcome, {student.get('name', link['student_id'])}!")
    
    if student.get('course'):
        st.info(f"📚 Course: {student['course']}")
    
    # Show link expiry
    if use_mongo:
        expires_str = link["expires_at"].strftime("%Y-%m-%d %H:%M")
    else:
        expires_dt = datetime.fromisoformat(link["expires_at"])
        expires_str = expires_dt.strftime("%Y-%m-%d %H:%M")
    
    st.warning(f"⏰ Link expires: {expires_str}")
    
    # Show usage info
    uses = link.get("uses", 0)
    max_uses = link.get("max_uses")
    if max_uses:
        st.info(f"📊 Usage: {uses}/{max_uses}")
    else:
        st.info(f"📊 Total uses: {uses}")
    
    if st.button("✅ Mark Present for Today", type="primary", use_container_width=True):
        # Mark attendance
        result = mark_attendance(
            link["student_id"], 
            1,  # Present
            datetime.now(), 
            course=student.get("course"),
            method="personal_link"
        )
        
        if "error" in result and result["error"] == "already":
            st.warning(f"⚠️ Attendance already marked for today!")
        else:
            st.success(f"🎉 Attendance marked successfully!")
            
            # Update link usage count
            if use_mongo:
                links_col.update_one(
                    {"link_id": link["link_id"]},
                    {"$inc": {"uses": 1}}
                )
            else:
                current_uses = link.get("uses", 0)
                links_col.update_one(
                    {"link_id": link["link_id"]},
                    {"$set": {"uses": current_uses + 1}}
                )
            
            st.balloons()

# Check for URL parameters first
if handle_url_params():
    st.stop()

def login_flow():
    st.title("🔐 Login")
    st.info("Default admin credentials: username='admin', password='admin123'")
    
    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            if authenticate(u,p):
                user_data = users_col.find_one({"username":u})
                st.session_state.auth.update({
                    "logged_in": True, 
                    "username": u, 
                    "role": user_data.get("role","teacher")
                })
                st.success("Logged in successfully!")
                st.rerun()
            else:
                st.error("Invalid credentials")
    st.stop()

if not st.session_state.auth["logged_in"]:
    login_flow()

# Sidebar and nav
st.sidebar.title("Smart Attendance — Enhanced")
st.sidebar.write(f"👤 {st.session_state.auth['username']} ({st.session_state.auth['role']})")

nav = st.sidebar.radio("Navigate to:", [
    "Dashboard",
    "Students", 
    "Scan QR/Barcode",
    "Manual Entry",
    "Bulk Entry",
    "Share Links",
    "Attendance Records",
    "Settings",
    "Teachers"
])

if st.sidebar.button("Logout"):
    st.session_state.auth = {"logged_in": False, "username": None, "role": None}
    st.session_state.unlocked = {}
    st.rerun()

# Helper: re-auth per page
def require_reauth(page):
    if st.session_state.unlocked.get(page): 
        return True
    
    st.warning("⚠️ This section requires re-authentication for security.")
    
    with st.form(f"reauth_{page}"):
        u = st.text_input("Username (current)")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Unlock"):
            if u != st.session_state.auth["username"]:
                st.error("Please use the currently logged-in username")
            elif authenticate(u,p):
                st.session_state.unlocked[page] = True
                st.success("✅ Unlocked for this session")
                st.rerun()
            else:
                st.error("Invalid credentials")
    st.stop()

# -------------------- Pages --------------------
if nav == "Dashboard":
    st.title("📊 Dashboard (Pivot View)")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        start = st.date_input("Start Date", date.today()-timedelta(days=7))
    with col2:
        end = st.date_input("End Date", date.today())
    with col3:
        students_df = get_students_df()
        courses = ["All"] + sorted({r.get("course","") for r in students_df.to_dict("records") if r.get("course")})
        course = st.selectbox("Course Filter", courses)
    
    pivot_df = pivot_attendance(start, end, None if course=="All" else course)
    
    if pivot_df.empty:
        st.info("📭 No attendance data found for the selected criteria")
    else:
        st.dataframe(pivot_df.sort_values(["course","student_id"]), use_container_width=True)
        
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "📥 Download CSV", 
                data=pivot_df.to_csv(index=False).encode(), 
                file_name="attendance_pivot.csv",
                mime="text/csv"
            )
        with col2:
            mem = BytesIO()
            with pd.ExcelWriter(mem, engine="xlsxwriter") as writer: 
                pivot_df.to_excel(writer, index=False)
            mem.seek(0)
            st.download_button(
                "📊 Download Excel", 
                data=mem, 
                file_name="attendance_pivot.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

elif nav == "Students":
    st.title("👨‍🎓 Manage Students")
    
    with st.expander("➕ Add New Student"):
        with st.form("add_student"):
            sid = st.text_input("Student ID *", help="Enter unique student ID")
            name = st.text_input("Student Name *")
            course = st.text_input("Course")
            
            if st.form_submit_button("Add Student"):
                if not sid or not name:
                    st.error("Student ID and Name are required")
                elif students_col.find_one({"student_id": sid}):
                    st.warning("⚠️ Student ID already exists")
                else:
                    try:
                        qr_path = make_qr(sid)
                        barcode_path = make_barcode(sid)
                        students_col.insert_one({
                            "student_id": sid, 
                            "name": name, 
                            "course": course,
                            "qr_path": qr_path,
                            "barcode_path": barcode_path
                        })
                        st.success(f"✅ Student {name} added successfully with QR code and barcode generated")
                    except Exception as e:
                        st.error(f"Error adding student: {e}")
    
    st.subheader("📤 Bulk Upload CSV")
    st.info("CSV format: student_id, name, course (with headers)")
    
    uploaded_file = st.file_uploader("Choose CSV file", type=["csv"])
    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file)
            st.dataframe(df, use_container_width=True)
            
            if st.button("Import Students from CSV"):
                inserted = skipped = 0
                for _, row in df.iterrows():
                    sid = str(row.get("student_id", "")).strip()
                    name = str(row.get("name", "")).strip()
                    course = str(row.get("course", "")).strip()
                    
                    if not sid or not name:
                        continue
                        
                    if students_col.find_one({"student_id": sid}):
                        skipped += 1
                        continue
                        
                    try:
                        qr_path = make_qr(sid)
                        barcode_path = make_barcode(sid)
                        students_col.insert_one({
                            "student_id": sid, 
                            "name": name, 
                            "course": course,
                            "qr_path": qr_path,
                            "barcode_path": barcode_path
                        })
                        inserted += 1
                    except Exception as e:
                        st.error(f"Error importing {sid}: {e}")
                        
                st.success(f"✅ Imported {inserted} students, skipped {skipped} duplicates")
        except Exception as e:
            st.error(f"Error reading CSV: {e}")
    
    # Display existing students
    df_students = get_students_df()
    if not df_students.empty:
        st.subheader("📋 Current Students")
        st.dataframe(df_students.sort_values(["course","student_id"]), use_container_width=True)
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📦 Download All QR Codes as ZIP"):
                try:
                    mem = io.BytesIO()
                    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
                        for _, row in df_students.iterrows():
                            qr_path = row.get("qr_path")
                            if qr_path and os.path.exists(qr_path):
                                zf.write(qr_path, arcname=f"QR_{row['student_id']}.png")
                    
                    mem.seek(0)
                    st.download_button(
                        "Download QR Codes ZIP", 
                        data=mem.getvalue(), 
                        file_name="qrcodes.zip",
                        mime="application/zip"
                    )
                except Exception as e:
                    st.error(f"Error creating QR ZIP: {e}")
        
        with col2:
            if st.button("📦 Download All Barcodes as ZIP"):
                try:
                    mem = io.BytesIO()
                    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
                        for _, row in df_students.iterrows():
                            barcode_path = row.get("barcode_path")
                            if barcode_path and os.path.exists(barcode_path):
                                zf.write(barcode_path, arcname=f"Barcode_{row['student_id']}.png")
                    
                    mem.seek(0)
                    st.download_button(
                        "Download Barcodes ZIP", 
                        data=mem.getvalue(), 
                        file_name="barcodes.zip",
                        mime="application/zip"
                    )
                except Exception as e:
                    st.error(f"Error creating Barcode ZIP: {e}")
    else:
        st.info("📭 No students found. Add some students first.")

elif nav == "Scan QR/Barcode":
    st.title("📷 Scan QR Code or Barcode for Attendance")
    
    chosen_date = st.date_input("Select Date", value=date.today())
    st.info("📱 Take a photo of the student's QR code or barcode using the camera below")
    
    # Option for different scanning methods
    scan_method = st.radio("Choose scanning method:", 
                          ["📷 Camera", "📁 Upload Image", "⌨️ Manual Barcode Scanner"])
    
    if scan_method == "📷 Camera":
        camera_image = st.camera_input("Take a photo of QR code or barcode")
        
        if camera_image is not None:
            try:
                img = Image.open(camera_image)
                st.image(img, caption="Captured Image", width=300)
                
                with st.spinner("Decoding QR code/barcode..."):
                    code_data, code_type = decode_any_code_from_image(img)
                    
                if code_data:
                    st.success(f"🔍 {code_type} detected: {code_data}")
                    
                    student = students_col.find_one({"student_id": code_data})
                    if not student:
                        st.error("❌ Student not found in database")
                    else:
                        combined_datetime = datetime.combine(chosen_date, datetime.now().time())
                        result = mark_attendance(
                            code_data, 
                            1,  # Present
                            combined_datetime, 
                            course=student.get("course"),
                            method="camera_scan"
                        )
                        
                        if "error" in result and result["error"] == "already":
                            st.warning(f"⚠️ Attendance already marked for {student.get('name', code_data)} on {chosen_date}")
                        else:
                            st.success(f"✅ Marked {student.get('name', code_data)} as PRESENT for {chosen_date}")
                            st.balloons()
                else:
                    st.warning("❌ No QR code or barcode detected in the image. Please try again.")
                    
            except Exception as e:
                st.error(f"Error processing image: {str(e)}")
    
    elif scan_method == "📁 Upload Image":
        uploaded_image = st.file_uploader("Upload an image containing QR code or barcode", 
                                         type=['png', 'jpg', 'jpeg'])
        
        if uploaded_image is not None:
            try:
                img = Image.open(uploaded_image)
                st.image(img, caption="Uploaded Image", width=300)
                
                with st.spinner("Decoding QR code/barcode..."):
                    code_data, code_type = decode_any_code_from_image(img)
                    
                if code_data:
                    st.success(f"🔍 {code_type} detected: {code_data}")
                    
                    student = students_col.find_one({"student_id": code_data})
                    if not student:
                        st.error("❌ Student not found in database")
                    else:
                        combined_datetime = datetime.combine(chosen_date, datetime.now().time())
                        result = mark_attendance(
                            code_data, 
                            1,  # Present
                            combined_datetime, 
                            course=student.get("course"),
                            method="upload_scan"
                        )
                        
                        if "error" in result and result["error"] == "already":
                            st.warning(f"⚠️ Attendance already marked for {student.get('name', code_data)} on {chosen_date}")
                        else:
                            st.success(f"✅ Marked {student.get('name', code_data)} as PRESENT for {chosen_date}")
                else:
                    st.warning("❌ No QR code or barcode detected in the image. Please try again.")
                    
            except Exception as e:
                st.error(f"Error processing image: {str(e)}")
    
    elif scan_method == "⌨️ Manual Barcode Scanner":
        st.info("💡 Use this option if you have a barcode scanner device connected to your computer.")
        st.markdown("**Instructions:**")
        st.markdown("1. Click in the input field below")
        st.markdown("2. Scan the barcode with your scanner device")
        st.markdown("3. The barcode data will appear automatically")
        st.markdown("4. Click 'Mark Attendance' to save")
        
        with st.form("barcode_scanner"):
            scanned_code = st.text_input("Scan barcode here:", placeholder="Click here and scan with your barcode scanner")
            
            if st.form_submit_button("✅ Mark Attendance"):
                if not scanned_code:
                    st.error("Please scan a barcode first")
                else:
                    student = students_col.find_one({"student_id": scanned_code})
                    if not student:
                        st.error("❌ Student not found in database")
                    else:
                        combined_datetime = datetime.combine(chosen_date, datetime.now().time())
                        result = mark_attendance(
                            scanned_code, 
                            1,  # Present
                            combined_datetime, 
                            course=student.get("course"),
                            method="scanner_device"
                        )
                        
                        if "error" in result and result["error"] == "already":
                            st.warning(f"⚠️ Attendance already marked for {student.get('name', scanned_code)} on {chosen_date}")
                        else:
                            st.success(f"✅ Marked {student.get('name', scanned_code)} as PRESENT for {chosen_date}")

elif nav == "Manual Entry":
    require_reauth("manual")
    st.title("✍️ Manual Attendance Entry")
    
    with st.form("manual_entry"):
        col1, col2 = st.columns(2)
        
        with col1:
            student_id = st.text_input("Student ID *")
            status_options = [("Present", 1), ("Absent", 0)]
            status = st.selectbox("Attendance Status", status_options, format_func=lambda x: x[0])
            
        with col2:
            entry_date = st.date_input("Date", date.today())
            course_override = st.text_input("Course (optional)", help="Leave blank to use student's default course")
        
        if st.form_submit_button("Save Attendance"):
            if not student_id:
                st.error("Student ID is required")
            else:
                student = students_col.find_one({"student_id": student_id})
                if not student:
                    st.error("❌ Student not found in database")
                else:
                    combined_datetime = datetime.combine(entry_date, datetime.now().time())
                    final_course = course_override if course_override else student.get("course")
                    
                    result = mark_attendance(
                        student_id, 
                        status[1], 
                        combined_datetime, 
                        course=final_course,
                        method="manual_entry"
                    )
                    
                    if "error" in result and result["error"] == "already":
                        st.warning(f"⚠️ Attendance already recorded for {student.get('name')} on {entry_date}")
                    else:
                        status_text = "PRESENT" if status[1] == 1 else "ABSENT"
                        st.success(f"✅ Marked {student.get('name')} as {status_text} for {entry_date}")

elif nav == "Bulk Entry":
    require_reauth("bulk")
    st.title("📑 Bulk Attendance Entry")
    
    selected_date = st.date_input("Select Date for Bulk Entry", value=date.today())
    
    students = list(students_col.find({}))
    if not students:
        st.info("📭 No students found. Please add students first.")
    else:
        st.info(f"📅 Setting attendance for {selected_date}")
        
        with st.form("bulk_attendance"):
            records = []
            
            # Group by course for better organization
            students_by_course = {}
            for s in students:
                course = s.get("course", "No Course")
                if course not in students_by_course:
                    students_by_course[course] = []
                students_by_course[course].append(s)
            
            for course, course_students in students_by_course.items():
                st.subheader(f"📚 {course}")
                
                for student in sorted(course_students, key=lambda x: x["student_id"]):
                    cols = st.columns([3, 3, 2])
                    
                    with cols[0]:
                        st.markdown(f"**{student['student_id']}**")
                    with cols[1]:
                        st.markdown(f"{student.get('name', '')}")
                    with cols[2]:
                        attendance_value = st.selectbox(
                            "",
                            options=[("Present", 1), ("Absent", 0)],
                            format_func=lambda x: x[0],
                            key=f"bulk_{student['student_id']}",
                            index=0  # Default to Present
                        )
                    
                    records.append({
                        "student_id": student['student_id'],
                        "name": student.get('name', ''),
                        "course": student.get('course', ''),
                        "status": attendance_value[1],
                        "status_text": attendance_value[0]
                    })
            
            if st.form_submit_button("💾 Save Bulk Attendance"):
                saved = skipped = 0
                
                for record in records:
                    existing = att_col.find_one({
                        "student_id": record["student_id"], 
                        "date": str(selected_date)
                    })
                    
                    if existing:
                        skipped += 1
                        continue
                    
                    try:
                        att_col.insert_one({
                            "student_id": record["student_id"],
                            "date": str(selected_date),
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "status": record["status"],
                            "course": record.get("course"),
                            "method": "bulk_entry",
                            "ts": datetime.now() if use_mongo else datetime.now().isoformat()
                        })
                        saved += 1
                    except Exception as e:
                        st.error(f"Error saving attendance for {record['student_id']}: {e}")
                
                st.success(f"✅ Saved {saved} attendance records. Skipped {skipped} duplicates.")

elif nav == "Share Links":
    st.title("🔗 Share Attendance Links")
    
    tab1, tab2, tab3 = st.tabs(["📋 Session Links", "👤 Student Links", "📊 Link Management"])
    
    with tab1:
        st.subheader("📋 Create Session Attendance Link")
        st.info("Create a shareable link for students to mark their attendance for a specific session/class.")
        
        with st.form("create_session"):
            session_desc = st.text_input("Session Description", placeholder="e.g., Math Class - Chapter 5")
            session_course = st.text_input("Course (optional)", placeholder="e.g., Mathematics")
            duration = st.selectbox("Link Valid For:", [
                ("1 hour", 1),
                ("3 hours", 3), 
                ("6 hours", 6),
                ("12 hours", 12),
                ("24 hours", 24),
                ("48 hours", 48),
                ("1 week", 168)
            ], format_func=lambda x: x[0])
            
            submit_button = st.form_submit_button("🔗 Create Session Link")
        
        # Move all download and display logic outside the form
        if submit_button:
            try:
                session_id, expires_at = create_attendance_session(
                    course=session_course if session_course else None,
                    duration_hours=duration[1],
                    description=session_desc if session_desc else "Class Attendance"
                )
                
                # Generate the shareable URL
                base_url = st.get_option('server.baseUrlPath') or 'http://localhost:8501'
                share_url = f"{base_url}?session={session_id}"
                
                st.success("✅ Session link created successfully!")
                st.info(f"🕒 Expires: {expires_at.strftime('%Y-%m-%d %H:%M')}")
                
                # Display the shareable link
                st.text_area("📋 Share this link with students:", value=share_url, height=100)
                
                # QR code for the link
                qr_img = qrcode.make(share_url, box_size=8, border=4)
                qr_buffer = io.BytesIO()
                qr_img.save(qr_buffer, format='PNG')
                qr_buffer.seek(0)
                
                st.image(qr_buffer, caption="QR Code for Session Link", width=200)
                
                st.download_button(
                    "📥 Download QR Code",
                    data=qr_buffer.getvalue(),
                    file_name=f"session_qr_{session_id[:8]}.png",
                    mime="image/png"
                )
                
            except Exception as e:
                st.error(f"Error creating session: {e}")
    
    with tab2:
        st.subheader("👤 Create Student Personal Links")
        st.info("Create personal attendance links for individual students.")
        
        students_df = get_students_df()
        if students_df.empty:
            st.warning("No students found. Please add students first.")
        else:
            with st.form("create_student_links"):
                selected_students = st.multiselect(
                    "Select Students:", 
                    options=students_df["student_id"].tolist(),
                    format_func=lambda x: f"{x} - {students_df[students_df['student_id']==x]['name'].iloc[0]}"
                )
                
                duration = st.selectbox("Link Valid For:", [
                    ("1 day", 24),
                    ("3 days", 72), 
                    ("1 week", 168),
                    ("2 weeks", 336),
                    ("1 month", 720)
                ], format_func=lambda x: x[0], index=2)  # Default to 1 week
                
                max_uses = st.number_input("Maximum Uses (0 = unlimited):", min_value=0, value=0)
                
                if st.form_submit_button("🔗 Create Student Links"):
                    if not selected_students:
                        st.error("Please select at least one student")
                    else:
                        created_links = []
                        base_url = st.get_option('server.baseUrlPath') or 'http://localhost:8501'
                        
                        for student_id in selected_students:
                            try:
                                link_id, expires_at = create_student_attendance_link(
                                    student_id=student_id,
                                    duration_hours=duration[1]
                                )
                                
                                if max_uses > 0:
                                    links_col.update_one(
                                        {"link_id": link_id},
                                        {"$set": {"max_uses": max_uses}}
                                    )
                                
                                share_url = f"{base_url}?student_link={link_id}"
                                student_name = students_df[students_df['student_id']==student_id]['name'].iloc[0]
                                
                                created_links.append({
                                    "student_id": student_id,
                                    "student_name": student_name,
                                    "link": share_url,
                                    "expires": expires_at.strftime('%Y-%m-%d %H:%M')
                                })
                                
                            except Exception as e:
                                st.error(f"Error creating link for {student_id}: {e}")
                        
                        if created_links:
                            st.success(f"✅ Created {len(created_links)} student links!")
                            
                            # Display created links
                            for link_info in created_links:
                                with st.expander(f"📋 {link_info['student_name']} ({link_info['student_id']})"):
                                    st.text_area(f"Link for {link_info['student_name']}:", 
                                               value=link_info['link'], height=100)
                                    st.info(f"🕒 Expires: {link_info['expires']}")
                            
                            # Create downloadable CSV with all links
                            links_df = pd.DataFrame(created_links)
                            csv_data = links_df.to_csv(index=False)
                            
                            st.download_button(
                                "📥 Download All Links as CSV",
                                data=csv_data.encode(),
                                file_name=f"student_attendance_links_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                                mime="text/csv"
                            )
    
    with tab3:
        st.subheader("📊 Link Management")
        
        # Active Sessions
        st.markdown("### 📋 Active Sessions")
        try:
            if use_mongo:
                sessions = list(sessions_col.find({"is_active": True, "expires_at": {"$gt": datetime.now()}}))
            else:
                all_sessions = sessions_col.find({"is_active": True})
                sessions = [s for s in all_sessions if s.get("expires_at", "9999-12-31") > datetime.now().isoformat()]
            
            if sessions:
                sessions_data = []
                for session in sessions:
                    if use_mongo:
                        expires_str = session["expires_at"].strftime("%Y-%m-%d %H:%M")
                        created_str = session["created_at"].strftime("%Y-%m-%d %H:%M")
                    else:
                        expires_str = datetime.fromisoformat(session["expires_at"]).strftime("%Y-%m-%d %H:%M")
                        created_str = datetime.fromisoformat(session["created_at"]).strftime("%Y-%m-%d %H:%M")
                    
                    sessions_data.append({
                        "Description": session.get("description", "N/A"),
                        "Course": session.get("course", "N/A"),
                        "Created": created_str,
                        "Expires": expires_str,
                        "Attendance Count": session.get("attendance_count", 0),
                        "Session ID": session["session_id"][:8] + "..."
                    })
                
                st.dataframe(pd.DataFrame(sessions_data), use_container_width=True)
            else:
                st.info("No active sessions found")
        except Exception as e:
            st.error(f"Error loading sessions: {e}")
        
        # Active Student Links
        st.markdown("### 👤 Active Student Links")
        try:
            if use_mongo:
                links = list(links_col.find({"is_active": True, "expires_at": {"$gt": datetime.now()}}))
            else:
                all_links = links_col.find({"is_active": True})
                links = [l for l in all_links if l.get("expires_at", "9999-12-31") > datetime.now().isoformat()]
            
            if links:
                links_data = []
                for link in links:
                    if use_mongo:
                        expires_str = link["expires_at"].strftime("%Y-%m-%d %H:%M")
                        created_str = link["created_at"].strftime("%Y-%m-%d %H:%M")
                    else:
                        expires_str = datetime.fromisoformat(link["expires_at"]).strftime("%Y-%m-%d %H:%M")
                        created_str = datetime.fromisoformat(link["created_at"]).strftime("%Y-%m-%d %H:%M")
                    
                    student = students_col.find_one({"student_id": link["student_id"]})
                    student_name = student.get("name", "Unknown") if student else "Unknown"
                    
                    links_data.append({
                        "Student ID": link["student_id"],
                        "Student Name": student_name,
                        "Created": created_str,
                        "Expires": expires_str,
                        "Uses": link.get("uses", 0),
                        "Max Uses": link.get("max_uses", "Unlimited"),
                        "Link ID": link["link_id"][:8] + "..."
                    })
                
                st.dataframe(pd.DataFrame(links_data), use_container_width=True)
            else:
                st.info("No active student links found")
        except Exception as e:
            st.error(f"Error loading student links: {e}")

elif nav == "Attendance Records":
    st.title("📋 Attendance Records")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        start_date = st.date_input("Start Date", date.today() - timedelta(days=7))
    with col2:
        end_date = st.date_input("End Date", date.today())
    with col3:
        students_df = get_students_df()
        courses = ["All"] + sorted({r.get("course","") for r in students_df.to_dict("records") if r.get("course")})
        course_filter = st.selectbox("Course Filter", courses)
    
    # Show attendance method filter
    method_filter = st.selectbox("Attendance Method Filter", [
        "All", "manual_entry", "camera_scan", "upload_scan", "scanner_device", 
        "bulk_entry", "session_link", "personal_link"
    ])
    
    pivot_df = pivot_attendance(start_date, end_date, None if course_filter=="All" else course_filter)
    
    if pivot_df.empty:
        st.info("📭 No attendance records found for the selected date range")
    else:
        # Show detailed records as well
        detailed_records = get_attendance_rows(start_date, end_date, None if course_filter=="All" else course_filter)
        
        # Filter by method if specified
        if not detailed_records.empty and method_filter != "All":
            detailed_records = detailed_records[detailed_records.get("method", "manual_entry") == method_filter]
        
        st.subheader("📊 Pivot View")
        st.dataframe(pivot_df.sort_values(["course", "student_id"]), use_container_width=True)
        
        st.subheader("📝 Detailed Records")
        if not detailed_records.empty:
            # Add student names to detailed records
            students_info = get_students_df()[["student_id", "name"]].set_index("student_id").to_dict()["name"]
            detailed_records["student_name"] = detailed_records["student_id"].map(students_info)
            
            # Reorder columns
            display_cols = ["date", "student_id", "student_name", "status", "time", "course", "method"]
            display_cols = [col for col in display_cols if col in detailed_records.columns]
            
            st.dataframe(detailed_records[display_cols].sort_values(["date", "student_id"]), 
                        use_container_width=True)
        else:
            st.info("No detailed records match the selected filters")
        
        # Export options
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "📥 Download Pivot as CSV",
                data=pivot_df.to_csv(index=False).encode(),
                file_name=f"attendance_pivot_{start_date}_to_{end_date}.csv",
                mime="text/csv"
            )
        
        with col2:
            if not detailed_records.empty:
                st.download_button(
                    "📥 Download Details as CSV",
                    data=detailed_records[display_cols].to_csv(index=False).encode(),
                    file_name=f"attendance_details_{start_date}_to_{end_date}.csv",
                    mime="text/csv"
                )

elif nav == "Settings":
    require_reauth("settings")
    st.title("⚙️ Settings")
    
    if st.session_state.auth.get("role") != "admin":
        st.warning("⚠️ Some settings require administrator privileges")
    
    # Password change
    st.subheader("🔒 Change Password")
    with st.form("change_password"):
        current_password = st.text_input("Current Password", type="password")
        new_password = st.text_input("New Password", type="password")
        confirm_password = st.text_input("Confirm New Password", type="password")
        
        if st.form_submit_button("Change Password"):
            if not current_password or not new_password:
                st.error("All fields are required")
            elif new_password != confirm_password:
                st.error("New passwords do not match")
            elif authenticate(st.session_state.auth['username'], current_password):
                try:
                    if use_mongo:
                        users_col.update_one(
                            {"username": st.session_state.auth['username']}, 
                            {"$set": {"password": generate_password_hash(new_password)}}
                        )
                    else:
                        users_col.update_one(
                            {"username": st.session_state.auth['username']}, 
                            {"$set": {"password": generate_password_hash(new_password)}}, 
                            upsert=True
                        )
                    st.success("✅ Password updated successfully")
                except Exception as e:
                    st.error(f"Error updating password: {e}")
            else:
                st.error("❌ Current password is incorrect")
    
    # System Information
    st.subheader("ℹ️ System Information")
    st.info(f"Database: {'MongoDB' if use_mongo else 'JSON Files'}")
    
    students_count = students_col.count_documents({})
    attendance_count = att_col.count_documents({})
    active_sessions = sessions_col.count_documents({"is_active": True}) if use_mongo else len([s for s in sessions_col.find({"is_active": True}) if s.get("expires_at", "9999-12-31") > datetime.now().isoformat()])
    active_links = links_col.count_documents({"is_active": True}) if use_mongo else len([l for l in links_col.find({"is_active": True}) if l.get("expires_at", "9999-12-31") > datetime.now().isoformat()])
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("👨‍🎓 Students", students_count)
    with col2:
        st.metric("📝 Attendance Records", attendance_count)
    with col3:
        st.metric("📋 Active Sessions", active_sessions)
    with col4:
        st.metric("🔗 Active Links", active_links)
    
    # Admin-only settings
    if st.session_state.auth.get("role") == "admin":
        st.subheader("🗑️ Data Management (Admin Only)")
        st.warning("⚠️ The following actions are irreversible!")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Clear All Students & Attendance", type="secondary"):
                try:
                    students_col.delete_many({})
                    att_col.delete_many({})
                    st.success("✅ All student and attendance data cleared")
                    
                    # Clean up QR code and barcode files
                    import shutil
                    for folder in [QR_FOLDER, BARCODE_FOLDER]:
                        if os.path.exists(folder):
                            shutil.rmtree(folder)
                            os.makedirs(folder, exist_ok=True)
                            
                except Exception as e:
                    st.error(f"Error clearing data: {e}")
        
        with col2:
            if st.button("Clear All Links & Sessions", type="secondary"):
                try:
                    sessions_col.delete_many({})
                    links_col.delete_many({})
                    st.success("✅ All links and sessions cleared")
                except Exception as e:
                    st.error(f"Error clearing links: {e}")

elif nav == "Teachers":
    st.title("👥 Teacher Management")
    
    if st.session_state.auth.get("role") != "admin":
        st.warning("⚠️ This section is restricted to administrators only")
    else:
        # Add new teacher
        st.subheader("➕ Add New Teacher")
        with st.form("add_teacher"):
            new_username = st.text_input("Username *")
            new_password = st.text_input("Password *", type="password")
            new_role = st.selectbox("Role", ["teacher", "admin"])
            
            if st.form_submit_button("Add Teacher"):
                if not new_username or not new_password:
                    st.error("Username and password are required")
                elif users_col.find_one({"username": new_username}):
                    st.error("❌ Username already exists")
                else:
                    try:
                        users_col.insert_one({
                            "username": new_username,
                            "password": generate_password_hash(new_password),
                            "role": new_role
                        })
                        st.success(f"✅ Teacher {new_username} added successfully with {new_role} role")
                    except Exception as e:
                        st.error(f"Error adding teacher: {e}")
        
        # Display existing teachers
        st.subheader("👥 Current Teachers")
        try:
            users = list(users_col.find({}))
            if users:
                # Create a clean display dataframe
                users_display = []
                for user in users:
                    users_display.append({
                        "Username": user.get("username", ""),
                        "Role": user.get("role", "teacher"),
                        "Created": "System" if user.get("username") == "admin" else "Manual"
                    })
                
                users_df = pd.DataFrame(users_display)
                st.dataframe(users_df, use_container_width=True)
                
                # Delete teacher functionality
                st.subheader("🗑️ Remove Teacher")
                usernames_to_delete = [u["username"] for u in users if u["username"] != "admin"]
                
                if usernames_to_delete:
                    with st.form("delete_teacher"):
                        username_to_delete = st.selectbox("Select teacher to remove", usernames_to_delete)
                        confirm_delete = st.checkbox("I confirm I want to delete this teacher")
                        
                        if st.form_submit_button("Delete Teacher", type="secondary"):
                            if not confirm_delete:
                                st.error("Please confirm the deletion")
                            else:
                                try:
                                    result = users_col.delete_many({"username": username_to_delete})
                                    if result.get("deleted_count", 0) > 0:
                                        st.success(f"✅ Teacher {username_to_delete} removed successfully")
                                    else:
                                        st.error("Teacher not found or already deleted")
                                except Exception as e:
                                    st.error(f"Error deleting teacher: {e}")
                else:
                    st.info("Only the admin user exists. Add more teachers above.")
            else:
                st.info("No teachers found in the system.")
        except Exception as e:
            st.error(f"Error loading teachers: {e}")

# -------------------- Footer --------------------
st.sidebar.markdown("---")
st.sidebar.markdown("**Smart Attendance System v3.0 Enhanced**")
st.sidebar.markdown("🔧 Database: " + ("MongoDB" if use_mongo else "JSON Files"))

# Add some helpful information based on current page
if nav == "Dashboard":
    st.sidebar.markdown("---")
    st.sidebar.info("💡 **Tip**: Use the pivot view to see attendance patterns across multiple days. Present=1, Absent=0.")

elif nav == "Scan QR/Barcode":
    st.sidebar.markdown("---")
    st.sidebar.info("💡 **Tips**:\n- Ensure good lighting for camera scanning\n- Hold codes steady for best results\n- Use barcode scanner option for hardware scanners")

elif nav == "Students":
    st.sidebar.markdown("---")
    st.sidebar.info("💡 **Tip**: Both QR codes and barcodes are generated for each student for maximum compatibility.")

elif nav == "Share Links":
    st.sidebar.markdown("---")
    st.sidebar.info("💡 **Link Types**:\n- Session links: For entire classes\n- Student links: Personal attendance links\n- Both expire automatically for security")

# Additional requirements note
st.sidebar.markdown("---")
st.sidebar.markdown("📦 **Required Libraries:**")
st.sidebar.markdown("""
**Core (Required):**
- `streamlit`
- `pandas`
- `pymongo`
- `qrcode`
- `opencv-python`
- `Pillow`
- `werkzeug`
- `numpy`

**Optional (for barcode support):**
- `pyzbar` (requires system libraries)
- `python-barcode`
""")

st.sidebar.markdown("---")
st.sidebar.markdown("🚀 **Features Status:**")
features_status = "✅ QR Code scanning & generation\n"
if BARCODE_GENERATION_AVAILABLE:
    features_status += "✅ Barcode generation\n"
else:
    features_status += "❌ Barcode generation (install python-barcode)\n"
    
if PYZBAR_AVAILABLE:
    features_status += "✅ Barcode image scanning\n"
else:
    features_status += "❌ Barcode image scanning (install pyzbar)\n"

features_status += """✅ Shareable attendance links
✅ Session-based attendance
✅ Personal student links
✅ Multiple scanning methods
✅ Hardware scanner support
✅ Enhanced security"""

st.sidebar.markdown(features_status)

# Instructions for deployment
if st.sidebar.button("📖 Deployment Guide"):
    st.info("""
    ## 🚀 Deployment Instructions
    
    ### Required Python Packages:
    ```bash
    pip install streamlit pandas pymongo qrcode opencv-python pyzbar python-barcode Pillow werkzeug numpy
    ```
    
    ### For QR/Barcode scanning on Linux:
    ```bash
    sudo apt-get install libzbar0
    ```
    
    ### Environment Variables (Optional):
    - `MONGODB_URI`: MongoDB connection string
    - `MONGODB_DB`: Database name
    
    ### Run the application:
    ```bash
    streamlit run app.py
    ```
    
    ### Features:
    1. **QR Code & Barcode Generation**: Automatic generation for all students
    2. **Multiple Scanning Methods**: Camera, upload, hardware scanner
    3. **Shareable Links**: Session and personal attendance links
    4. **Enhanced Security**: Token-based links with expiration
    5. **Flexible Database**: MongoDB or JSON file storage
    
    ### Sharing Links:
    - Session links: Share with entire class for specific sessions
    - Student links: Personal links for individual students
    - All links expire automatically for security
    - Generate QR codes for easy sharing
    """)