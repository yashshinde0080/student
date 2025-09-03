
# Smart Attendance (Mini Project)

A lightweight attendance system using **Streamlit + MongoDB + QR codes + OpenCV**.

## Features
- Add students and auto-generate QR codes.
- Scan QR via webcam (Streamlit) to mark attendance.
- View dashboard & records, export CSV.
- MongoDB for persistence.
- Optional web-based scanner using `html5-qrcode` (client-only demo).

## Tech Stack
Python, Streamlit, NumPy, Pandas, OpenCV, qrcode, MongoDB, (JS for optional scanner).

## Setup
1. **Clone or unzip** this project.
2. Create a virtual environment and install requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Configure MongoDB connection:
   - Copy `.env.example` to `.env` and update values if needed.
   - Default assumes local MongoDB at `mongodb://localhost:27017`.
4. (Optional) Seed students from CSV and generate QR codes:
   ```bash
   python generate_qr_codes.py
   ```
5. **Run Streamlit app:**
   ```bash
   streamlit run streamlit_app.py
   ```

## Usage
- **Students → Add Student** to create student + QR.
- **Scan QR** page → use your webcam to scan and mark attendance.
- **Attendance Records** → filter by date/course, export CSV.
- **Dashboard** → quick stats & trend line.

## Notes
- For higher security, encode JSON payload in QR (e.g., `{student_id, nonce}`) and validate.
- The provided `scanner.html` demonstrates `html5-qrcode` scanning in browser; to auto-mark attendance, attach it to a backend API route.
