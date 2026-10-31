# GET /stream?projectId=research-agent  -> Server-Sent Events (SSE)
import json
from typing import Optional
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from db import db
from routes_read import iso

router = APIRouter()


def sse_error(message, detail=""):
    # Named "stream_error" (not "error") so it does not clash with the browser's own error event.
    body = json.dumps({"error": message, "detail": detail[:300]})
    return f"event: stream_error\ndata: {body}\n\n"


def event_stream(project_id):
    # Only listen for NEW events (inserts), optionally for one project.
    match = {"operationType": "insert"}
    if project_id:
        match["fullDocument.projectId"] = project_id

    try:
        # Opens the change stream. max_await_time_ms=1000 means: wait at most 1 second
        # for news, then give control back to us (so we can send keepalives).
        stream = db.events.watch([{"$match": match}], max_await_time_ms=1000)
    except Exception as e:
        yield sse_error("could not open change stream", str(e))
        return

    try:
        yield ": connected\n\n"          # a comment line; tells the browser we are live
        idle = 0
        while stream.alive:
            change = stream.try_next()   # returns None if nothing new within 1 second
            if change is None:
                idle += 1
                if idle >= 15:           # about every 15 seconds
                    idle = 0
                    yield ": keepalive\n\n"   # stops proxies from closing a quiet connection
                continue
            idle = 0
            doc = change["fullDocument"]
            doc.pop("_id", None)
            doc["ts"] = iso(doc["ts"])
            yield f"data: {json.dumps(doc, default=str)}\n\n"
    except Exception as e:
        yield sse_error("stream stopped", str(e))
    finally:
        stream.close()


# Plain "def" on purpose: FastAPI runs the generator in a worker thread (pymongo is sync).
@router.get("/stream")
def stream(projectId: Optional[str] = None):
    return StreamingResponse(
        event_stream(projectId),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )