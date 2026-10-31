# GET /compare?failed=<runId>&success=<runId>   (simple version)
from fastapi import APIRouter, HTTPException
from db import db
from routes_read import run_summary, iso

router = APIRouter()


def clean(ev):
    """Remove _id (not JSON-safe) and turn the date into a string."""
    if ev is None:
        return None
    ev = dict(ev)
    ev.pop("_id", None)
    ev["ts"] = iso(ev["ts"])
    return ev


def describe_args(args):
    """Turn tool args into words for the explanation."""
    args = args or {}
    if "query" in args:
        q = args["query"]
        if q is None or str(q).strip() == "":
            return "an empty query"
        return f"'{q}'"
    return str(args) if args else "no arguments"


def find_reasons(f, s):
    """Compare ONE step of the failed run (f) with the same step of the success run (s)."""
    if f is None or s is None:
        return ["missing_step"]

    fp = f.get("payload") or {}
    sp = s.get("payload") or {}

    if f["type"] == "error" and s["type"] != "error":
        return ["error"]
    if f["type"] != s["type"]:
        return ["different_output"]

    reasons = []
    t = f["type"]
    if t == "tool_call":
        if fp.get("tool") != sp.get("tool"):
            reasons.append("different_tool")
        elif fp.get("args") != sp.get("args"):
            reasons.append("different_args")
    elif t == "retrieval":
        if fp.get("docCount", 0) == 0 and sp.get("docCount", 0) > 0:
            reasons.append("missing_retrieval")
    elif t == "tool_result":
        if fp.get("ok") is False and sp.get("ok") is not False:
            reasons.append("error")
    return reasons


def explain(seq, reason, f, s):
    """Plain-English sentence for the first divergence."""
    fp = (f or {}).get("payload") or {}
    sp = (s or {}).get("payload") or {}

    if reason == "different_args":
        return (f"At step {seq} the failed run called {fp.get('tool')} with "
                f"{describe_args(fp.get('args'))}; the successful run used "
                f"{describe_args(sp.get('args'))}.")
    if reason == "different_tool":
        return (f"At step {seq} the failed run called {fp.get('tool')}; "
                f"the successful run called {sp.get('tool')}.")
    if reason == "missing_retrieval":
        return (f"At step {seq} the failed run retrieved {fp.get('docCount', 0)} documents; "
                f"the successful run retrieved {sp.get('docCount')}.")
    if reason == "error":
        msg = fp.get("message") or "a tool call returned ok=false"
        return f"At step {seq} the failed run hit an error: {msg}."
    if reason == "missing_step":
        who = "failed" if f is None else "successful"
        return f"At step {seq} the {who} run has no event, but the other run does."
    return f"At step {seq} the two runs behaved differently ({reason})."


@router.get("/compare")
def compare(failed: str, success: str):
    if failed == success:
        raise HTTPException(status_code=400, detail="failed and success must be different runs")

    failed_run = db.runs.find_one({"_id": failed})
    success_run = db.runs.find_one({"_id": success})
    if failed_run is None:
        raise HTTPException(status_code=404, detail=f"run not found: {failed}")
    if success_run is None:
        raise HTTPException(status_code=404, detail=f"run not found: {success}")

    # Aggregation pipeline: line the two runs up step by step.
    # $group on seq puts the failed and success event of the same step in one row.
    pipeline = [
        {"$match": {"runId": {"$in": [failed, success]}}},
        {"$group": {"_id": "$seq", "events": {"$push": "$$ROOT"}}},
        {"$sort": {"_id": 1}},
    ]

    steps = []
    first = None
    for row in db.events.aggregate(pipeline):
        f = s = None
        for ev in row["events"]:
            if ev["runId"] == failed and f is None:
                f = ev
            elif ev["runId"] == success and s is None:
                s = ev

        reasons = find_reasons(f, s)
        steps.append({
            "seq": row["_id"],
            "failed": clean(f),
            "success": clean(s),
            "diverged": len(reasons) > 0,
            "reasons": reasons,
        })
        if first is None and reasons:
            first = {
                "seq": row["_id"],
                "reason": reasons[0],
                "explanation": explain(row["_id"], reasons[0], f, s),
            }

    return {
        "failedRun": run_summary(failed_run),
        "successRun": run_summary(success_run),
        "steps": steps,
        "firstDivergence": first,   # null if the runs never diverge
        "findings": [],             # filled in A4 part 2
    }