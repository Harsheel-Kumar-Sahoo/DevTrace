# App entry point. Run with: uvicorn main:app --reload --port 8000
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import routes_events
import routes_read
import routes_compare
import routes_stream

app = FastAPI(title="DevTrace API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # hackathon shortcut: allow any website to call us
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


app.include_router(routes_events.router)
app.include_router(routes_read.router)
app.include_router(routes_compare.router)
app.include_router(routes_stream.router)

# Serve ../frontend at "/". Must stay LAST, otherwise it swallows other routes.
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")