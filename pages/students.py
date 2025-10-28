import os
import io
import zipfile
import pandas as pd
import streamlit as st
from helpers import get_students_df, make_qr, make_barcode


def render(collections):
    """Render students management page"""
    students_col = collections['students']
    
    st.title("👨‍🎓 Manage Students")
    
    with st.expander("➕ Add New Student"):
        st.subheader("Manual Entry")
        with st.form("add_student_manual"):
            sid = st.text_input("Student ID *")
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
                            "barcode_path": barcode_path,
                            "created_by": st.session_state.auth.get("username")
                        })
                        st.success(f"✅ Student {name} added successfully")
                    except Exception as e:
                        st.error(f"Error adding student: {e}")
    
    df_students = get_students_df(students_col)
    if not df_students.empty:
        st.subheader("📋 Current Students")
        st.dataframe(df_students.sort_values(["course", "student_id"]), use_container_width=True)
    else:
        st.info("📭 No students found. Add some students first.")
