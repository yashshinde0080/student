import streamlit as st
from helpers import require_reauth, get_user_filter


def render(collections, user_manager):
    """Render settings page"""
    require_reauth("settings", user_manager)
    
    st.title("⚙️ Settings")
    
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
    
    st.subheader("ℹ️ System Information")
    st.info(f"Database: {'MongoDB' if collections['use_mongo'] else 'JSON Files'}")
    
    user_filter = get_user_filter()
    students_count = collections['students'].count_documents(user_filter)
    attendance_count = collections['attendance'].count_documents(user_filter)
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("👨‍🎓 Students", students_count)
    with col2:
        st.metric("📝 Attendance Records", attendance_count)
