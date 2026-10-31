# Run while the server is running. Tests insert, duplicates, bad events, run totals.
import json
import urllib.request
import urllib.error
from db import db

URL = "http://localhost:8000/events"
RUN = "t_run_1"


def post(text):
    req = urllib.request.Request(
        URL, data=text.encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def ev(seq, type_, payload=None, **over):
    e = {
        "eventId": f"{RUN}-{seq:03d}", "runId": RUN, "projectId": "test-project",
        "type": type_, "ts": f"2026-10-31T12:00:{seq:02d}.000Z", "seq": seq,
        "payload": payload or {},
    }
    e.update(over)
    return e


def show_run(label):
    r = db.runs.find_one({"_id": RUN})
    print(f"  run now ({label}): status={r.get('status')} "
          f"stepCount={r['totals']['stepCount']} totalTokens={r['totals']['totalTokens']} "
          f"promptVersion={r.get('promptVersion')} finishedAt={r.get('finishedAt')}")


def cleanup():
    db.events.delete_many({"runId": RUN})
    db.runs.delete_one({"_id": RUN})


cleanup()

print("\nTEST 1: batch of 3 new events")
batch = [
    ev(0, "run_started", {"promptVersion": "v1", "model": "mock-llm-1"}),
    ev(1, "prompt", {"text": "hi", "tokens": 120}),
    ev(2, "llm_output", {"text": "ok", "tokensIn": 300, "tokensOut": 150, "latencyMs": 1200}),
]
print(" ", post(json.dumps(batch)))
show_run("expect running, 3 steps, 570 tokens")

print("\nTEST 2: send the SAME batch again (all duplicates)")
print(" ", post(json.dumps(batch)))
show_run("expect NO change: 3 steps, 570 tokens")

print("\nTEST 3: mixed batch: 1 duplicate + 1 new + 2 bad")
bad_type = ev(4, "banana")
no_ts = ev(5, "prompt")
del no_ts["ts"]
mixed = [batch[0], ev(3, "run_finished", {"status": "success"}), bad_type, no_ts]
print(" ", post(json.dumps(mixed)))
show_run("expect success, 4 steps, 570 tokens, finishedAt set")

print("\nTEST 4: body that is not JSON (expect status 400)")
print(" ", post("this is not json"))

input("\nLook at Atlas now if you want (events + runs). Press Enter to delete the test data...")
cleanup()
print("test data deleted")