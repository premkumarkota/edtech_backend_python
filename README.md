# EdTech LMS Backend

FastAPI backend for Learning Management System with CBSE/SSC exam preparation.

## Setup

1. Create virtual environment:
```bash
python -m venv myenv
myenv\Scripts\activate  # Windows
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create `.env` file:
```bash
cp .env.example .env
# Edit .env with your database credentials
```

4. Run server:
```bash
uvicorn app.main:app --reload
```

## API Documentation

Once running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Database

PostgreSQL database: `learning_management_system`

## Project Structure

```
edtech-backend/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app
│   ├── config.py        # Settings
│   ├── database.py      # DB connection
│   ├── models/          # SQLAlchemy models
│   ├── schemas/         # Pydantic schemas
│   ├── routers/         # API endpoints
│   ├── services/        # Business logic
│   └── utils/           # Helper functions
├── tests/
├── .env                 # Environment variables (not in git)
├── .env.example         # Template
├── requirements.txt     # Dependencies
└── README.md
```
