import re
import string
import secrets
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash


def generate_secure_token(length=32):
    """Generate a secure random token"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


class UserManager:
    def __init__(self, users_collection, use_mongo=True):
        self.users_col = users_collection
        self.use_mongo = use_mongo
        self.PASSWORD_MIN_LENGTH = 8
        self.PASSWORD_REGEX = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
        self.MAX_LOGIN_ATTEMPTS = 5
        self.LOCKOUT_DURATION = timedelta(minutes=30)

    def validate_email(self, email):
        """Validate email format"""
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(email_regex, email))

    def validate_password(self, password):
        """Validate password strength"""
        if not password:
            return False, "Password cannot be empty"
        if len(password) < self.PASSWORD_MIN_LENGTH:
            return False, f"Password must be at least {self.PASSWORD_MIN_LENGTH} characters"
        if not re.match(self.PASSWORD_REGEX, password):
            return False, "Password must contain at least one uppercase letter, one lowercase letter, one number, and one special character"
        return True, ""

    def create_user(self, username, password, email, name, role="teacher"):
        """Create a new user with validated credentials"""
        if not username or len(username) < 3:
            return False, "Username must be at least 3 characters"
        if not self.validate_email(email):
            return False, "Invalid email format"
        if self.users_col.find_one({"username": username}):
            return False, "Username already exists"
        if self.users_col.find_one({"email": email}):
            return False, "Email already exists"

        is_valid, error = self.validate_password(password)
        if not is_valid:
            return False, error

        try:
            user_data = {
                "username": username,
                "password": generate_password_hash(password, method='pbkdf2:sha256:600000'),
                "email": email,
                "name": name,
                "role": role,
                "created_at": datetime.now() if self.use_mongo else datetime.now().isoformat(),
                "last_login": None,
                "failed_attempts": 0,
                "is_locked": False,
                "lockout_until": None,
                "status": "active"  # Auto-activate (no email verification)
            }
            self.users_col.insert_one(user_data)
            return True, "User created successfully."
        except Exception as e:
            return False, f"Error creating user: {str(e)}"

    def authenticate_user(self, username, password):
        """Authenticate user with rate limiting and lockout"""
        user = self.users_col.find_one({"username": username})
        if not user:
            return False, "User not found"

        if user.get("is_locked", False):
            lockout_until = user.get("lockout_until")
            if lockout_until and (self.use_mongo and lockout_until > datetime.now() or
                                not self.use_mongo and lockout_until > datetime.now().isoformat()):
                return False, f"Account locked until {lockout_until}"
            else:
                self.users_col.update_one(
                    {"username": username},
                    {"$set": {"is_locked": False, "failed_attempts": 0, "lockout_until": None}}
                )

        if user.get("status") != "active":
            return False, "Account is inactive"

        # If password is None, it's a session check (cookie-based)
        if password is None:
            return True, {
                "username": username,
                "role": user.get("role"),
                "name": user.get("name"),
                "email": user.get("email")
            }

        if check_password_hash(user.get("password"), password):
            self.users_col.update_one(
                {"username": username},
                {
                    "$set": {
                        "last_login": datetime.now() if self.use_mongo else datetime.now().isoformat(),
                        "failed_attempts": 0,
                        "lockout_until": None
                    }
                }
            )
            return True, {
                "username": username,
                "role": user.get("role"),
                "name": user.get("name"),
                "email": user.get("email")
            }
        else:
            failed_attempts = user.get("failed_attempts", 0) + 1
            is_locked = failed_attempts >= self.MAX_LOGIN_ATTEMPTS
            lockout_until = datetime.now() + self.LOCKOUT_DURATION if is_locked else None

            self.users_col.update_one(
                {"username": username},
                {
                    "$set": {
                        "failed_attempts": failed_attempts,
                        "is_locked": is_locked,
                        "lockout_until": lockout_until
                    }
                }
            )
            return False, "Invalid password" if not is_locked else f"Account locked until {lockout_until}"

    def change_password(self, username, current_password, new_password):
        """Change user password with validation"""
        auth_success, _ = self.authenticate_user(username, current_password)
        if not auth_success:
            return False, "Current password is incorrect"

        is_valid, error = self.validate_password(new_password)
        if not is_valid:
            return False, error

        try:
            self.users_col.update_one(
                {"username": username},
                {
                    "$set": {
                        "password": generate_password_hash(new_password, method='pbkdf2:sha256:600000'),
                        "last_modified": datetime.now() if self.use_mongo else datetime.now().isoformat()
                    }
                }
            )
            return True, "Password updated successfully"
        except Exception as e:
            return False, f"Error updating password: {str(e)}"

    def generate_reset_token(self, username):
        """Generate a password reset token"""
        try:
            token = generate_secure_token()
            expires = datetime.now() + timedelta(hours=24)
            self.users_col.update_one(
                {"username": username},
                {
                    "$set": {
                        "password_reset_token": token,
                        "password_reset_expires": expires if self.use_mongo else expires.isoformat()
                    }
                }
            )
            return True, token
        except Exception as e:
            return False, f"Error generating reset token: {str(e)}"

    def reset_password(self, token, new_password):
        """Reset password using a token"""
        is_valid, error = self.validate_password(new_password)
        if not is_valid:
            return False, error

        query = {
            "password_reset_token": token,
            "password_reset_expires": {"$gt": datetime.now()} if self.use_mongo else {"$gt": datetime.now().isoformat()}
        }
        user = self.users_col.find_one(query)

        if not user:
            return False, "Invalid or expired reset token"

        try:
            self.users_col.update_one(
                {"password_reset_token": token},
                {
                    "$set": {
                        "password": generate_password_hash(new_password, method='pbkdf2:sha256:600000'),
                        "password_reset_token": None,
                        "password_reset_expires": None,
                        "last_modified": datetime.now() if self.use_mongo else datetime.now().isoformat()
                    }
                }
            )
            return True, "Password reset successfully"
        except Exception as e:
            return False, f"Error resetting password: {str(e)}"
