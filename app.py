import os
import io
import zipfile
import json
import secrets
import string
import re
from datetime import datetime, date, timedelta
import pandas as pd
import qrcode
from PIL import Image
import cv2
import numpy as np
from pymongo import MongoClient, errors as mongo_errors     

from werkzeug.security import generate_password_hash, check_password_hash
import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

try:
    import barcode
    from barcode.writer import ImageWriter
    import shutil
    BARCODE_GENERATION_AVAILABLE = True
except ImportError:
    BARCODE_GENERATION_AVAILABLE = False

st.set_page_config(page_title="Smart Attendance — Enhanced", layout="wide", initial_sidebar_state="collapsed")

# -------------------- Cookie Manager --------------------
cookies = EncryptedCookieManager(prefix="attendance_", password=os.getenv("COOKIE_SECRET", "supersecretkey"))
if not cookies.ready():
    st.stop()

# -------------------- MongoDB Configuration --------------------
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_DB", "smart_attendance_enhanced")

# -------------------- User Management Class --------------------
class UserManager:
    def __init__(self, users_collection):
        self.users_col = users_collection
        self.PASSWORD_MIN_LENGTH = 8
        self.PASSWORD_REGEX = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
        self.MAX_LOGIN_ATTEMPTS = 5
        self.LOCKOUT_DURATION = timedelta(minutes=30)

    def validate_email(self, email):
        """Validate email format"""
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(email_regex, email))

    def validate_password(self, password):
        """Validate password strength"""
        if not password:
            return False, "Password cannot be empty"
        if len(password) < self.PASSWORD_MIN_LENGTH:
            return False, f"Password must be at least {self.PASSWORD_MIN_LENGTH} characters"
        if not re.match(self.PASSWORD_REGEX, password):
            return False, "Password must contain at least one uppercase letter, one lowercase letter, one number, and one special character"
        return True, ""

    def create_user(self, username, password, email, name, role="teacher"):
        """Create a new user with validated credentials"""
        if not username or len(username) < 3:
            return False, "Username must be at least 3 characters"
        if not self.validate_email(email):
            return False, "Invalid email format"
        if self.users_col.find_one({"username": username}):
            return False, "Username already exists"
        if self.users_col.find_one({"email": email}):
            return False, "Email already exists"
        
        is_valid, error = self.validate_password(password)
        if not is_valid:
            return False, error

        try:
            user_data = {
                "username": username,
                "password": generate_password_hash(password, method='pbkdf2:sha256:600000'),
                "email": email,
                "name": name,
                "role": role,
                "created_at": datetime.now() if use_mongo else datetime.now().isoformat(),
                "last_login": None,
                "failed_attempts": 0,
                "is_locked": False,
                "lockout_until": None,
                "status": "pending",  # Requires email verification
                "two_factor_enabled": False,
                "two_factor_secret": None
            }
            self.users_col.insert_one(user_data)
            # Mock email verification (in real implementation, send verification email)
            return True, "User created successfully. Please check your email for verification."
        except Exception as e:
            return False, f"Error creating user: {str(e)}"

    def authenticate_user(self, username, password):
        """Authenticate user with rate limiting and lockout"""
        user = self.users_col.find_one({"username": username})
        if not user:
            return False, "User not found"
        
        if user.get("is_locked", False):
            lockout_until = user.get("lockout_until")
            if lockout_until and (use_mongo and lockout_until > datetime.now() or 
                                not use_mongo and lockout_until > datetime.now().isoformat()):
                return False, f"Account locked until {lockout_until}"
            else:
                self.users_col.update_one(
                    {"username": username},
                    {"$set": {"is_locked": False, "failed_attempts": 0, "lockout_until": None}}
                )

        if user.get("status") != "active":
            return False, "Account is not verified or is inactive"

        if check_password_hash(user.get("password"), password):
            self.users_col.update_one(
                {"username": username},
                {
                    "$set": {
                        "last_login": datetime.now() if use_mongo else datetime.now().isoformat(),
                        "failed_attempts": 0,
                        "lockout_until": None
                    }
                }
            )
            return True, {
                "username": username,
                "role": user.get("role"),
                "name": user.get("name"),
                "email": user.get("email"),
                "two_factor_enabled": user.get("two_factor_enabled", False)
            }
        else:
            failed_attempts = user.get("failed_attempts", 0) + 1
            is_locked = failed_attempts >= self.MAX_LOGIN_ATTEMPTS
            lockout_until = datetime.now() + self.LOCKOUT_DURATION if is_locked else None
            
            self.users_col.update_one(
                {"username": username},
                {
                    "$set": {
                        "failed_attempts": failed_attempts,
                        "is_locked": is_locked,
                        "lockout_until": lockout_until
                    }
                }
            )
            return False, "Invalid password" if not is_locked else f"Account locked until {lockout_until}"

    def verify_two_factor(self, username, code):
        """Mock 2FA verification (in real implementation, verify with TOTP or SMS)"""
        # This is a mock implementation; real 2FA would use TOTP or SMS verification
        return True, "2FA verified"

    def change_password(self, username, current_password, new_password):
        """Change user password with validation"""
        auth_success, _ = self.authenticate_user(username, current_password)
        if not auth_success:
            return False, "Current password is incorrect"

        is_valid, error = self.validate_password(new_password)
        if not is_valid:
            return False, error

        try:
            self.users_col.update_one(
                {"username": username},
                {
                    "$set": {
                        "password": generate_password_hash(new_password, method='pbkdf2:sha256:600000'),
                        "last_modified": datetime.now() if use_mongo else datetime.now().isoformat()
                    }
                }
            )
            return True, "Password updated successfully"
        except Exception as e:
            return False, f"Error updating password: {str(e)}"

    def generate_reset_token(self, username):
        """Generate a password reset token"""
        try:
            token = generate_secure_token()
            expires = datetime.now() + timedelta(hours=24)
            self.users_col.update_one(
                {"username": username},
                {
                    "$set": {
                        "password_reset_token": token,
                        "password_reset_expires": expires if use_mongo else expires.isoformat()
                    }
                }
            )
            return True, token
        except Exception as e:
            return False, f"Error generating reset token: {str(e)}"

    def reset_password(self, token, new_password):
        """Reset password using a token"""
        is_valid, error = self.validate_password(new_password)
        if not is_valid:
            return False, error

        query = {
            "password_reset_token": token,
            "password_reset_expires": {"$gt": datetime.now()} if use_mongo else {"$gt": datetime.now().isoformat()}
        }
        user = self.users_col.find_one(query)

        if not user:
            return False, "Invalid or expired reset token"

        try:
            self.users_col.update_one(
                {"password_reset_token": token},
                {
                    "$set": {
                        "password": generate_password_hash(new_password, method='pbkdf2:sha256:600000'),
                        "password_reset_token": None,
                        "password_reset_expires": None,
                        "last_modified": datetime.now() if use_mongo else datetime.now().isoformat()
                    }
                }
            )
            return True, "Password reset successfully"
        except Exception as e:
            return False, f"Error resetting password: {str(e)}"

    def verify_email(self, username):
        """Mock email verification (set status to active)"""
        try:
            self.users_col.update_one(
                {"username": username},
                {"$set": {"status": "active"}}
            )
            return True, "Email verified successfully"
        except Exception as e:
            return False, f"Error verifying email: {str(e)}"

