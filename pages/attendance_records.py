import io
from datetime import date, timedelta
import pandas as pd
import streamlit as st
from helpers import get_attendance_rows, get_students_df


def render(collections):
    """Render attendance records page with filtering and export"""
    students_col = collections['students']
    att_col = collections['attendance']
    use_mongo = collections['use_mongo']

    st.title("📊 Attendance Records")

    col1, col2, col3 = st.columns(3)

    with col1:
        start_date = st.date_input("Start Date", date.today() - timedelta(days=30))
    with col2:
        end_date = st.date_input("End Date", date.today())
    with col3:
        students_df = get_students_df(students_col)
        courses = ["All"] + sorted({r.get("course", "") for r in students_df.to_dict("records") if r.get("course")})
        selected_course = st.selectbox("Course Filter", courses)

    course_filter = None if selected_course == "All" else selected_course
    attendance_df = get_attendance_rows(att_col, use_mongo, start=start_date, end=end_date, course=course_filter)

    if attendance_df.empty:
        st.info("📭 No attendance records found for the selected criteria")
    else:
        # Merge with student names
        if not students_df.empty:
            attendance_df = attendance_df.merge(
                students_df[["student_id", "name"]],
                on="student_id",
                how="left"
            )

        # Format status
        attendance_df["status_text"] = attendance_df["status"].apply(lambda x: "Present" if x == 1 else "Absent")

        # Display records
        st.write(f"**Total Records:** {len(attendance_df)}")

        display_cols = ["student_id", "name", "date", "time", "status_text", "course", "method"]
        available_cols = [col for col in display_cols if col in attendance_df.columns]

        st.dataframe(
            attendance_df[available_cols].sort_values(["date", "student_id"], ascending=[False, True]),
            use_container_width=True
        )

        # Statistics
        st.subheader("📈 Statistics")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            total_present = (attendance_df["status"] == 1).sum()
            st.metric("Present", total_present)
        with col2:
            total_absent = (attendance_df["status"] == 0).sum()
            st.metric("Absent", total_absent)
        with col3:
            total_records = len(attendance_df)
            st.metric("Total Records", total_records)
        with col4:
            if total_records > 0:
                attendance_rate = (total_present / total_records) * 100
                st.metric("Attendance Rate", f"{attendance_rate:.1f}%")

        # Export options
        st.subheader("📥 Export Data")
        col1, col2 = st.columns(2)

        with col1:
            csv_data = attendance_df[available_cols].to_csv(index=False).encode()
            st.download_button(
                "📥 Download CSV",
                data=csv_data,
                file_name=f"attendance_{start_date}_{end_date}.csv",
                mime="text/csv"
            )

        with col2:
            mem = io.BytesIO()
            with pd.ExcelWriter(mem, engine="xlsxwriter") as writer:
                attendance_df[available_cols].to_excel(writer, index=False, sheet_name="Attendance")
            mem.seek(0)
            st.download_button(
                "📊 Download Excel",
                data=mem,
                file_name=f"attendance_{start_date}_{end_date}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
