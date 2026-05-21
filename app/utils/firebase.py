import firebase_admin
from firebase_admin import credentials, auth
from typing import Optional, Dict
import os
import json
from app.config import settings

# Initialize Firebase App
firebase_app = None

def init_firebase():
    global firebase_app
    if firebase_app is not None:
        return

    # Priority 1: raw JSON string in env var (Cloud Run production)
    json_str = settings.FIREBASE_SERVICE_ACCOUNT_JSON
    if json_str:
        try:
            service_account_info = json.loads(json_str)
            if not firebase_admin._apps:
                cred = credentials.Certificate(service_account_info)
                firebase_admin.initialize_app(cred)
            firebase_app = firebase_admin.get_app()
            print("Firebase initialized with FIREBASE_SERVICE_ACCOUNT_JSON env var.")
            return
        except Exception as e:
            print(f"WARNING: Firebase init from JSON env var failed: {e}")

    # Priority 2: service account file path (local dev)
    path = settings.FIREBASE_SERVICE_ACCOUNT_PATH
    if path and os.path.exists(path):
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(path)
                firebase_admin.initialize_app(cred)
            firebase_app = firebase_admin.get_app()
            print("Firebase initialized with service account file.")
            return
        except Exception as e:
            print(f"WARNING: Firebase init from file failed: {e}")

    # Priority 3: Application Default Credentials (fallback)
    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        firebase_app = firebase_admin.get_app()
        print("Firebase initialized with Application Default Credentials (ADC).")
    except Exception as e:
        print(f"WARNING: Firebase init failed (all methods): {e}")

def verify_firebase_token(id_token: str) -> Optional[Dict]:
    """
    Verify the ID token from Firebase.
    Returns the decoded token (including uid and phone_number) or None.
    """
    try:
        # If firebase not initialized, we can't verify
        if firebase_app is None:
            init_firebase()
            if firebase_app is None:
                return None
        
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        print(f"Error verifying Firebase token: {e}")
        return None
