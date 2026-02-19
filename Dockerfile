
# Use an official lightweight Python image.
# https://hub.docker.com/_/python
FROM python:3.10-slim

# Set environment variables
# PYTHONDONTWRITEBYTECODE: Prevents Python from writing pyc files to disc
# PYTHONUNBUFFERED: Prevents Python from buffering stdout and stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose the port that the application listens on.
# Cloud Run sets the PORT environment variable (default 8080).
ENV PORT=8080
EXPOSE 8080

# Command to run the application using Uvicorn
# We use the $PORT environment variable to ensure compatibility with Cloud Run
CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"
