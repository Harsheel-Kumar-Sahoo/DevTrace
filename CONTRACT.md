# DevTrace API CONTRACT v1

PROJECT: DevTrace. Captures an AI agent's run in MongoDB, compares a failed run
with a successful run, and shows the FIRST step where they diverged, with a
plain-English explanation.

STACK: Python 3.11, FastAPI, pymongo (sync), Uvicorn. Frontend = single page,
plain HTML + Tailwind CDN + vanilla JS. MongoDB Atlas free tier. Windows 11.
Server runs at http://localhost:8000 and also serves the frontend/ folder at "/".

## Event (one document in the `events` collection)
{
  "eventId":  "run_ok_001-003",        // string, unique. Format: "<runId>-<seq padded to 3>"
  "runId":    "run_ok_001",
  "projectId":"research-agent",
  "type":     "tool_call",             // run_started|prompt|retrieval|tool_call|tool_result|llm_output|error|run_finished
  "ts":       "2026-10-31T12:00:03.120Z", // ISO-8601 string in the API; stored as a BSON date
  "seq":      3,                       // integer step number, starts at 0
  "payload":  { ... }                  // flexible, see below
}

## Payload conventions (flexible, but use these keys)
run_started : { "promptVersion":"v1", "model":"mock-llm-1", "task":"Summarize our auth docs" }
prompt      : { "text":"...", "tokens":120 }
retrieval   : { "query":"auth flow", "docCount":5, "docs":[{"id":"d1","title":"..."}] }
tool_call   : { "tool":"search_docs", "args":{ "query":"auth flow" } }
tool_result : { "tool":"search_docs", "ok":true, "latencyMs":800, "result":{...} }
llm_output  : { "text":"...", "tokensIn":300, "tokensOut":150, "latencyMs":1200 }
error       : { "message":"...", "code":"EMPTY_QUERY" }
run_finished: { "status":"success" }   // or "failed"

## Sample data (generator creates these, all in projectId "research-agent")
- run_ok_001   : success, promptVersion "v2", 10 steps (seq 0..9)
- run_fail_001 : failed,  promptVersion "v1", same step layout. FIRST DIVERGENCE
                 is at seq 3: search_docs called with an EMPTY query, then
                 0 documents retrieved, then a hallucinated llm_output.
- run_fail_002 : failed, scenario 2. read_url takes 9000ms and errors.
- run_live_001 : sent one event per second with `--live` (for the live view).

## Endpoints (all JSON)
GET  /health                      -> {"ok":true}

POST /events                      body: ONE event object OR an array of events
  -> 200 {"received":3,"inserted":2,"duplicates":1,
          "rejected":[{"index":2,"eventId":"x","error":"type must be one of [...]"}]}
  -> 400 if body is not valid JSON / not an object or array.
  (Duplicate eventId is NOT an error: it is counted in "duplicates" and ignored.)

GET  /projects                    -> [{"projectId":"research-agent","runCount":3}]
GET  /projects/{projectId}/runs   -> [{"runId","status","startedAt","finishedAt",
                                       "durationMs","stepCount","totalTokens",
                                       "promptVersion","model"}]   // status: success|failed|running
GET  /runs/{runId}/events         -> [event, event, ...] sorted by seq

GET  /compare?failed=<runId>&success=<runId>
  -> {
       "failedRun": {run summary}, "successRun": {run summary},
       "steps": [ { "seq":3, "failed":<event|null>, "success":<event|null>,
                    "diverged":true, "reasons":["different_args"] } , ... ],
       "firstDivergence": { "seq":3, "reason":"different_args",
                            "explanation":"At step 3 the failed run called search_docs with an empty query; the successful run used 'auth flow'." },
       "findings": [ {finding}, ... ]
     }
  reasons allowed: different_tool | different_args | error | missing_retrieval |
                   slow_step | different_output | missing_step

Finding = { "_id":"<failedRunId>__<successRunId>__<rule>__<seq>",  // deterministic
            "failedRunId","successRunId","severity":"error|warning|info",
            "title":"Retrieval returned 0 documents vs 5","detail":"...","seq":5 }

GET  /stream?projectId=research-agent
  -> Server-Sent Events. Each message: "data: <event JSON>\n\n"
  
