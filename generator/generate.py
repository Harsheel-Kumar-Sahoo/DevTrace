# generator/generate.py
# Fake "research agent" simulator. Default: writes mock JSON into frontend/mock/.
#   python generator/generate.py          -> write mock files
#   python generator/generate.py --post   -> POST all events to the backend (batch)
#   python generator/generate.py --live   -> POST run_live_001, one event per second
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "http://localhost:8000"
PROJECT = "research-agent"
# Path(__file__) = this file; go up one folder (repo root), then frontend/mock
MOCK_DIR = Path(__file__).resolve().parent.parent / "frontend" / "mock"

TASK = "Summarize our auth docs"
URL = "https://docs.example.com/auth"
DOCS = [
    {"id": "d1", "title": "Auth flow overview"},
    {"id": "d2", "title": "OAuth token refresh"},
    {"id": "d3", "title": "Session cookies"},
    {"id": "d4", "title": "API key rotation"},
    {"id": "d5", "title": "SSO setup guide"},
]


def iso(dt):
    """datetime -> '2026-10-31T12:00:03.120Z' (what the contract wants)."""
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------- 1. The scripted agent: (type, payload, ms_since_previous_step) ----------
def build_steps(scenario, prompt_version):
    # Steps 0-2 are identical in every scenario
    steps = [
        ("run_started", {"promptVersion": prompt_version, "model": "mock-llm-1", "task": TASK}, 0),
        ("prompt", {"text": "You are a research agent. Task: " + TASK, "tokens": 120}, 50),
        ("llm_output", {"text": "Plan: search the docs for the auth flow, read the best page, then summarize.",
                        "tokensIn": 120, "tokensOut": 30, "latencyMs": 900}, 900),
    ]
    if scenario == "fail1":
        # FIRST DIVERGENCE (seq 3): empty query -> 0 docs
        steps += [
            ("tool_call", {"tool": "search_docs", "args": {"query": ""}}, 40),
            ("tool_result", {"tool": "search_docs", "ok": True, "latencyMs": 800, "result": {"docCount": 0}}, 800),
            ("retrieval", {"query": "", "docCount": 0, "docs": []}, 30),
        ]
    else:
        steps += [
            ("tool_call", {"tool": "search_docs", "args": {"query": "auth flow"}}, 40),
            ("tool_result", {"tool": "search_docs", "ok": True, "latencyMs": 800, "result": {"docCount": 5}}, 800),
            ("retrieval", {"query": "auth flow", "docCount": 5, "docs": DOCS}, 30),
        ]
    steps.append(("tool_call", {"tool": "read_url", "args": {"url": URL}}, 60))

    if scenario == "fail2":
        # read_url takes 9000ms and errors
        steps += [
            ("tool_result", {"tool": "read_url", "ok": False, "latencyMs": 9000, "result": {"error": "timeout"}}, 9000),
            ("error", {"message": "read_url timed out after 9000ms", "code": "TIMEOUT"}, 10),
            ("run_finished", {"status": "failed"}, 10),
        ]
    else:
        steps.append(("tool_result", {"tool": "read_url", "ok": True, "latencyMs": 700,
                                      "result": {"status": 200, "chars": 4200}}, 700))
        if scenario == "fail1":
            text = "The auth docs say users log in with a magic link sent over SMS and tokens never expire."  # hallucinated
            steps.append(("llm_output", {"text": text, "tokensIn": 150, "tokensOut": 140, "latencyMs": 1100}, 1100))
            steps.append(("run_finished", {"status": "failed"}, 20))
        else:
            text = ("Auth uses OAuth 2.0: users sign in, get a short-lived access token, "
                    "and refresh it with a refresh token.")
            steps.append(("llm_output", {"text": text, "tokensIn": 300, "tokensOut": 150, "latencyMs": 1200}, 1200))
            steps.append(("run_finished", {"status": "success"}, 20))
    return steps  # always 10 steps (seq 0..9)


def build_events(run_id, scenario, prompt_version, start):
    events, t = [], start
    for seq, (etype, payload, gap_ms) in enumerate(build_steps(scenario, prompt_version)):
        t = t + timedelta(milliseconds=gap_ms)
        events.append({
            "eventId": f"{run_id}-{seq:03d}",  # deterministic, e.g. run_ok_001-003
            "runId": run_id, "projectId": PROJECT, "type": etype,
            "ts": iso(t), "seq": seq, "payload": payload,
        })
    return events


def t0(minute):
    return datetime(2026, 10, 31, 12, minute, 0, tzinfo=timezone.utc)


RUNS = {
    "run_ok_001": build_events("run_ok_001", "ok", "v2", t0(0)),
    "run_fail_001": build_events("run_fail_001", "fail1", "v1", t0(5)),
    "run_fail_002": build_events("run_fail_002", "fail2", "v1", t0(10)),
}
LIVE_EVENTS = build_events("run_live_001", "ok", "v2", t0(15))


# ---------- 2. Mock API responses ----------
def total_tokens(events):
    n = 0
    for e in events:
        p = e["payload"]
        if e["type"] == "prompt":
            n += p["tokens"]
        if e["type"] == "llm_output":
            n += p["tokensIn"] + p["tokensOut"]
    return n


def summarize(events):
    first, last = events[0], events[-1]
    finished = last["type"] == "run_finished"
    return {
        "runId": first["runId"],
        "status": last["payload"]["status"] if finished else "running",
        "startedAt": first["ts"],
        "finishedAt": last["ts"] if finished else None,
        "durationMs": int((parse(last["ts"]) - parse(first["ts"])).total_seconds() * 1000),
        "stepCount": len(events),
        "totalTokens": total_tokens(events),
        "promptVersion": first["payload"]["promptVersion"],
        "model": first["payload"]["model"],
    }


