# Run while the server is running. Tests rules, stored findings, and safe re-running.
import json
import urllib.request
import urllib.error
from db import db

BASE = "http://localhost:8000"
OK, FAIL, SLOW = "cmp_ok", "cmp_fail", "cmp_slow"
PAIR = {"failedRunId": FAIL, "successRunId": OK}


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
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


def build_slow(run):
    evs = build(run, True)  # same as the good run, except step 4 is slow + errors, and it fails
    evs[4]["payload"] = {"tool": "search_docs", "ok": False, "latencyMs": 9000, "result": {}}
    evs[7]["payload"] = {"status": "failed"}
    return evs


def cleanup():
    db.events.delete_many({"runId": {"$in": [OK, FAIL, SLOW]}})
    db.runs.delete_many({"_id": {"$in": [OK, FAIL, SLOW]}})
    db.findings.delete_many({"failedRunId": {"$in": [FAIL, SLOW]}})


cleanup()
call("/events", build(OK, True) + build(FAIL, False) + build_slow(SLOW))

print("TEST 1: failed vs success (retrieval scenario)")
status, res = call(f"/compare?failed={FAIL}&success={OK}")
print("  status:", status)
print("  firstDivergence:", res["firstDivergence"])
print("  diverged steps:", [(s["seq"], s["reasons"]) for s in res["steps"] if s["diverged"]])
print("  findings:", [(f["seq"], f["_id"].split("__")[2], f["severity"]) for f in res["findings"]])
print("  stored in MongoDB:", db.findings.count_documents(PAIR))

print("\nTEST 2: call it again (findings must NOT double)")
call(f"/compare?failed={FAIL}&success={OK}")
call(f"/compare?failed={FAIL}&success={OK}")
print("  stored in MongoDB:", db.findings.count_documents(PAIR))

print("\nTEST 3: slow + error scenario")
status, res = call(f"/compare?failed={SLOW}&success={OK}")
print("  firstDivergence:", res["firstDivergence"])
print("  diverged steps:", [(s["seq"], s["reasons"]) for s in res["steps"] if s["diverged"]])

print("\nTEST 4: same run twice (expect 400):", call(f"/compare?failed={OK}&success={OK}"))

input("\nLook at the `findings` collection in Atlas. Press Enter to delete the test data...")
cleanup()
print("test data deleted")