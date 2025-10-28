from datetime import date, datetime
import streamlit as st
from helpers import require_reauth, mark_attendance, get_user_filter


def render(collections, user_manager):
    """Render bulk attendance entry page"""
    require_reauth("bulk", user_manager)

    students_col = collections['students']
    att_col = collections['attendance']
    use_mongo = collections['use_mongo']

    st.title("📑 Bulk Attendance Entry")

    selected_date = st.date_input("Select Date for Bulk Entry", value=date.today())

    students = list(students_col.find(get_user_filter()))
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

            for course_name, course_students in sorted(students_by_course.items()):
                st.subheader(f"📚 {course_name}")

                col_count = 3
                cols = st.columns(col_count)

                for idx, student in enumerate(course_students):
                    col = cols[idx % col_count]
                    with col:
                        status = st.checkbox(
                            f"{student['name']} ({student['student_id']})",
                            value=True,
                            key=f"bulk_{student['student_id']}"
                        )
                        records.append({
                            "student_id": student["student_id"],
                            "name": student["name"],
                            "course": student.get("course"),
                            "status": 1 if status else 0
                        })

            submit_col1, submit_col2 = st.columns([3, 1])
            with submit_col2:
                submitted = st.form_submit_button("✅ Submit All", type="primary", use_container_width=True)

            if submitted:
                success_count = 0
                already_marked = 0
                errors = 0

                progress_bar = st.progress(0)
                status_text = st.empty()

                for idx, record in enumerate(records):
                    status_text.text(f"Processing {record['name']} ({idx + 1}/{len(records)})...")

                    combined_datetime = datetime.combine(selected_date, datetime.now().time())

                    result = mark_attendance(
                        att_col,
                        use_mongo,
                        record["student_id"],
                        record["status"],
                        combined_datetime,
                        course=record["course"],
                        method="bulk_entry"
                    )

                    if "error" in result and result["error"] == "already":
                        already_marked += 1
                    elif "ok" in result:
                        success_count += 1
                    else:
                        errors += 1

                    progress_bar.progress((idx + 1) / len(records))

                status_text.empty()
                progress_bar.empty()

                st.success(f"✅ Bulk entry complete!")
                st.write(f"- Successfully marked: {success_count}")
                st.write(f"- Already marked: {already_marked}")
                if errors > 0:
                    st.write(f"- Errors: {errors}")

                st.balloons()
