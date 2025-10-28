import os
import json
from datetime import datetime
from pymongo import MongoClient, errors as mongo_errors

# -------------------- MongoDB Configuration --------------------
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_DB", "smart_attendance_enhanced")

# -------------------- Database Setup --------------------
use_mongo = True
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    client.server_info()
    db = client[DB_NAME]
    users_col = db["users"]
    students_col = db["students"]
    att_col = db["attendance"]
    sessions_col = db["attendance_sessions"]
    links_col = db["attendance_links"]

    # Create indexes
    users_col.create_index("username", unique=True)
    users_col.create_index("email", unique=True, sparse=True)
    users_col.create_index("password_reset_token")
    students_col.create_index("student_id", unique=True)
    students_col.create_index("created_by")  # Index for user isolation
    att_col.create_index([("student_id", 1), ("date", 1)], unique=True)
    att_col.create_index("created_by")  # Index for user isolation
    sessions_col.create_index("session_id", unique=True)
    sessions_col.create_index("expires_at", expireAfterSeconds=0)
    links_col.create_index("link_id", unique=True)
    links_col.create_index("expires_at", expireAfterSeconds=0)

except Exception as e:
    use_mongo = False
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_dir, exist_ok=True)
    USERS_FILE = os.path.join(data_dir, "users.json")
    STUDENTS_FILE = os.path.join(data_dir, "students.json")
    ATT_FILE = os.path.join(data_dir, "attendance.json")
    SESSIONS_FILE = os.path.join(data_dir, "sessions.json")
    LINKS_FILE = os.path.join(data_dir, "links.json")

    for f in (USERS_FILE, STUDENTS_FILE, ATT_FILE, SESSIONS_FILE, LINKS_FILE):
        if not os.path.exists(f):
            with open(f, "w") as fh:
                json.dump([], fh)

    class SimpleCol:
        def __init__(self, path):
            self.path = path

        def _load(self):
            try:
                with open(self.path, "r") as fh:
                    data = json.load(fh)
                if self.path.endswith(("sessions.json", "links.json")):
                    now = datetime.now().isoformat()
                    data = [d for d in data if d.get("expires_at", "9999-12-31") > now]
                    self._save(data)
                return data
            except (FileNotFoundError, json.JSONDecodeError):
                return []

        def _save(self, data):
            with open(self.path, "w") as fh:
                json.dump(data, fh, default=str, indent=2)

        def find_one(self, filt):
            data = self._load()
            for d in data:
                ok = True
                for k, v in (filt or {}).items():
                    if d.get(k) != v:
                        ok = False
                        break
                if ok:
                    return d
            return None

        def find(self, filt=None):
            data = self._load()
            if not filt:
                return data
            out = []
            for d in data:
                ok = True
                for k, v in filt.items():
                    if d.get(k) != v:
                        ok = False
                        break
                if ok:
                    out.append(d)
            return out

        def insert_one(self, doc):
            data = self._load()
            data.append(doc)
            self._save(data)
            return {"inserted_id": len(data)}

        def update_one(self, filt, update, upsert=False):
            data = self._load()
            found = False
            for i, d in enumerate(data):
                ok = True
                for k, v in filt.items():
                    if d.get(k) != v:
                        ok = False
                        break
                if ok:
                    if "$set" in update:
                        for kk, vv in update["$set"].items():
                            d[kk] = vv
                    data[i] = d
                    found = True
                    break
            if not found and upsert:
                new = dict(filt)
                if "$set" in update:
                    new.update(update["$set"])
                data.append(new)
            self._save(data)

        def update_many(self, filt, update):
            data = self._load()
            modified_count = 0
            for i, d in enumerate(data):
                ok = True
                for k, v in filt.items():
                    # Handle MongoDB operators like $exists
                    if isinstance(v, dict) and "$exists" in v:
                        if v["$exists"] and k not in d:
                            ok = False
                        elif not v["$exists"] and k in d:
                            ok = False
                    elif d.get(k) != v:
                        ok = False
                        break
                if ok:
                    if "$set" in update:
                        for kk, vv in update["$set"].items():
                            d[kk] = vv
                    data[i] = d
                    modified_count += 1
            self._save(data)
            return type('obj', (object,), {'modified_count': modified_count})()

        def delete_many(self, filt):
            data = self._load()
            out = []
            removed = 0
            for d in data:
                match = True
                for k, v in (filt or {}).items():
                    if d.get(k) != v:
                        match = False
                        break
                if not match:
                    out.append(d)
                else:
                    removed += 1
            self._save(out)
            return {"deleted_count": removed}

        def count_documents(self, filt=None):
            return len(self.find(filt))

    users_col = SimpleCol(USERS_FILE)
    students_col = SimpleCol(STUDENTS_FILE)
    att_col = SimpleCol(ATT_FILE)
    sessions_col = SimpleCol(SESSIONS_FILE)
    links_col = SimpleCol(LINKS_FILE)


