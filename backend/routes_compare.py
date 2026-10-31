# GET /compare?failed=<runId>&success=<runId>   (full version)
from fastapi import APIRouter, HTTPException
from pymongo import ReplaceOne
from db import db
from routes_read import run_summary, iso

router = APIRouter()

# Hackathon thresholds (our guesses, tweak if the demo data needs it)
SLOW_FACTOR = 2        # failed step must be at least 2x slower...
SLOW_MIN_EXTRA_MS = 1000  # ...and at least 1000 ms slower
TOKEN_FACTOR = 1.5
TOKEN_MIN_EXTRA = 200

SEVERITY = {
    "error": "error", "missing_retrieval": "error", "different_args": "error",
    "different_tool": "error", "missing_step": "error",
    "slow_step": "warning", "different_output": "info",
}


def clean(ev):
    """Remove _id (not JSON-safe) and turn the date into a string."""
    if ev is None:
        return None
    ev = dict(ev)
    ev.pop("_id", None)
    ev["ts"] = iso(ev["ts"])
    return ev


def num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def latency(ev):
    v = ((ev or {}).get("payload") or {}).get("latencyMs")
    return v if num(v) else None


def tokens(ev):
    p = (ev or {}).get("payload") or {}
    return (p.get("tokensIn") if num(p.get("tokensIn")) else 0) + \
           (p.get("tokensOut") if num(p.get("tokensOut")) else 0)


def describe_args(args):
    args = args or {}
    if "query" in args:
        q = args["query"]
        if q is None or str(q).strip() == "":
            return "an empty query"
        return f"'{q}'"
    return str(args) if args else "no arguments"


def find_reasons(f, s, downstream):
    """Compare ONE step. 'downstream' = True if an earlier step already diverged."""
    if f is None or s is None:
        return ["missing_step"]

    fp = f.get("payload") or {}
    sp = s.get("payload") or {}
    reasons = []

    if f["type"] == "error" and s["type"] != "error":
        return ["error"]
    if f["type"] != s["type"]:
        return ["different_output"]

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
    elif t == "llm_output":
        # Texts almost always differ a little, so we only call it a divergence
        # when something earlier already went wrong (downstream effect).
        if downstream and fp.get("text") != sp.get("text"):
            reasons.append("different_output")
    elif t == "run_finished":
        if fp.get("status") != sp.get("status"):
            reasons.append("different_output")

    fl, sl = latency(f), latency(s)
    if fl is not None and sl is not None and fl >= SLOW_FACTOR * sl and fl - sl >= SLOW_MIN_EXTRA_MS:
        reasons.append("slow_step")
    return reasons


def explain(seq, reason, f, s):
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
        msg = fp.get("message") or f"{fp.get('tool', 'a tool')} returned ok=false"
        return f"At step {seq} the failed run hit an error: {msg}."
    if reason == "slow_step":
        return (f"At step {seq} the failed run took {latency(f)} ms; "
                f"the successful run took {latency(s)} ms.")
    if reason == "missing_step":
        who = "failed" if f is None else "successful"
        return f"At step {seq} the {who} run has no event, but the other run does."
    if reason == "different_output":
        return f"At step {seq} the failed run produced a different result than the successful run."
    return f"At step {seq} the two runs behaved differently ({reason})."


def title_for(reason, f, s):
    fp = (f or {}).get("payload") or {}
    sp = (s or {}).get("payload") or {}
    if reason == "missing_retrieval":
        return f"Retrieval returned {fp.get('docCount', 0)} documents vs {sp.get('docCount')}"
    if reason == "different_args":
        return f"{fp.get('tool')} called with different arguments"
    if reason == "different_tool":
        return "A different tool was called"
    if reason == "error":
        return "Error in the failed run"
    if reason == "slow_step":
        return f"Step took {latency(f)} ms vs {latency(s)} ms"
    if reason == "missing_step":
        return "A step is missing in one run"
    return "Different output"


