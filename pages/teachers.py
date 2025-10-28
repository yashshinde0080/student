import pandas as pd
import streamlit as st
from helpers import require_reauth


def render(collections, user_manager):
    """Render teachers management page (admin only)"""
    if st.session_state.auth.get("role") != "admin":
        st.error("⛔ This page is only accessible to administrators")
        st.stop()

    require_reauth("teachers", user_manager)

    users_col = collections['users']
    use_mongo = collections['use_mongo']

    st.title("👨‍🏫 Manage Teachers")

    tab1, tab2 = st.tabs(["View Teachers", "Add Teacher"])

    with tab1:
        st.subheader("👥 All Users")

        users = list(users_col.find({}))
        if not users:
            st.info("No users found")
        else:
            users_data = []
            for user in users:
                users_data.append({
                    "Username": user.get("username"),
                    "Name": user.get("name"),
                    "Email": user.get("email"),
                    "Role": user.get("role"),
                    "Status": user.get("status", "active"),
                    "Failed Attempts": user.get("failed_attempts", 0),
                    "Locked": "Yes" if user.get("is_locked", False) else "No"
                })

            df = pd.DataFrame(users_data)
            st.dataframe(df, use_container_width=True)

            st.subheader("🔧 User Actions")

            selected_username = st.selectbox(
                "Select User",
                options=[u.get("username") for u in users],
                format_func=lambda x: f"{x} - {next((u['role'] for u in users if u['username']==x), 'N/A')}"
            )

            if selected_username:
                user = users_col.find_one({"username": selected_username})

                col1, col2, col3 = st.columns(3)

                with col1:
                    if st.button("🔓 Unlock Account", use_container_width=True):
                        users_col.update_one(
                            {"username": selected_username},
                            {"$set": {"is_locked": False, "failed_attempts": 0, "lockout_until": None}}
                        )
                        st.success(f"✅ Account {selected_username} unlocked")
                        st.rerun()

                with col2:
                    new_role = st.selectbox("Change Role", ["teacher", "admin"])
                    if st.button("🔄 Update Role", use_container_width=True):
                        users_col.update_one(
                            {"username": selected_username},
                            {"$set": {"role": new_role}}
                        )
                        st.success(f"✅ Role updated to {new_role}")
                        st.rerun()

                with col3:
                    if user.get("status") == "active":
                        if st.button("🚫 Deactivate", use_container_width=True):
                            users_col.update_one(
                                {"username": selected_username},
                                {"$set": {"status": "inactive"}}
                            )
                            st.success(f"✅ User {selected_username} deactivated")
                            st.rerun()
                    else:
                        if st.button("✅ Activate", use_container_width=True):
                            users_col.update_one(
                                {"username": selected_username},
                                {"$set": {"status": "active"}}
                            )
                            st.success(f"✅ User {selected_username} activated")
                            st.rerun()

                st.subheader("⚠️ Danger Zone")
                with st.expander("🗑️ Delete User"):
                    st.warning("⚠️ This action cannot be undone!")
                    confirm = st.text_input(
                        f"Type '{selected_username}' to confirm deletion",
                        key="delete_confirm"
                    )
                    if st.button("Delete User", type="primary"):
                        if confirm == selected_username:
                            if selected_username == "admin":
                                st.error("❌ Cannot delete admin user")
                            else:
                                users_col.delete_many({"username": selected_username})
                                st.success(f"✅ User {selected_username} deleted")
                                st.rerun()
                        else:
                            st.error("❌ Username confirmation doesn't match")

    with tab2:
        st.subheader("➕ Add New Teacher")

        with st.form("add_teacher"):
            username = st.text_input("Username *", placeholder="Min 3 characters")
            name = st.text_input("Full Name *")
            email = st.text_input("Email *")
            password = st.text_input("Password *", type="password")
            role = st.selectbox("Role", ["teacher", "admin"])

            if st.form_submit_button("Add Teacher"):
                if not username or not password or not email:
                    st.error("Username, email, and password are required")
                else:
                    success, message = user_manager.create_user(
                        username=username,
                        password=password,
                        email=email,
                        name=name,
                        role=role
                    )

                    if success:
                        st.success(f"✅ Teacher {username} created successfully!")
                        st.info(f"Username: {username}")
                        st.info(f"Role: {role}")
                    else:
                        st.error(message)
