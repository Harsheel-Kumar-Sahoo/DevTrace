# Read-only endpoints: /projects, /projects/{id}/runs, /runs/{id}/events
from fastapi import APIRouter
from db import db

router = APIRouter()


def iso(dt):
    """datetime -> '2026-10-31T12:00:03.120Z' (the format the contract uses)."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"

def run_summary(r):
    """Turn a runs document into the shape the contract wants."""
    totals = r.get("totals") or {}
    started, finished = r.get("startedAt"), r.get("finishedAt")
    duration = None
    if started and finished:
        duration = int((finished - started).total_seconds() * 1000)
    return {
        "runId": r["_id"],
        "status": r.get("status", "running"),
        "startedAt": iso(started),
        "finishedAt": iso(finished),
        "durationMs": duration,
        "stepCount": totals.get("stepCount", 0),
        "totalTokens": totals.get("totalTokens", 0),
        "promptVersion": r.get("promptVersion"),
        "model": r.get("model"),
    }


# Plain "def" (not async) on purpose: FastAPI runs these in a thread,
# which is what we want because pymongo is synchronous.
@router.get("/projects")
def list_projects():
    # Count runs per project straight from the runs collection.
    rows = db.runs.aggregate([
        {"$group": {"_id": "$projectId", "runCount": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ])
    return [{"projectId": r["_id"], "runCount": r["runCount"]} for r in rows]


@router.get("/projects/{project_id}/runs")
def list_runs(project_id: str):
    runs = db.runs.find({"projectId": project_id}).sort("startedAt", -1)
    return [run_summary(r) for r in runs]


@router.get("/runs/{run_id}/events")
def list_events(run_id: str):
    # Uses the {runId, seq} index from A1.
    docs = db.events.find({"runId": run_id}).sort("seq", 1)
    out = []
    for d in docs:
        d.pop("_id", None)       # ObjectId can't be turned into JSON, and the frontend doesn't need it
        d["ts"] = iso(d["ts"])   # datetime -> ISO string
        out.append(d)
    return out