def make_finding(failed, success, rule, seq, severity, title, detail):
    return {
        "_id": f"{failed}__{success}__{rule}__{seq}",   # deterministic -> safe to re-run
        "failedRunId": failed, "successRunId": success,
        "severity": severity, "title": title, "detail": detail, "seq": seq,
    }


def run_totals(failed, success):
    """Pipeline (a): one summary row per run, computed from its events."""
    pipeline = [
        {"$match": {"runId": {"$in": [failed, success]}}},
        {"$group": {
            "_id": "$runId",
            "events": {"$sum": 1},
            "errors": {"$sum": {"$cond": [{"$eq": ["$type", "error"]}, 1, 0]}},
            "latencyMs": {"$sum": {"$ifNull": ["$payload.latencyMs", 0]}},
            "tokens": {"$sum": {"$add": [
                {"$ifNull": ["$payload.tokens", 0]},
                {"$ifNull": ["$payload.tokensIn", 0]},
                {"$ifNull": ["$payload.tokensOut", 0]},
            ]}},
        }},
    ]
    return {row["_id"]: row for row in db.events.aggregate(pipeline)}


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

    # Pipeline (b): $group on seq puts both runs' events for the same step in one row.
    pipeline = [
        {"$match": {"runId": {"$in": [failed, success]}}},
        {"$group": {"_id": "$seq", "events": {"$push": "$$ROOT"}}},
        {"$sort": {"_id": 1}},
    ]

    steps, findings = [], []
    first = None
    for row in db.events.aggregate(pipeline):
        seq = row["_id"]
        f = s = None
        for ev in row["events"]:
            if ev["runId"] == failed and f is None:
                f = ev
            elif ev["runId"] == success and s is None:
                s = ev

        reasons = find_reasons(f, s, downstream=first is not None)
        steps.append({
            "seq": seq, "failed": clean(f), "success": clean(s),
            "diverged": len(reasons) > 0, "reasons": reasons,
        })

        for r in reasons:
            findings.append(make_finding(
                failed, success, r, seq, SEVERITY.get(r, "info"),
                title_for(r, f, s), explain(seq, r, f, s)))

        if first is None and reasons:
            first = {"seq": seq, "reason": reasons[0], "explanation": explain(seq, reasons[0], f, s)}

        # Pipeline (d) idea, per step: token comparison (a finding only, not a "reason")
        if f and s and f["type"] == "llm_output" and s["type"] == "llm_output":
            ft, st = tokens(f), tokens(s)
            if ft >= TOKEN_FACTOR * st and ft - st >= TOKEN_MIN_EXTRA:
                findings.append(make_finding(
                    failed, success, "more_tokens", seq, "info",
                    f"Used {ft} tokens vs {st}",
                    f"At step {seq} the failed run used {ft} tokens; the successful run used {st}."))

    # Whole-run summary finding (seq -1 means "whole run, not one step")
    totals = run_totals(failed, success)
    ft, st = totals.get(failed, {}), totals.get(success, {})
    findings.append(make_finding(
        failed, success, "run_totals", -1, "info", "Run totals compared",
        f"Failed run: {ft.get('events', 0)} events, {ft.get('errors', 0)} errors, "
        f"{ft.get('latencyMs', 0)} ms of step latency, {ft.get('tokens', 0)} tokens. "
        f"Successful run: {st.get('events', 0)} events, {st.get('errors', 0)} errors, "
        f"{st.get('latencyMs', 0)} ms, {st.get('tokens', 0)} tokens."))

    # Store findings. Same _id -> replaced, never duplicated. Old ones no longer true are removed.
    ids = [d["_id"] for d in findings]
    db.findings.delete_many({"failedRunId": failed, "successRunId": success, "_id": {"$nin": ids}})
    db.findings.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in findings])

    findings.sort(key=lambda d: (d["seq"], d["_id"]))
    return {
        "failedRun": run_summary(failed_run),
        "successRun": run_summary(success_run),
        "steps": steps,
        "firstDivergence": first,
        "findings": findings,
    }