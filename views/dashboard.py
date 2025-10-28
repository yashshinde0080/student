import io
from datetime import date, timedelta
import pandas as pd
import streamlit as st
from helpers import get_students_df, pivot_attendance


def render(collections):
    """Render dashboard page"""
    students_col = collections['students']
    att_col = collections['attendance']
    use_mongo = collections['use_mongo']

    st.title("📊 Dashboard (Pivot View)")

    col1, col2, col3 = st.columns(3)
    with col1:
        start = st.date_input("Start Date", date.today() - timedelta(days=7))
    with col2:
        end = st.date_input("End Date", date.today())
    with col3:
        students_df = get_students_df(students_col)
        courses = ["All"] + sorted({r.get("course", "") for r in students_df.to_dict("records") if r.get("course")})
        course = st.selectbox("Course Filter", courses)

    pivot_df = pivot_attendance(students_col, att_col, use_mongo, start, end, None if course == "All" else course)

    if pivot_df.empty:
        st.info("📭 No attendance data found for the selected criteria")
    else:
        st.dataframe(pivot_df.sort_values(["course", "student_id"]), use_container_width=True)

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
