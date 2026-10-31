# App entry point. Run with: uvicorn main:app --reload --port 8000
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import routes_events

app = FastAPI(title="DevTrace API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # hackathon shortcut: allow any website to call us
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_events.router)
# A3 routers get added HERE, above the static mount below.

# Serve ../frontend at "/". Must stay LAST, otherwise it swallows other routes.
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")