# Run while the server is running.
# Opens /stream in a background thread, then sends events and checks they arrive live.
import json
import threading
import time
import urllib.request
from db import db

BASE = "http://localhost:8000"
RUN = "stream_run"
received = []


def reader():
    r = urllib.request.urlopen(BASE + "/stream?projectId=test-project", timeout=40)
    for raw in r:
        line = raw.decode().strip()
        if line.startswith("data:"):
            ev = json.loads(line[5:])
            received.append(ev)
            print(f"  LIVE  got {ev['eventId']} ({ev['type']}) at {time.strftime('%H:%M:%S')}")
            if len(received) >= 3:
                break
    r.close()


def post(body):
    req = urllib.request.Request(BASE + "/events", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def mk(run, seq, project="test-project", type_="prompt"):
    return {"eventId": f"{run}-{seq:03d}", "runId": run, "projectId": project, "type": type_,
            "ts": f"2026-10-31T14:00:{seq:02d}.000Z", "seq": seq, "payload": {"text": "hi", "tokens": 10}}


def cleanup():
    db.events.delete_many({"runId": RUN})
    db.runs.delete_one({"_id": RUN})


cleanup()
t = threading.Thread(target=reader, daemon=True)
t.start()
print("stream opened, waiting 4 seconds so it is fully connected...")
time.sleep(4)

print("sending 3 events for test-project, 1 second apart")
for i in range(3):
    print("  sent", post(mk(RUN, i)))
    time.sleep(1)

print("sending 1 event for ANOTHER project (should NOT show up)")
post(mk(RUN, 10, project="other-project"))
print("sending a DUPLICATE of seq 0 (should NOT show up)")
print("  ", post(mk(RUN, 0)))

t.join(timeout=10)
print(f"\nlive messages received: {len(received)} (expected 3)")
print("PASS" if len(received) == 3 else "CHECK: something is off")
cleanup()
db.events.delete_many({"runId": RUN})
print("test data deleted")