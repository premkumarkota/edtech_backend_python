import firebase_admin
from firebase_admin import credentials, auth
from typing import Optional, Dict
import os
from app.config import settings

# Initialize Firebase App
firebase_app = None

def init_firebase():
    global firebase_app
    if firebase_app is not None:
        return
        
    path = settings.FIREBASE_SERVICE_ACCOUNT_PATH
    if path and os.path.exists(path):
        cred = credentials.Certificate(path)
        firebase_app = firebase_admin.initialize_app(cred)
        print("Firebase initialized with service account.")
    else:
        print("WARNING: Firebase service account not found. Firebase features will be disabled.")

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
