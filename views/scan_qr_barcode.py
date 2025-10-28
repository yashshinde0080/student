from datetime import date, datetime
import streamlit as st
from PIL import Image
from helpers import decode_from_camera, mark_attendance


def render(collections):
    """Render QR/Barcode scanning page"""
    students_col = collections['students']
    att_col = collections['attendance']
    use_mongo = collections['use_mongo']

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
                            att_col,
                            use_mongo,
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
                            att_col,
                            use_mongo,
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
