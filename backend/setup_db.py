# Run once (safe to re-run). Creates collections, validator, and indexes.
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import WriteError
from datetime import datetime, timezone
from db import db

EVENT_TYPES = [
    "run_started", "prompt", "retrieval", "tool_call",
    "tool_result", "llm_output", "error", "run_finished",
]

# The rulebook MongoDB enforces on every event insert.
# 'payload' is only required to be an object; its inside is free-form.
events_validator = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["eventId", "runId", "projectId", "type", "ts", "seq"],
        "properties": {
            "eventId":   {"bsonType": "string", "minLength": 1},
            "runId":     {"bsonType": "string", "minLength": 1},
            "projectId": {"bsonType": "string", "minLength": 1},
            "type":      {"enum": EVENT_TYPES},
            "ts":        {"bsonType": "date"},
            "seq":       {"bsonType": "int", "minimum": 0},
            "payload":   {"bsonType": "object"},
        },
        # additionalProperties not set to false -> extra fields are allowed
    }
}

# 1) Create collections
existing = db.list_collection_names()
for name in ["projects", "runs", "findings"]:
    if name not in existing:
        db.create_collection(name)
        print(f"created collection: {name}")
    else:
        print(f"already exists:     {name}")

# 2) events collection with validator (create, or update the rules if it exists)
if "events" not in existing:
    db.create_collection(
        "events",
        validator=events_validator,
        validationLevel="strict",
        validationAction="error",
    )
    print("created collection: events (with $jsonSchema validator)")
else:
    db.command(
        "collMod", "events",
        validator=events_validator,
        validationLevel="strict",
        validationAction="error",
    )
    print("already exists:     events (validator updated)")

# 3) Indexes on events
ev = db["events"]
ev.create_index([("eventId", ASCENDING)], unique=True, name="uniq_eventId")
ev.create_index([("runId", ASCENDING), ("seq", ASCENDING)], name="runId_seq")
ev.create_index([("projectId", ASCENDING), ("ts", DESCENDING)], name="projectId_ts")
ev.create_index([("runId", ASCENDING), ("type", ASCENDING)], name="runId_type")
print("indexes ready on events")

# 4) Self-test: this bad event MUST be rejected by MongoDB
bad_event = {
    "eventId": "test-bad-001",
    "runId": "test_run",
    "projectId": "test",
    "type": "banana",                 # not in the allowed list
    "ts": datetime.now(timezone.utc),
    "seq": 0,
}
try:
    ev.insert_one(bad_event)
    print("PROBLEM: bad event was accepted! Validator is not working.")
except WriteError:
    print("validator test passed: invalid event was rejected")

print("\nIndexes now on events:")
for idx in ev.list_indexes():
    print("  ", idx["name"], dict(idx["key"]))