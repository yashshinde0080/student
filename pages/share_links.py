from datetime import datetime
import pandas as pd
import streamlit as st
from helpers import require_reauth, create_attendance_session, create_student_attendance_link, get_students_df, get_user_filter


def render(collections, user_manager):
    """Render share links page for attendance sessions and student links"""
    require_reauth("links", user_manager)

    sessions_col = collections['sessions']
    links_col = collections['links']
    students_col = collections['students']
    use_mongo = collections['use_mongo']

    st.title("🔗 Share Attendance Links")

    tab1, tab2, tab3 = st.tabs(["Create Session Link", "Create Student Link", "Manage Links"])

    with tab1:
        st.subheader("📋 Create Attendance Session")
        st.info("💡 Create a session link that allows multiple students to mark their attendance")

        with st.form("create_session"):
            description = st.text_input("Session Description *", placeholder="e.g., Monday Morning Lecture")
            course = st.text_input("Course", placeholder="e.g., Math 101")
            duration = st.number_input("Link Duration (hours)", min_value=1, max_value=168, value=24)

            if st.form_submit_button("🎯 Create Session Link"):
                if not description:
                    st.error("Session description is required")
                else:
                    session_id, expires_at = create_attendance_session(
                        sessions_col,
                        use_mongo,
                        course=course,
                        duration_hours=duration,
                        description=description
                    )

                    base_url = "http://localhost:8501"  # Update with your actual URL
                    link = f"{base_url}?session={session_id}"

                    st.success("✅ Session link created successfully!")
                    st.code(link, language="text")

                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**Expires:** {expires_at.strftime('%Y-%m-%d %H:%M') if use_mongo else datetime.fromisoformat(expires_at).strftime('%Y-%m-%d %H:%M')}")
                    with col2:
                        st.write(f"**Session ID:** {session_id[:12]}...")

                    st.info("📤 Share this link with students to mark attendance")

    with tab2:
        st.subheader("👤 Create Personal Student Link")
        st.info("💡 Create a personal link for a specific student to mark their own attendance")

        students_df = get_students_df(students_col)

        if students_df.empty:
            st.warning("📭 No students found. Please add students first.")
        else:
            with st.form("create_student_link"):
                student_id = st.selectbox(
                    "Select Student *",
                    options=students_df["student_id"].tolist(),
                    format_func=lambda x: f"{x} - {students_df[students_df['student_id']==x]['name'].iloc[0]}"
                )
                duration = st.number_input("Link Duration (hours)", min_value=1, max_value=720, value=168)
                max_uses = st.number_input("Max Uses (0 = unlimited)", min_value=0, value=0)

                if st.form_submit_button("🎯 Create Student Link"):
                    link_id, expires_at = create_student_attendance_link(
                        links_col,
                        use_mongo,
                        student_id=student_id,
                        duration_hours=duration
                    )

                    if max_uses > 0:
                        links_col.update_one(
                            {"link_id": link_id},
                            {"$set": {"max_uses": max_uses}}
                        )

                    base_url = "http://localhost:8501"  # Update with your actual URL
                    link = f"{base_url}?student_link={link_id}"

                    student = students_col.find_one({"student_id": student_id})
                    st.success(f"✅ Personal link created for {student.get('name', student_id)}")
                    st.code(link, language="text")

                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**Expires:** {expires_at.strftime('%Y-%m-%d %H:%M') if use_mongo else datetime.fromisoformat(expires_at).strftime('%Y-%m-%d %H:%M')}")
                    with col2:
                        if max_uses > 0:
                            st.write(f"**Max Uses:** {max_uses}")
                        else:
                            st.write("**Max Uses:** Unlimited")

                    st.info("📤 Share this link with the student")

    with tab3:
        st.subheader("📊 Link Management")

        st.markdown("### 📋 Active Sessions")
        try:
            if use_mongo:
                session_query = {"is_active": True, "expires_at": {"$gt": datetime.now()}}
                session_query.update(get_user_filter())
                sessions = list(sessions_col.find(session_query))
            else:
                user_filter = get_user_filter()
                base_filter = {"is_active": True, **user_filter}
                all_sessions = sessions_col.find(base_filter)
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
                links_query = {"is_active": True, "expires_at": {"$gt": datetime.now()}}
                links_query.update(get_user_filter())
                links = list(links_col.find(links_query))
            else:
                user_filter = get_user_filter()
                base_filter = {"is_active": True, **user_filter}
                all_links = links_col.find(base_filter)
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
            st.error(f"Error loading links: {e}")
