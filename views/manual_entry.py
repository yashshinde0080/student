from datetime import date, datetime
import streamlit as st
from helpers import require_reauth, mark_attendance, get_students_df, is_admin, get_user_filter


def render(collections, user_manager):
    """Render manual attendance entry and edit page"""
    require_reauth("manual", user_manager)

    students_col = collections['students']
    att_col = collections['attendance']
    use_mongo = collections['use_mongo']

    st.title("✍ Manual Attendance Entry & Edit")

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
                            att_col,
                            use_mongo,
                            student_id,
                            status[1],
                            combined_datetime,
                            course=final_course,
                            method="manual_entry"
                        )

                        if "error" in result and result["error"] == "already":
                            st.warning(f"⚠ Attendance already recorded for {student.get('name')} on {entry_date}")
                        else:
                            status_text = "PRESENT" if status[1] == 1 else "ABSENT"
                            st.success(f"✅ Marked {student.get('name')} as {status_text} for {entry_date}")

    with tab2:
        st.subheader("Edit Existing Attendance")

        col1, col2, col3 = st.columns(3)
        with col1:
            search_date = st.date_input("Select Date", date.today())
        with col2:
            students_df = get_students_df(students_col)
            if 'student_id' not in students_df.columns:
                st.error("❌ 'student_id' column is missing in the students DataFrame.")
                return
            student_options = ["All"] + sorted(students_df["student_id"].tolist())
            search_student = st.selectbox("Student ID", student_options)
        with col3:
            courses = ["All"] + sorted({r.get("course", "") for r in students_df.to_dict("records") if r.get("course")})
            search_course = st.selectbox("Course", courses)

        query = {"date": str(search_date)}
        if search_student != "All":
            query["student_id"] = search_student
        if search_course != "All":
            query["course"] = search_course
        query.update(get_user_filter())  # Add user isolation filter

        attendance_records = list(att_col.find(query))

        if attendance_records:
            st.write(f"Found {len(attendance_records)} attendance records")

            for record in attendance_records:
                student = students_col.find_one({"student_id": record["student_id"]})
                student_name = student.get("name", "Unknown") if student else "Unknown"

                with st.expander(f"{student_name} ({record['student_id']})"):
                    col1, col2, col3 = st.columns([2, 2, 1])

                    with col1:
                        st.write(f"*Course:* {record.get('course', 'N/A')}")
                        st.write(f"*Time:* {record.get('time', 'N/A')}")
                    with col2:
                        st.write(f"*Method:* {record.get('method', 'manual')}")
                        current_status = "Present" if record.get("status", 0) == 1 else "Absent"
                        st.write(f"*Current Status:* {current_status}")
                    with col3:
                        new_status = st.selectbox(
                            "New Status",
                            [("Present", 1), ("Absent", 0)],
                            format_func=lambda x: x[0],
                            key=f"edit_{record['student_id']}_{record['date']}"
                        )

                        if st.button("Update", key=f"btn_{record['student_id']}_{record['date']}"):
                            # Verify ownership before updating
                            if not is_admin() and record.get("created_by") != st.session_state.auth.get("username"):
                                st.error("❌ You don't have permission to edit this record")
                            else:
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
                            att_col,
                            use_mongo,
                            student_id,
                            status[1],
                            combined_datetime,
                            course=final_course,
                            method="manual_edit"
                        )

                        if "error" in result and result["error"] == "already":
                            st.warning("⚠ Attendance record already exists for this date")
                        else:
                            st.success("✅ Attendance record added successfully!")
                            st.rerun()