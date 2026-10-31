# Shared database connection. Every other file does: from db import db
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()  # reads the .env file

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "devtrace")

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is missing. Check backend/.env")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
db = client[DB_NAME]