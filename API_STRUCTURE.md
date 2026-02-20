
# API Documentation & Folder Structure

This project uses a **Feature-First** structure, meaning everything is separated by "who uses it" (Student vs Teacher vs Admin).

## 📂 Folder Structure

```
app/
├── main.py                  # Entry point (URLs are defined here)
├── models/
│   └── user.py              # The Database Table (One table for everyone)
├── schemas/                 # Data shapes (What fields to send/receive)
│   ├── student.py           # Student request/response bodies
│   ├── teacher.py           # Teacher request/response bodies
│   └── admin.py             # Admin stuff
├── routers/                 # The actual API logic (URLs)
│   ├── student/
│   │   ├── auth.py          # Login & Onboarding (Private)
│   │   └── profile.py       # Public Profile (No Token)
│   ├── teacher/
│   │   ├── auth.py          # Login & Onboarding (Private)
│   │   └── profile.py       # Public Profile (No Token)
│   └── admin/               # Admin APIs
└── utils/                   # Helpers (Firebase, Security, etc.)
```

---

## 👨‍🎓 Student APIs

### 1. Login/Sync (Private)
*   **URL**: `POST /api/student/auth/sync`
*   **What it does**: Takes a Firebase ID Token, checks if user exists. If not, creates a new "blank" user.
*   **Returns**: A `jwt` token for future calls.

### 2. Onboarding (Private)
*   **URL**: `PATCH /api/student/auth/onboarding`
*   **Input**:
    ```json
    {
      "name": "Ramu",
      "email": "ramu@school.com",
      "profile_image_url": "https://...",
      "dob": "2010-01-01",
      "age": 14,
      "school_college": "Grand Oak High School",
      "location": "Mumbai"
    }
    ```
*   **What it does**: Updates the user's profile with personal details.

### 3. Get Student Profile (Token Required)
*   **URL**: `GET /api/student/profile/{user_id}`
*   **Headers**: `Authorization: Bearer <your-token>`
*   **What it does**: Returns the details of a student. Use this to view any student's profile securely.

---

## 👩‍🏫 Teacher APIs

### 1. Login/Sync (Private)
*   **URL**: `POST /api/teacher/auth/sync`
*   **What it does**: Checks Firebase Token. Creates "blank" teacher if new.

### 2. Onboarding (Private)
*   **URL**: `PATCH /api/teacher/auth/onboarding`
*   **Input**:
    ```json
    {
      "name": "Lakshmi Devi",
      "email": "lakshmi@university.com",
      "profile_image_url": "https://...",
      "document_url": "https://storage.google.com/certs/my_degree.pdf"
    }
    ```
*   **What it does**: Saves teacher details + certificate link.

### 3. Get Teacher Profile (Token Required)
*   **URL**: `GET /api/teacher/profile/{user_id}`
*   **Headers**: `Authorization: Bearer <your-token>`
*   **What it does**: Returns the details of a teacher. Use this to view any teacher's profile securely.

---

## 📦 Database Model (Technical Note)
We use **One Table (`users`)** for everyone. This is the smartest way for a 10-year-old or a pro to do it because:
1.  **Firebase UID** is unique for everyone.
2.  Logins are handled in one place.
3.  Student fields (`age`, `school`) are just empty for Teachers.
4.  Teacher fields (`document_url`) are just empty for Students.