# -------------------- Database Setup --------------------
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
    users_col.create_index("username", unique=True)
    users_col.create_index("email", unique=True, sparse=True)
    users_col.create_index("password_reset_token")
    students_col.create_index("student_id", unique=True)
    students_col.create_index("created_by")  # Index for user isolation
    att_col.create_index([("student_id", 1), ("date", 1)], unique=True)
    att_col.create_index("created_by")  # Index for user isolation
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
            with open(f, "w") as fh:
                json.dump([], fh)

    class SimpleCol:
        def __init__(self, path): 
            self.path = path
            
        def _load(self):
            try:
                with open(self.path, "r") as fh:
                    data = json.load(fh)
                if self.path.endswith(("sessions.json", "links.json")):
                    now = datetime.now().isoformat()
                    data = [d for d in data if d.get("expires_at", "9999-12-31") > now]
                    self._save(data)
                return data
            except (FileNotFoundError, json.JSONDecodeError):
                return []
                
        def _save(self, data):
            with open(self.path, "w") as fh:
                json.dump(data, fh, default=str, indent=2)
                
        def find_one(self, filt):
            data = self._load()
            for d in data:
                ok = True
                for k, v in (filt or {}).items():
                    if d.get(k) != v: 
                        ok = False
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
                for k, v in filt.items():
                    if d.get(k) != v: 
                        ok = False
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
            found = False
            for i, d in enumerate(data):
                ok = True
                for k, v in filt.items():
                    if d.get(k) != v: 
                        ok = False
                        break
                if ok:
                    if "$set" in update:
                        for kk, vv in update["$set"].items(): 
                            d[kk] = vv
                    data[i] = d
                    found = True
                    break
            if not found and upsert:
                new = dict(filt)
                if "$set" in update: 
                    new.update(update["$set"])
                data.append(new)
            self._save(data)
            
        def delete_many(self, filt):
            data = self._load()
            out = []
            removed = 0
            for d in data:
                match = True
                for k, v in (filt or {}).items():
                    if d.get(k) != v: 
                        match = False
                        break
                if not match: 
                    out.append(d)
                else: 
                    removed += 1
            self._save(out)
            return {"deleted_count": removed}
            
        def count_documents(self, filt=None):
            return len(self.find(filt))

    users_col = SimpleCol(USERS_FILE)
    students_col = SimpleCol(STUDENTS_FILE)
    att_col = SimpleCol(ATT_FILE)
    sessions_col = SimpleCol(SESSIONS_FILE)
    links_col = SimpleCol(LINKS_FILE)