# FAKE comparison, only so the UI has data. The REAL logic is Person A's.
def step_diff(f, s):
    if f is None or s is None:
        return ["missing_step"]
    if f["type"] != s["type"]:
        return ["error"] if f["type"] == "error" else ["different_output"]
    fp, sp, reasons = f["payload"], s["payload"], []
    if f["type"] == "tool_call":
        if fp["tool"] != sp["tool"]:
            reasons.append("different_tool")
        elif fp["args"] != sp["args"]:
            reasons.append("different_args")
    elif f["type"] == "tool_result":
        if fp["ok"] != sp["ok"]:
            reasons.append("error")
        if fp["latencyMs"] > 5000:
            reasons.append("slow_step")
    elif f["type"] == "retrieval":
        if fp["docCount"] != sp["docCount"]:
            reasons.append("missing_retrieval")
    elif f["type"] == "llm_output":
        if fp["text"] != sp["text"]:
            reasons.append("different_output")
    return reasons


def describe_args(a):
    if "query" in a:
        return "an empty query" if a["query"] == "" else f"the query '{a['query']}'"
    return f"args {a}"


def explain(seq, reason, f, s):
    if f is None or s is None:
        return f"At step {seq} one of the runs has no step."
    fp, sp = f["payload"], s["payload"]
    if reason == "different_args":
        return (f"At step {seq} the failed run called {fp['tool']} with {describe_args(fp['args'])}; "
                f"the successful run used {describe_args(sp['args'])}.")
    if reason == "different_tool":
        return f"At step {seq} the failed run called {fp['tool']} but the successful run called {sp['tool']}."
    if reason == "missing_retrieval":
        return f"At step {seq} the failed run retrieved {fp['docCount']} documents; the successful run retrieved {sp['docCount']}."
    if reason == "slow_step":
        return f"At step {seq} the failed run took {fp['latencyMs']}ms; the successful run took {sp['latencyMs']}ms."
    if reason == "error":
        return f"At step {seq} the failed run's {f['type']} step reported a failure, but the successful run's {s['type']} step worked."
    return f"At step {seq} the failed run's output differs from the successful run's output."


SEVERITY = {"different_tool": "error", "different_args": "error", "error": "error",
            "missing_retrieval": "error", "slow_step": "warning",
            "different_output": "warning", "missing_step": "warning"}


def title_for(reason, f, s):
    if reason == "missing_retrieval":
        return f"Retrieval returned {f['payload']['docCount']} documents vs {s['payload']['docCount']}"
    if reason == "slow_step":
        return f"Step took {f['payload']['latencyMs']}ms vs {s['payload']['latencyMs']}ms"
    return reason.replace("_", " ").capitalize()


def build_compare(failed_ev, success_ev):
    fsum, ssum = summarize(failed_ev), summarize(success_ev)
    steps, findings, first = [], [], None
    for seq in range(max(len(failed_ev), len(success_ev))):
        f = failed_ev[seq] if seq < len(failed_ev) else None
        s = success_ev[seq] if seq < len(success_ev) else None
        reasons = step_diff(f, s)
        steps.append({"seq": seq, "failed": f, "success": s,
                      "diverged": bool(reasons), "reasons": reasons})
        for r in reasons:
            text = explain(seq, r, f, s)
            if first is None:
                first = {"seq": seq, "reason": r, "explanation": text}
            findings.append({
                "_id": f"{fsum['runId']}__{ssum['runId']}__{r}__{seq}",
                "failedRunId": fsum["runId"], "successRunId": ssum["runId"],
                "severity": SEVERITY[r], "title": title_for(r, f, s), "detail": text, "seq": seq,
            })
    return {"failedRun": fsum, "successRun": ssum, "steps": steps,
            "firstDivergence": first, "findings": findings}


# ---------- 3. The three modes ----------
def write_json(name, data):
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    (MOCK_DIR / name).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("wrote", name)


def write_mock():
    write_json("projects.json", [{"projectId": PROJECT, "runCount": len(RUNS)}])
    write_json("runs.json", [summarize(ev) for ev in RUNS.values()])
    for run_id, ev in RUNS.items():
        write_json(f"events_{run_id}.json", ev)
    write_json("events_run_live_001.json", LIVE_EVENTS)
    for failed in ("run_fail_001", "run_fail_002"):
        write_json(f"compare_{failed}__run_ok_001.json",
                   build_compare(RUNS[failed], RUNS["run_ok_001"]))


def send(payload):
    import requests  # imported here so the default mode needs no install
    try:
        return requests.post(f"{BASE_URL}/events", json=payload, timeout=10)
    except requests.exceptions.ConnectionError:
        raise SystemExit(f"Could not reach {BASE_URL}. Is the backend running?")


def post_all():
    events = [e for ev in RUNS.values() for e in ev]  # one big batch
    r = send(events)
    print(r.status_code, r.json())


def live():
    for e in LIVE_EVENTS:
        e["ts"] = iso(datetime.now(timezone.utc))  # real "now" so the live view looks live
        r = send(e)
        print(e["eventId"], r.status_code, r.json())
        time.sleep(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="DevTrace sample data generator")
    ap.add_argument("--post", action="store_true", help="send all runs to POST /events")
    ap.add_argument("--live", action="store_true", help="send run_live_001 one event per second")
    args = ap.parse_args()
    if args.post:
        post_all()
    elif args.live:
        live()
    else:
        write_mock()