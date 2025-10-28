# Smart Attendance System - Multi-File Architecture

## Overview
Refactored Smart Attendance System with proper authentication, user data isolation, and modular structure.

## Running the Application

### New Multi-File App:
```bash
streamlit run mainapp.py
```

### Original App (full features):
```bash
streamlit run app.py
```

## Default Admin Credentials
- Username: `admin`
- Password: `Admin@123`

## Key Changes
- ✅ Removed 2FA verification
- ✅ Fixed authentication flows
- ✅ User data isolation (teachers see only their data)
- ✅ Multi-file architecture (easier maintenance)

## Project Structure
```
student/
├── mainapp.py       # Main entry point
├── auth.py          # Authentication (no 2FA)
├── database.py      # Database setup
├── helpers.py       # Helper functions
└── pages/           # Page modules
    ├── dashboard.py
    ├── students.py
    └── settings.py
```

## Features
- Teacher/Admin roles with proper isolation
- QR/Barcode attendance scanning
- Manual & bulk entry
- Shareable attendance links
- Data export (CSV/Excel)