# -------------------- Data Migration for User Isolation --------------------
def migrate_existing_data_to_user_ownership():
    """One-time migration to add created_by field to existing records"""
    try:
        # Find a default user to assign existing data to
        admin_user = users_col.find_one({"role": "admin"})
        if admin_user:
            default_user = admin_user["username"]
        else:
            first_user = users_col.find_one({})
            if not first_user:
                print("Migration skipped: No users found in database")
                return
            default_user = first_user["username"]

        print(f"Running data migration: assigning unowned data to '{default_user}'")

        if use_mongo:
            # MongoDB mode: use update_many with $exists operator
            students_updated = students_col.update_many(
                {"created_by": {"$exists": False}},
                {"$set": {"created_by": default_user}}
            )
            att_updated = att_col.update_many(
                {"created_by": {"$exists": False}},
                {"$set": {"created_by": default_user}}
            )
            sessions_updated = sessions_col.update_many(
                {"created_by": {"$exists": False}},
                {"$set": {"created_by": default_user}}
            )
            links_updated = links_col.update_many(
                {"created_by": {"$exists": False}},
                {"$set": {"created_by": default_user}}
            )

            print(f"Migration completed: {students_updated.modified_count} students, "
                  f"{att_updated.modified_count} attendance records, "
                  f"{sessions_updated.modified_count} sessions, "
                  f"{links_updated.modified_count} links updated")
        else:
            # JSON mode: iterate and update documents manually
            students_count = 0
            students_data = students_col._load()
            for doc in students_data:
                if "created_by" not in doc:
                    doc["created_by"] = default_user
                    students_count += 1
            if students_count > 0:
                students_col._save(students_data)

            att_count = 0
            att_data = att_col._load()
            for doc in att_data:
                if "created_by" not in doc:
                    doc["created_by"] = default_user
                    att_count += 1
            if att_count > 0:
                att_col._save(att_data)

            sessions_count = 0
            sessions_data = sessions_col._load()
            for doc in sessions_data:
                if "created_by" not in doc:
                    doc["created_by"] = default_user
                    sessions_count += 1
            if sessions_count > 0:
                sessions_col._save(sessions_data)

            links_count = 0
            links_data = links_col._load()
            for doc in links_data:
                if "created_by" not in doc:
                    doc["created_by"] = default_user
                    links_count += 1
            if links_count > 0:
                links_col._save(links_data)

            print(f"Migration completed: {students_count} students, {att_count} attendance records, "
                  f"{sessions_count} sessions, {links_count} links updated")

    except Exception as e:
        print(f"Migration error (non-critical): {e}")

# Initialize UserManager
user_manager = UserManager(users_col)

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

def mark_attendance(student_id, status, when_dt=None, course=None, method="manual"):
    """Mark attendance for a student"""
    when_dt = when_dt or datetime.now()
    date_str = when_dt.date().isoformat()
    
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

def create_student_attendance_link(student_id, duration_hours=168):
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
        
        if not df.empty and (start or end):
            df = df.copy()
            if start:
                df = df[df["date"] >= start.isoformat()]
            if end:
                df = df[df["date"] <= end.isoformat()]
        
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
    st.session_state.auth = {"logged_in": False, "username": None, "role": None, "name": None, "email": None}
if "unlocked" not in st.session_state:
    st.session_state.unlocked = {}
if "page" not in st.session_state:
    st.session_state.page = "login"

def bootstrap_admin():
    """Create default admin user if none exists"""
    if users_col.count_documents({}) == 0:
        success, message = user_manager.create_user(
            username="admin",
            password="admin123",
            email="admin@example.com",
            name="Administrator",
            role="admin"
        )
        if success:
            user_manager.verify_email("admin")  # Auto-verify admin
        else:
            st.error(message)

