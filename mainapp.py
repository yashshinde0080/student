import os
import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager
from datetime import datetime, date
from PIL import Image

# Import modules
from database import get_collections, migrate_existing_data_to_user_ownership, users_col, use_mongo
from auth import UserManager
from helpers import decode_from_camera, mark_attendance, get_students_df

# Import views
from views import dashboard, students, scan_qr_barcode, manual_entry, bulk_entry, share_links, attendance_records, settings, teachers

# Page configuration
st.set_page_config(page_title="Smart Attendance — Enhanced", layout="wide", initial_sidebar_state="expanded")

# Cookie Manager
cookies = EncryptedCookieManager(prefix="attendance_", password=os.getenv("COOKIE_SECRET", "supersecretkey"))
if not cookies.ready():
    st.stop()

# Get database collections
collections = get_collections()
user_manager = UserManager(collections['users'], collections['use_mongo'])

# Initialize session state
if "auth" not in st.session_state:
    st.session_state.auth = {"logged_in": False, "username": None, "role": None, "name": None, "email": None}
if "unlocked" not in st.session_state:
    st.session_state.unlocked = {}
if "page" not in st.session_state:
    st.session_state.page = "login"


# -------------------- Bootstrap Admin --------------------
def bootstrap_admin():
    """Create default admin user if none exists"""
    if collections['users'].count_documents({}) == 0:
        success, message = user_manager.create_user(
            username="admin",
            password="Admin@123",
            email="admin@example.com",
            name="Administrator",
            role="admin"
        )
        if not success:
            st.error(message)


# -------------------- Authentication Flows --------------------
def login_flow():
    """Login page"""
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
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="login-container">', unsafe_allow_html=True)
    st.markdown('<h2 class="login-title">🔐 Smart Attendance Login</h2>', unsafe_allow_html=True)

    with st.form("login_form"):
        username = st.text_input("Username", placeholder="Enter your username")
        password = st.text_input("Password", type="password", placeholder="Enter your password")

        col1, col2 = st.columns(2)
        with col1:
            if st.form_submit_button("Login"):
                user_data = user_manager.authenticate_user(username, password)
                if user_data[0]:
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

    st.markdown('</div>', unsafe_allow_html=True)


def signup_flow():
    """Signup page"""
    st.markdown('<h2>📝 Sign Up</h2>', unsafe_allow_html=True)

    with st.form("signup_form"):
        username = st.text_input("Username *", placeholder="Choose a username (min 3 characters)")
        email = st.text_input("Email *", placeholder="Enter your email")
        name = st.text_input("Full Name", placeholder="Enter your full name")
        password = st.text_input("Password *", type="password", placeholder="Create a password")
        confirm_password = st.text_input("Confirm Password *", type="password", placeholder="Confirm your password")

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


# -------------------- URL Parameter Handling --------------------
def handle_url_params():
    """Handle URL parameters for attendance links and sessions"""
    query_params = st.query_params

    if "session" in query_params:
        session_id = query_params["session"]
        return handle_attendance_session(session_id)

    if "student_link" in query_params:
        link_id = query_params["student_link"]
        return handle_student_attendance_link(link_id)

    return None


def handle_attendance_session(session_id):
    """Handle attendance session access"""
    sessions_col = collections['sessions']
    students_col = collections['students']
    session = sessions_col.find_one({"session_id": session_id})

    if not session:
        st.error("❌ Invalid or expired attendance session")
        st.stop()

    if collections['use_mongo']:
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

    display_session_attendance_form(session, students_col)
    st.stop()


def display_session_attendance_form(session, students_col):
    """Display attendance form for session-based attendance"""
    st.title("📝 Mark Your Attendance")
    st.success(f"✅ Session: {session.get('description', 'Class Attendance')}")

    with st.form("session_attendance"):
        student_id = st.text_input("🆔 Enter your Student ID", placeholder="e.g., STU001")
        student_name = st.text_input("👤 Enter your Name", placeholder="Your full name")

        camera_image = st.camera_input("Take a photo of your QR code or barcode")

        if camera_image is not None:
            try:
                img = Image.open(camera_image)
                code_data, code_type = decode_from_camera(img)

                if code_data:
                    st.success(f"🔍 {code_type} detected: {code_data}")
                    student_id = code_data
                else:
                    st.warning("❌ No QR code or barcode detected.")
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
                        collections['attendance'],
                        collections['use_mongo'],
                        student_id,
                        1,
                        datetime.now(),
                        course=session.get("course"),
                        method="session_link",
                        created_by_override=session.get("created_by")
                    )

                    if "error" in result and result["error"] == "already":
                        st.warning(f"⚠️ Attendance already marked for today!")
                    else:
                        st.success(f"🎉 Attendance marked successfully!")
                        if collections['use_mongo']:
                            collections['sessions'].update_one(
                                {"session_id": session["session_id"]},
                                {"$inc": {"attendance_count": 1}}
                            )


def handle_student_attendance_link(link_id):
    """Handle individual student attendance link"""
    links_col = collections['links']
    students_col = collections['students']
    link = links_col.find_one({"link_id": link_id})

    if not link:
        st.error("❌ Invalid or expired attendance link")
        st.stop()

    student = students_col.find_one({"student_id": link["student_id"]})

    if not student:
        st.error("❌ Student not found")
        st.stop()

    st.title("📝 Mark Your Attendance")
    st.success(f"👤 Welcome, {student.get('name', link['student_id'])}!")

    if st.button("✅ Mark Present for Today", type="primary", use_container_width=True):
        result = mark_attendance(
            collections['attendance'],
            collections['use_mongo'],
            link["student_id"],
            1,
            datetime.now(),
            course=student.get("course"),
            method="personal_link",
            created_by_override=link.get("created_by")
        )

        if "error" in result and result["error"] == "already":
            st.warning(f"⚠️ Attendance already marked for today!")
        else:
            st.success(f"🎉 Attendance marked successfully!")
            if collections['use_mongo']:
                links_col.update_one(
                    {"link_id": link["link_id"]},
                    {"$inc": {"uses": 1}}
                )
            st.balloons()

    st.stop()


# -------------------- Main Application Logic --------------------
bootstrap_admin()
migrate_existing_data_to_user_ownership()

# Check cookie-based session
if "session" in cookies and not st.session_state.auth["logged_in"]:
    user_data = user_manager.authenticate_user(cookies["session"], None)
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
    st.session_state.auth = {"logged_in": False, "username": None, "role": None, "name": None, "email": None}
    st.session_state.unlocked = {}
    st.session_state.page = "login"
    cookies["session"] = ""
    cookies.save()
    st.rerun()

# -------------------- Render Pages --------------------
if nav == "Dashboard":
    dashboard.render(collections)
elif nav == "Students":
    students.render(collections)
elif nav == "Scan QR/Barcode":
    scan_qr_barcode.render(collections)
elif nav == "Manual Entry":
    manual_entry.render(collections, user_manager)
elif nav == "Bulk Entry":
    bulk_entry.render(collections, user_manager)
elif nav == "Share Links":
    share_links.render(collections, user_manager)
elif nav == "Attendance Records":
    attendance_records.render(collections)
elif nav == "Settings":
    settings.render(collections, user_manager)
elif nav == "Teachers":
    teachers.render(collections, user_manager)

# Footer
st.sidebar.markdown("---")
st.sidebar.markdown("**Smart Attendance System v4.0**")