# -------------------- Data Migration for User Isolation --------------------
def migrate_existing_data_to_user_ownership():
    """One-time migration to add created_by field to existing records"""
    try:
        # Find a default user to assign existing data to
        admin_user = users_col.find_one({"role": "admin"})
        if admin_user:
            default_user = admin_user["username"]
        else:
            first_user = users_col.find_one({})
            if not first_user:
                print("Migration skipped: No users found in database")
                return
            default_user = first_user["username"]

        print(f"Running data migration: assigning unowned data to '{default_user}'")

        if use_mongo:
            # MongoDB mode: use update_many with $exists operator
            students_updated = students_col.update_many(
                {"created_by": {"$exists": False}},
                {"$set": {"created_by": default_user}}
            )
            att_updated = att_col.update_many(
                {"created_by": {"$exists": False}},
                {"$set": {"created_by": default_user}}
            )
            sessions_updated = sessions_col.update_many(
                {"created_by": {"$exists": False}},
                {"$set": {"created_by": default_user}}
            )
            links_updated = links_col.update_many(
                {"created_by": {"$exists": False}},
                {"$set": {"created_by": default_user}}
            )

            print(f"Migration completed: {students_updated.modified_count} students, "
                  f"{att_updated.modified_count} attendance records, "
                  f"{sessions_updated.modified_count} sessions, "
                  f"{links_updated.modified_count} links updated")
        else:
            # JSON mode: iterate and update documents manually
            students_count = 0
            students_data = students_col._load()
            for doc in students_data:
                if "created_by" not in doc:
                    doc["created_by"] = default_user
                    students_count += 1
            if students_count > 0:
                students_col._save(students_data)

            att_count = 0
            att_data = att_col._load()
            for doc in att_data:
                if "created_by" not in doc:
                    doc["created_by"] = default_user
                    att_count += 1
            if att_count > 0:
                att_col._save(att_data)

            sessions_count = 0
            sessions_data = sessions_col._load()
            for doc in sessions_data:
                if "created_by" not in doc:
                    doc["created_by"] = default_user
                    sessions_count += 1
            if sessions_count > 0:
                sessions_col._save(sessions_data)

            links_count = 0
            links_data = links_col._load()
            for doc in links_data:
                if "created_by" not in doc:
                    doc["created_by"] = default_user
                    links_count += 1
            if links_count > 0:
                links_col._save(links_data)

            print(f"Migration completed: {students_count} students, {att_count} attendance records, "
                  f"{sessions_count} sessions, {links_count} links updated")

    except Exception as e:
        print(f"Migration error (non-critical): {e}")


def get_collections():
    """Return all database collections"""
    return {
        'users': users_col,
        'students': students_col,
        'attendance': att_col,
        'sessions': sessions_col,
        'links': links_col,
        'use_mongo': use_mongo
    }
