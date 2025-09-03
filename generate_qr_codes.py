
import os
import csv
import qrcode
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_DB", "smart_attendance")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
students_col = db["students"]

QR_FOLDER = os.path.join(os.path.dirname(__file__), "qrcodes")
os.makedirs(QR_FOLDER, exist_ok=True)

def make_qr(content: str, filename: str) -> str:
    img = qrcode.make(content)
    path = os.path.join(QR_FOLDER, filename)
    img.save(path)
    return path

def bulk_from_csv(csv_path: str):
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row["student_id"].strip()
            name = row.get("name","").strip()
            course = row.get("course","").strip()
            if not students_col.find_one({"student_id": sid}):
                qr_path = make_qr(sid, f"{sid}.png")
                students_col.insert_one({
                    "student_id": sid,
                    "name": name,
                    "course": course,
                    "qr_path": qr_path
                })
                print("Inserted:", sid, name, course)
            else:
                print("Exists:", sid)

if __name__ == "__main__":
    sample_csv = os.path.join(os.path.dirname(__file__), "assets", "students_sample.csv")
    if os.path.exists(sample_csv):
        bulk_from_csv(sample_csv)
        print("Done.")
    else:
        print("Provide CSV with columns: student_id,name,course")
