# POST /events: validate, insert (ignoring duplicates), update the runs document.
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pymongo.errors import BulkWriteError
from db import db

router = APIRouter()

EVENT_TYPES = [
    "run_started", "prompt", "retrieval", "tool_call",
    "tool_result", "llm_output", "error", "run_finished",
]
REQUIRED = ["eventId", "runId", "projectId", "type", "ts", "seq"]


def parse_ts(value):
    """'2026-10-31T12:00:03.120Z' (string) -> datetime in UTC."""
    if not isinstance(value, str):
        raise ValueError("ts must be an ISO-8601 string like 2026-10-31T12:00:03.120Z")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("ts is not a valid ISO-8601 date string")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_event(raw):
    """Returns (document, None) if OK, or (None, 'error message') if bad."""
    if not isinstance(raw, dict):
        return None, "event must be a JSON object"

    missing = [f for f in REQUIRED if f not in raw]
    if missing:
        return None, f"missing required field(s): {missing}"

    for f in ("eventId", "runId", "projectId"):
        if not isinstance(raw[f], str) or not raw[f].strip():
            return None, f"{f} must be a non-empty string"

    if raw["type"] not in EVENT_TYPES:
        return None, f"type must be one of {EVENT_TYPES}"

    seq = raw["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0 or seq > 2_000_000_000:
        return None, "seq must be an integer >= 0"

    try:
        ts = parse_ts(raw["ts"])
    except ValueError as e:
        return None, str(e)

    if "payload" in raw and not isinstance(raw["payload"], dict):
        return None, "payload must be a JSON object"

    doc = dict(raw)
    doc["ts"] = ts  # string -> datetime (stored as BSON date)
    return doc, None


def num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def tokens_of(doc):
    p = doc.get("payload") or {}
    if doc["type"] == "prompt":
        return num(p.get("tokens"))
    if doc["type"] == "llm_output":
        return num(p.get("tokensIn")) + num(p.get("tokensOut"))
    return 0


def update_runs(inserted_docs):
    """Update the runs collection using ONLY events that were really inserted.
    Duplicates never reach this function, so totals are never double counted."""
    by_run = {}
    for d in inserted_docs:
        by_run.setdefault(d["runId"], []).append(d)

    for run_id, docs in by_run.items():
        set_fields = {}
        for d in docs:
            p = d.get("payload") or {}
            if d["type"] == "run_started":
                if "promptVersion" in p:
                    set_fields["promptVersion"] = p["promptVersion"]
                if "model" in p:
                    set_fields["model"] = p["model"]
            elif d["type"] == "run_finished":
                status = p.get("status")
                set_fields["status"] = status if status in ("success", "failed") else "failed"
                set_fields["finishedAt"] = d["ts"]

        update = {
            "$inc": {
                "totals.stepCount": len(docs),
                "totals.totalTokens": sum(tokens_of(d) for d in docs),
            },
            "$min": {"startedAt": min(d["ts"] for d in docs)},
            "$setOnInsert": {"runId": run_id, "projectId": docs[0]["projectId"]},
        }
        if set_fields:
            update["$set"] = set_fields
        if "status" not in set_fields:
            update["$setOnInsert"]["status"] = "running"

        db.runs.update_one({"_id": run_id}, update, upsert=True)


def process_events(events):
    """The real work. Plain (sync) function because pymongo is sync."""
    rejected = []
    valid = []        # documents that passed our own checks
    valid_index = []  # their position in the original request

    for i, raw in enumerate(events):
        doc, err = validate_event(raw)
        if err:
            event_id = raw.get("eventId") if isinstance(raw, dict) else None
            rejected.append({"index": i, "eventId": event_id, "error": err})
        else:
            valid.append(doc)
            valid_index.append(i)

    duplicates = 0
    inserted_docs = list(valid)

    if valid:
        try:
            db.events.insert_many(valid, ordered=False)
        except BulkWriteError as e:
            # Some docs failed. Find which ones and why.
            failed = {we["index"]: we for we in e.details.get("writeErrors", [])}
            inserted_docs = []
            for pos, doc in enumerate(valid):
                we = failed.get(pos)
                if we is None:
                    inserted_docs.append(doc)
                elif we["code"] == 11000:  # duplicate eventId: not an error
                    duplicates += 1
                else:  # e.g. code 121 = MongoDB $jsonSchema validation failed
                    rejected.append({
                        "index": valid_index[pos],
                        "eventId": doc["eventId"],
                        "error": "MongoDB validation failed: " + str(we.get("errmsg", "")),
                    })

    if inserted_docs:
        update_runs(inserted_docs)

    rejected.sort(key=lambda r: r["index"])
    return {
        "received": len(events),
        "inserted": len(inserted_docs),
        "duplicates": duplicates,
        "rejected": rejected,
    }


@router.post("/events")
async def post_events(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "body is not valid JSON"})

    if isinstance(body, dict):
        events = [body]
    elif isinstance(body, list):
        events = body
    else:
        return JSONResponse(status_code=400, content={"error": "body must be an event object or an array of events"})

    # run the blocking database work in a thread so the server stays responsive
    return await run_in_threadpool(process_events, events)