def login_flow():
    """Login page with improved UI"""
    st.markdown("""
        <style>
        .login-container {
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            background-color: #f9f9f9;
        }
        .login-title {
            text-align: center;
            color: #333;
            margin-bottom: 20px;
        }
        .stButton>button {
            width: 100%;
            background-color: #4CAF50;
            color: white;
            padding: 10px;
            border-radius: 5px;
        }
        .stButton>button:hover {
            background-color: #45a049;
        }
        .stTextInput {
            margin-bottom: 15px;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="login-container">', unsafe_allow_html=True)
    st.markdown('<h2 class="login-title">🔐 Smart Attendance Login</h2>', unsafe_allow_html=True)

    with st.form("login_form"):
        username = st.text_input("Username", placeholder="Enter your username")
        password = st.text_input("Password", type="password", placeholder="Enter your password")
        two_factor_code = st.text_input("2FA Code (if enabled)", placeholder="Enter 6-digit code", disabled=True)
        
        col1, col2 = st.columns(2)
        with col1:
            if st.form_submit_button("Login"):
                user_data = user_manager.authenticate_user(username, password)
                if user_data[0]:
                    if user_data[1].get("two_factor_enabled"):
                        st.session_state.temp_auth = user_data[1]
                        st.session_state.page = "two_factor"
                        st.rerun()
                    else:
                        st.session_state.auth.update({
                            "logged_in": True,
                            "username": user_data[1]["username"],
                            "role": user_data[1]["role"],
                            "name": user_data[1]["name"],
                            "email": user_data[1]["email"]
                        })
                        cookies["session"] = user_data[1]["username"]
                        cookies.save()
                        st.session_state.page = "dashboard"
                        st.success(f"Welcome, {user_data[1].get('name', username)}!")
                        st.rerun()
                else:
                    st.error(user_data[1])
        with col2:
            if st.form_submit_button("Sign Up"):
                st.session_state.page = "signup"
                st.rerun()

    st.markdown('<a href="#" onclick="st.session_state.page=\'forgot_password\';st.rerun()">Forgot Password?</a>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

def signup_flow():
    """Signup page with improved UI"""
    st.markdown("""
        <style>
        .signup-container {
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            background-color: #f9f9f9;
        }
        .signup-title {
            text-align: center;
            color: #333;
            margin-bottom: 20px;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="signup-container">', unsafe_allow_html=True)
    st.markdown('<h2 class="signup-title">📝 Sign Up</h2>', unsafe_allow_html=True)

    with st.form("signup_form"):
        username = st.text_input("Username *", placeholder="Choose a username (min 3 characters)")
        email = st.text_input("Email *", placeholder="Enter your email")
        name = st.text_input("Full Name", placeholder="Enter your full name")
        password = st.text_input("Password *", type="password", placeholder="Create a password")
        confirm_password = st.text_input("Confirm Password *", type="password", placeholder="Confirm your password")
        role = st.selectbox("Role", ["teacher", "admin"], disabled=True)  # Only admins can create admins
        
        col1, col2 = st.columns(2)
        with col1:
            if st.form_submit_button("Sign Up"):
                if password != confirm_password:
                    st.error("Passwords do not match")
                else:
                    success, message = user_manager.create_user(
                        username=username,
                        password=password,
                        email=email,
                        name=name,
                        role="teacher"
                    )
                    if success:
                        st.success(message)
                        st.session_state.page = "login"
                        st.rerun()
                    else:
                        st.error(message)
        with col2:
            if st.form_submit_button("Back to Login"):
                st.session_state.page = "login"
                st.rerun()
    
    st.markdown('</div>', unsafe_allow_html=True)

def two_factor_flow():
    """Two-factor authentication page"""
    st.markdown("""
        <style>
        .two-factor-container {
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            background-color: #f9f9f9;
        }
        .two-factor-title {
            text-align: center;
            color: #333;
            margin-bottom: 20px;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="two-factor-container">', unsafe_allow_html=True)
    st.markdown('<h2 class="two-factor-title">🔐 Two-Factor Authentication</h2>', unsafe_allow_html=True)

    with st.form("two_factor_form"):
        code = st.text_input("Enter 6-digit 2FA Code", placeholder="123456")
        if st.form_submit_button("Verify"):
            success, message = user_manager.verify_two_factor(st.session_state.temp_auth["username"], code)
            if success:
                st.session_state.auth.update({
                    "logged_in": True,
                    "username": st.session_state.temp_auth["username"],
                    "role": st.session_state.temp_auth["role"],
                    "name": st.session_state.temp_auth["name"],
                    "email": st.session_state.temp_auth["email"]
                })
                cookies["session"] = st.session_state.temp_auth["username"]
                cookies.save()
                st.session_state.temp_auth = None
                st.session_state.page = "dashboard"
                st.success("2FA verified successfully!")
                st.rerun()
            else:
                st.error(message)
    
    st.markdown('</div>', unsafe_allow_html=True)

def forgot_password_flow():
    """Forgot password page"""
    st.markdown("""
        <style>
        .forgot-password-container {
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            background-color: #f9f9f9;
        }
        .forgot-password-title {
            text-align: center;
            color: #333;
            margin-bottom: 20px;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="forgot-password-container">', unsafe_allow_html=True)
    st.markdown('<h2 class="forgot-password-title">🔑 Reset Password</h2>', unsafe_allow_html=True)

    with st.form("forgot_password_form"):
        email = st.text_input("Email", placeholder="Enter your email")
        if st.form_submit_button("Send Reset Link"):
            user = users_col.find_one({"email": email})
            if user:
                success, token = user_manager.generate_reset_token(user["username"])
                if success:
                    base_url = st.get_option('server.baseUrlPath') or 'http://localhost:8501'
                    reset_url = f"{base_url}?reset_token={token}"
                    st.success("Reset link sent to your email!")
                    st.text_area("Reset Link (mock email):", value=reset_url, height=100)
                else:
                    st.error(token)
            else:
                st.error("Email not found")
        
        if st.form_submit_button("Back to Login"):
            st.session_state.page = "login"
            st.rerun()
    
    st.markdown('</div>', unsafe_allow_html=True)

def reset_password_flow(token):
    """Password reset page"""
    st.markdown("""
        <style>
        .reset-password-container {
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            background-color: #f9f9f9;
        }
        .reset-password-title {
            text-align: center;
            color: #333;
            margin-bottom: 20px;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="reset-password-container">', unsafe_allow_html=True)
    st.markdown('<h2 class="reset-password-title">🔑 Reset Password</h2>', unsafe_allow_html=True)

    with st.form("reset_password_form"):
        new_password = st.text_input("New Password", type="password")
        confirm_password = st.text_input("Confirm New Password", type="password")
        if st.form_submit_button("Reset Password"):
            if new_password != confirm_password:
                st.error("Passwords do not match")
            else:
                success, message = user_manager.reset_password(token, new_password)
                if success:
                    st.success(message)
                    st.session_state.page = "login"
                    st.rerun()
                else:
                    st.error(message)
    
    st.markdown('</div>', unsafe_allow_html=True)

# -------------------- URL Parameter Handling --------------------
def handle_url_params():
    """Handle URL parameters for attendance links, sessions, and password reset"""
    query_params = st.query_params
    
    if "session" in query_params:
        session_id = query_params["session"]
        return handle_attendance_session(session_id)
    
    if "student_link" in query_params:
        link_id = query_params["student_link"]
        return handle_student_attendance_link(link_id)
    
    if "reset_token" in query_params:
        st.session_state.page = "reset_password"
        return reset_password_flow(query_params["reset_token"])
    
    return None

def handle_attendance_session(session_id):
    """Handle attendance session access"""
    session = sessions_col.find_one({"session_id": session_id})
    
    if not session:
        st.error("❌ Invalid or expired attendance session")
        st.stop()
    
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
    
    display_session_attendance_form(session)
    st.stop()

def handle_student_attendance_link(link_id):
    """Handle individual student attendance link"""
    link = links_col.find_one({"link_id": link_id})
    
    if not link:
        st.error("❌ Invalid or expired attendance link")
        st.stop()
    
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
    
    if link.get("max_uses") and link.get("uses", 0) >= link["max_uses"]:
        st.error("❌ This attendance link has reached its usage limit")
        st.stop()
    
    display_student_attendance_form(link)
    st.stop()

def display_session_attendance_form(session):
    """Display attendance form for session-based attendance"""
    st.title("📝 Mark Your Attendance")
    st.success(f"✅ Session: {session.get('description', 'Class Attendance')}")
    
    if session.get('course'):
        st.info(f"📚 Course: {session['course']}")
    
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
                code_data, code_type = decode_from_camera(img)
                
                if code_data:
                    st.success(f"🔍 {code_type} detected: {code_data}")
                    student_id = code_data
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
                student = students_col.find_one({"student_id": student_id})
                if not student:
                    st.error("❌ Student ID not found in database. Please contact your teacher.")
                else:
                    if student.get("name", "").lower() != student_name.lower():
                        st.warning("⚠️ Name doesn't match our records, but attendance will be marked.")
                    
                    result = mark_attendance(
                        student_id, 
                        1,
                        datetime.now(), 
                        course=session.get("course"),
                        method="session_link"
                    )
                    
                    if "error" in result and result["error"] == "already":
                        st.warning(f"⚠️ Attendance already marked for today!")
                    else:
                        st.success(f"🎉 Attendance marked successfully for {student.get('name', student_name)}!")
                        
                        if use_mongo:
                            sessions_col.update_one(
                                {"session_id": session["session_id"]},
                                {"$inc": {"attendance_count": 1}}
                            )
                        else:
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
    
    if use_mongo:
        expires_str = link["expires_at"].strftime("%Y-%m-%d %H:%M")
    else:
        expires_dt = datetime.fromisoformat(link["expires_at"])
        expires_str = expires_dt.strftime("%Y-%m-%d %H:%M")
    
    st.warning(f"⏰ Link expires: {expires_str}")
    
    uses = link.get("uses", 0)
    max_uses = link.get("max_uses")
    if max_uses:
        st.info(f"📊 Usage: {uses}/{max_uses}")
    else:
        st.info(f"📊 Total uses: {uses}")
    
    if st.button("✅ Mark Present for Today", type="primary", use_container_width=True):
        result = mark_attendance(
            link["student_id"], 
            1,
            datetime.now(), 
            course=student.get("course"),
            method="personal_link"
        )
        
        if "error" in result and result["error"] == "already":
            st.warning(f"⚠️ Attendance already marked for today!")
        else:
            st.success(f"🎉 Attendance marked successfully!")
            
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

# -------------------- Main Application Logic --------------------
bootstrap_admin()

# Check cookie-based session
if "session" in cookies and not st.session_state.auth["logged_in"]:
    user_data = user_manager.authenticate_user(cookies["session"], None)  # Check if user exists
    if user_data[0]:
        st.session_state.auth.update({
            "logged_in": True,
            "username": user_data[1]["username"],
            "role": user_data[1]["role"],
            "name": user_data[1]["name"],
            "email": user_data[1]["email"]
        })
        st.session_state.page = "dashboard"

if handle_url_params():
    st.stop()

if not st.session_state.auth["logged_in"]:
    if st.session_state.page == "login":
        login_flow()
    elif st.session_state.page == "signup":
        signup_flow()
    elif st.session_state.page == "two_factor":
        two_factor_flow()
    elif st.session_state.page == "forgot_password":
        forgot_password_flow()
    st.stop()

# -------------------- Sidebar and Navigation --------------------
st.sidebar.title("Smart Attendance — Enhanced")
st.sidebar.write(f"👤 {st.session_state.auth.get('name', st.session_state.auth['username'])} ({st.session_state.auth['role']})")

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
], key="nav")

if st.sidebar.button("Logout", type="secondary"):
    with st.form("logout_confirm"):
        st.warning("Are you sure you want to logout?")
        if st.form_submit_button("Confirm Logout"):
            st.session_state.auth = {"logged_in": False, "username": None, "role": None, "name": None, "email": None}
            st.session_state.unlocked = {}
            st.session_state.page = "login"
            cookies["session"] = ""
            cookies.save()
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
            mem = io.BytesIO()
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
        st.subheader("Manual Entry")
        with st.form("add_student_manual"):
            sid = st.text_input("Student ID *", key="manual_student_id", help="Enter unique student ID")
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
        
        st.subheader("Add by QR/Barcode Scanner")
        st.info("💡 Use this option if you have a barcode/QR code scanner device connected to your computer.")
        with st.form("add_student_scanner"):
            st.markdown("**Instructions:**")
            st.markdown("- Click in the input field below")
            st.markdown("- Scan the student's QR code or barcode with your scanner device")
            st.markdown("- The code data will appear automatically")
            st.markdown("- Enter the student's name and course, then click 'Add Student' to save")
            
            scanner_code = st.text_input("Scan QR code or barcode here:", 
                                       placeholder="Click here and scan with your scanner", 
                                       key="scanner_input")
            if scanner_code:
                st.success(f"🔍 Code scanned: {scanner_code}")
            
            scanner_student_id = st.text_input("Student ID *", value=scanner_code if scanner_code else "", 
                                             key="scanner_student_id", help="Auto-filled from scan or enter manually")
            scanner_student_name = st.text_input("Student Name *")
            scanner_course = st.text_input("Course")
            
            if st.form_submit_button("Add Student"):
                if not scanner_student_id or not scanner_student_name:
                    st.error("Student ID and Name are required")
                elif students_col.find_one({"student_id": scanner_student_id}):
                    st.warning("⚠️ Student ID already exists")
                else:
                    try:
                        qr_path = make_qr(scanner_student_id)
                        barcode_path = make_barcode(scanner_student_id)
                        students_col.insert_one({
                            "student_id": scanner_student_id, 
                            "name": scanner_student_name, 
                            "course": scanner_course,
                            "qr_path": qr_path,
                            "barcode_path": barcode_path
                        })
                        st.success(f"✅ Student {scanner_student_name} added successfully with QR code and barcode generated")
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
    st.info("📱 Scan a student's QR code or barcode using the camera or a hardware scanner")
    
    scan_method = st.radio("Choose scanning method:", 
                          ["📷 Camera", "⌨️ Manual Barcode Scanner"])
    
    if scan_method == "📷 Camera":
        camera_image = st.camera_input("Take a photo of QR code or barcode")
        
        if camera_image is not None:
            try:
                img = Image.open(camera_image)
                st.image(img, caption="Captured Image", width=300)
                
                with st.spinner("Decoding QR code/barcode..."):
                    code_data, code_type = decode_from_camera(img)
                    
                if code_data:
                    st.success(f"🔍 {code_type} detected: {code_data}")
                    
                    student = students_col.find_one({"student_id": code_data})
                    if not student:
                        st.error("❌ Student not found in database")
                    else:
                        combined_datetime = datetime.combine(chosen_date, datetime.now().time())
                        result = mark_attendance(
                            code_data, 
                            1,
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
    
    elif scan_method == "⌨️ Manual Barcode Scanner":
        st.info("💡 Use this option if you have a barcode scanner device connected to your computer.")
        st.markdown("**Instructions:**")
        st.markdown("- Click in the input field below")
        st.markdown("- Scan the student's QR code or barcode with your scanner device")
        st.markdown("- The code data will appear automatically")
        st.markdown("- Click 'Mark Attendance' to save")
        
        with st.form("barcode_scanner"):
            scanned_code = st.text_input("Scan QR code or barcode here:", 
                                       placeholder="Click here and scan with your scanner")
            
            if st.form_submit_button("✅ Mark Attendance"):
                if not scanned_code:
                    st.error("Please scan a QR code or barcode first")
                else:
                    student = students_col.find_one({"student_id": scanned_code})
                    if not student:
                        st.error("❌ Student not found in database")
                    else:
                        combined_datetime = datetime.combine(chosen_date, datetime.now().time())
                        result = mark_attendance(
                            scanned_code, 
                            1,
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
    st.title("✍️ Manual Attendance Entry & Edit")
    
    tab1, tab2 = st.tabs(["New Entry", "Edit Attendance"])
    
    with tab1:
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
    
    with tab2:
        st.subheader("Edit Existing Attendance")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            search_date = st.date_input("Select Date", date.today())
        with col2:
            students_df = get_students_df()
            student_options = ["All"] + sorted(students_df["student_id"].tolist())
            search_student = st.selectbox("Student ID", student_options)
        with col3:
            courses = ["All"] + sorted({r.get("course","") for r in students_df.to_dict("records") if r.get("course")})
            search_course = st.selectbox("Course", courses)
        
        query = {"date": str(search_date)}
        if search_student != "All":
            query["student_id"] = search_student
        if search_course != "All":
            query["course"] = search_course
            
        attendance_records = list(att_col.find(query))
        
        if attendance_records:
            st.write(f"Found {len(attendance_records)} attendance records")
            
            for record in attendance_records:
                student = students_col.find_one({"student_id": record["student_id"]})
                student_name = student.get("name", "Unknown") if student else "Unknown"
                
                with st.expander(f"{student_name} ({record['student_id']})"):
                    col1, col2, col3 = st.columns([2,2,1])
                    
                    with col1:
                        st.write(f"**Course:** {record.get('course', 'N/A')}")
                        st.write(f"**Time:** {record.get('time', 'N/A')}")
                    with col2:
                        st.write(f"**Method:** {record.get('method', 'manual')}")
                        current_status = "Present" if record.get("status", 0) == 1 else "Absent"
                        st.write(f"**Current Status:** {current_status}")
                    with col3:
                        new_status = st.selectbox(
                            "New Status",
                            [("Present", 1), ("Absent", 0)],
                            format_func=lambda x: x[0],
                            key=f"edit_{record['student_id']}_{record['date']}"
                        )
                        
                        if st.button("Update", key=f"btn_{record['student_id']}_{record['date']}"):
                            try:
                                att_col.update_one(
                                    {
                                        "student_id": record["student_id"],
                                        "date": record["date"]
                                    },
                                    {
                                        "$set": {
                                            "status": new_status[1],
                                            "last_modified": datetime.now() if use_mongo else datetime.now().isoformat(),
                                            "modified_by": st.session_state.auth["username"]
                                        }
                                    }
                                )
                                st.success("✅ Attendance updated successfully!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error updating attendance: {e}")
        else:
            st.info("No attendance records found for the selected criteria")
            
        with st.expander("➕ Add New Record for Selected Date"):
            with st.form("add_missing_record"):
                student_id = st.selectbox(
                    "Student ID *", 
                    options=students_df["student_id"].tolist(),
                    format_func=lambda x: f"{x} - {students_df[students_df['student_id']==x]['name'].iloc[0]}"
                )
                status = st.selectbox("Status", [("Present", 1), ("Absent", 0)])
                course = st.text_input("Course Override (optional)")
                
                if st.form_submit_button("Add Record"):
                    student = students_col.find_one({"student_id": student_id})
                    if student:
                        combined_datetime = datetime.combine(search_date, datetime.now().time())
                        final_course = course if course else student.get("course")
                        
                        result = mark_attendance(
                            student_id,
                            status[1],
                            combined_datetime,
                            course=final_course,
                            method="manual_edit"
                        )
                        
                        if "error" in result and result["error"] == "already":
                            st.warning("⚠️ Attendance record already exists for this date")
                        else:
                            st.success("✅ Attendance record added successfully!")
                            st.rerun()

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
                            index=0
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
        
        if submit_button:
            try:
                session_id, expires_at = create_attendance_session(
                    course=session_course if session_course else None,
                    duration_hours=duration[1],
                    description=session_desc if session_desc else "Class Attendance"
                )
                
                base_url = st.get_option('server.baseUrlPath') or 'http://localhost:8501'
                share_url = f"{base_url}?session={session_id}"
                
                st.success("✅ Session link created successfully!")
                st.info(f"🕒 Expires: {expires_at.strftime('%Y-%m-%d %H:%M')}")
                
                st.text_area("📋 Share this link with students:", value=share_url, height=100)
                
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
                ], format_func=lambda x: x[0], index=2)
                
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
                            
                            for link_info in created_links:
                                with st.expander(f"📋 {link_info['student_name']} ({link_info['student_id']})"):
                                    st.text_area(f"Link for {link_info['student_name']}:", 
                                               value=link_info['link'], height=100)
                                    st.info(f"🕒 Expires: {link_info['expires']}")
                            
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
    
    method_filter = st.selectbox("Attendance Method Filter", [
        "All", "manual_entry", "camera_scan", "scanner_device", 
        "bulk_entry", "session_link", "personal_link"
    ])
    
    pivot_df = pivot_attendance(start_date, end_date, None if course_filter=="All" else course_filter)
    
    if pivot_df.empty:
        st.info("📭 No attendance records found for the selected date range")
    else:
        detailed_records = get_attendance_rows(start_date, end_date, None if course_filter=="All" else course_filter)
        
        if not detailed_records.empty and method_filter != "All":
            detailed_records = detailed_records[detailed_records.get("method", "manual_entry") == method_filter]
        
        st.subheader("📊 Pivot View")
        st.dataframe(pivot_df.sort_values(["course", "student_id"]), use_container_width=True)
        
        st.subheader("📝 Detailed Records")
        if not detailed_records.empty:
            students_info = get_students_df()[["student_id", "name"]].set_index("student_id").to_dict()["name"]
            detailed_records["student_name"] = detailed_records["student_id"].map(students_info)
            
            display_cols = ["date", "student_id", "student_name", "status", "time", "course", "method"]
            display_cols = [col for col in display_cols if col in detailed_records.columns]
            
            st.dataframe(detailed_records[display_cols].sort_values(["date", "student_id"]), 
                        use_container_width=True)
        else:
            st.info("No detailed records match the selected filters")
        
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
            else:
                success, message = user_manager.change_password(
                    st.session_state.auth['username'],
                    current_password,
                    new_password
                )
                if success:
                    st.success(message)
                else:
                    st.error(message)
    
    st.subheader("🔐 Two-Factor Authentication")
    with st.form("two_factor_setup"):
        enable_2fa = st.checkbox("Enable 2FA (SMS-based)")
        if st.form_submit_button("Update 2FA Settings"):
            users_col.update_one(
                {"username": st.session_state.auth['username']},
                {"$set": {"two_factor_enabled": enable_2fa}}
            )
            st.success("2FA settings updated")

    st.subheader("ℹ️ System Information")
    st.info(f"Database: {'MongoDB' if use_mongo else 'JSON Files'}")

    students_count = students_col.count_documents({})
    attendance_count = att_col.count_documents({})
    if use_mongo:
        active_sessions = sessions_col.count_documents({"is_active": True})
        active_links = links_col.count_documents({"is_active": True})
    else:
        active_sessions = len([s for s in sessions_col.find({"is_active": True}) if s.get("expires_at", "9999-12-31") > datetime.now().isoformat()])
        active_links = len([l for l in links_col.find({"is_active": True}) if l.get("expires_at", "9999-12-31") > datetime.now().isoformat()])

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("👨‍🎓 Students", students_count)
    with col2:
        st.metric("📝 Attendance Records", attendance_count)
    with col3:
        st.metric("📋 Active Sessions", active_sessions)
    with col4:
        st.metric("🔗 Active Links", active_links)

    if st.session_state.auth.get("role") == "admin":
        st.subheader("🗑️ Data Management (Admin Only)")
        st.warning("⚠️ The following actions are irreversible!")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Clear All Students & Attendance", type="secondary"):
                with st.form("confirm_clear_students"):
                    st.warning("This will delete all students and attendance records. This action cannot be undone.")
                    if st.form_submit_button("Confirm Clear Students"):
                        try:
                            students_col.delete_many({})
                            att_col.delete_many({})
                            st.success("✅ All student and attendance data cleared")
                            for folder in [QR_FOLDER, BARCODE_FOLDER]:
                                if os.path.exists(folder):
                                    shutil.rmtree(folder)
                                    os.makedirs(folder, exist_ok=True)
                        except Exception as e:
                            st.error(f"Error clearing data: {e}")

        with col2:
            if st.button("Clear All Links & Sessions", type="secondary"):
                with st.form("confirm_clear_links"):
                    st.warning("This will delete all links and sessions. This action cannot be undone.")
                    if st.form_submit_button("Confirm Clear Links"):
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
        st.stop()

    st.subheader("➕ Add New Teacher")
    with st.form("add_teacher"):
        username = st.text_input("Username *")
        password = st.text_input("Password *", type="password")
        email = st.text_input("Email *")
        name = st.text_input("Full Name")
        role = st.selectbox("Role", ["teacher", "admin"])

        if st.form_submit_button("Add Teacher"):
            success, message = user_manager.create_user(
                username=username,
                password=password,
                email=email,
                name=name,
                role=role
            )
            if success:
                # Auto-verify small convenience for admin-created users
                user_manager.verify_email(username)
                st.success(message)
            else:
                st.error(message)

    st.subheader("👥 Current Teachers")
    try:
        users = list(users_col.find({}))
        if users:
            users_display = []
            for user in users:
                created_at = user.get("created_at")
                if not use_mongo and created_at:
                    try:
                        created_at = datetime.fromisoformat(created_at)
                    except Exception:
                        created_at = created_at
                users_display.append({
                    "Username": user.get("username", ""),
                    "Name": user.get("name", ""),
                    "Email": user.get("email", ""),
                    "Role": user.get("role", "teacher"),
                    "Status": user.get("status", "active"),
                    "Last Login": user.get("last_login").strftime("%Y-%m-%d %H:%M") if user.get("last_login") and use_mongo else (user.get("last_login") or "Never"),
                    "Created": "System" if user.get("username") == "admin" else (created_at.strftime("%Y-%m-%d %H:%M") if isinstance(created_at, datetime) else created_at or "N/A")
                })

            users_df = pd.DataFrame(users_display)
            st.dataframe(users_df, use_container_width=True)

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
                                deleted_count = getattr(result, "deleted_count", None)
                                if deleted_count is None:
                                    # SimpleCol returns dict
                                    deleted_count = result.get("deleted_count", 0) if isinstance(result, dict) else 0

                                if deleted_count > 0:
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

    st.subheader("🔑 Password Reset")
    try:
        users_list = [u for u in list(users_col.find({})) if u.get("username") != "admin"]
        if users_list:
            with st.form("reset_password_admin"):
                username_to_reset = st.selectbox("Select user for password reset", [u["username"] for u in users_list])
                if st.form_submit_button("Generate Reset Link"):
                    success, token = user_manager.generate_reset_token(username_to_reset)
                    if success:
                        base_url = st.get_option('server.baseUrlPath') or 'http://localhost:8501'
                        reset_url = f"{base_url}?reset_token={token}"
                        st.success(f"✅ Reset link generated for {username_to_reset}")
                        st.text_area("Share this reset link:", value=reset_url, height=100)
                    else:
                        st.error(token)
        else:
            st.info("No users available for password reset.")
    except Exception as e:
        st.error(f"Error generating reset link: {e}")

# -------------------- Footer --------------------
st.sidebar.markdown("---")
st.sidebar.markdown("**Smart Attendance System v3.0 Enhanced**")