# Run while the server is running. Creates 2 mini runs, calls /compare, prints the result.
import json
import urllib.request
import urllib.error
from db import db

BASE = "http://localhost:8000"
OK, FAIL = "cmp_ok", "cmp_fail"


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def mk(run, seq, type_, payload):
    return {"eventId": f"{run}-{seq:03d}", "runId": run, "projectId": "test-project",
            "type": type_, "ts": f"2026-10-31T13:00:{seq:02d}.000Z", "seq": seq, "payload": payload}


def build(run, good):
    q = "auth flow" if good else ""
    docs = 5 if good else 0
    return [
        mk(run, 0, "run_started", {"promptVersion": "v2" if good else "v1", "model": "mock-llm-1"}),
        mk(run, 1, "prompt", {"text": "Summarize auth docs", "tokens": 120}),
        mk(run, 2, "llm_output", {"text": "I will search", "tokensIn": 300, "tokensOut": 50, "latencyMs": 900}),
        mk(run, 3, "tool_call", {"tool": "search_docs", "args": {"query": q}}),
        mk(run, 4, "tool_result", {"tool": "search_docs", "ok": True, "latencyMs": 800, "result": {}}),
        mk(run, 5, "retrieval", {"query": q, "docCount": docs, "docs": []}),
        mk(run, 6, "llm_output", {"text": "answer", "tokensIn": 400, "tokensOut": 150, "latencyMs": 1200}),
        mk(run, 7, "run_finished", {"status": "success" if good else "failed"}),
    ]


def cleanup():
    db.events.delete_many({"runId": {"$in": [OK, FAIL]}})
    db.runs.delete_many({"_id": {"$in": [OK, FAIL]}})


cleanup()
print("seed:", call("/events", build(OK, True) + build(FAIL, False))[1])

status, res = call(f"/compare?failed={FAIL}&success={OK}")
print("\nstatus:", status)
print("steps:", len(res["steps"]))
print("firstDivergence:", json.dumps(res["firstDivergence"], indent=2))
print("diverged steps:", [(s["seq"], s["reasons"]) for s in res["steps"] if s["diverged"]])
print("findings:", res["findings"])

print("\nunknown run (expect 404):", call(f"/compare?failed=nope&success={OK}"))

input("\nPress Enter to delete the test data...")
cleanup()
print("test data deleted")