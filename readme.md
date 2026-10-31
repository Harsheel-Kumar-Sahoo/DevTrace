## MongoDB Design (Backend)

DevTrace stores every AI agent run as a stream of events in MongoDB Atlas, then
compares a failed run against a successful one to find the first step where they
diverged.

### Collections

| Collection | What it stores |
|---|---|
| `events` | One document per agent step (prompt, tool call, retrieval, error...). The source of truth. |
| `runs` | One summary document per run (status, start/finish, step count, tokens). Updated as events arrive. |
| `findings` | Comparison results (e.g. "retrieval returned 0 documents vs 5"). Stored so they can be queried later. |
| `projects` | Created for future use. Project lists are currently computed from `runs`. |

### Schema validation ($jsonSchema)

The `events` collection has a `$jsonSchema` validator (validationAction: error).
Required: `eventId`, `runId`, `projectId`, `type` (enum of 8 values), `ts` (date),
`seq` (int >= 0). The `payload` field is only required to be an object, so each
event type can carry different keys. Structure where it must be strict, flexibility
where agents differ. The API also validates first, to give friendly per-event
error messages; the database validator is the safety net.

### Indexes

| Index | Question it answers |
|---|---|
| unique `{eventId}` | Has this event already been stored? Makes ingestion safe to repeat. |
| `{runId, seq}` | All steps of a run, in order. Used by run view and step-by-step compare. |
| `{projectId, ts}` | Recent activity in a project. Used by the live view. |
| `{runId, type}` | Did this run contain an error or retrieval event? |

### Safe repeated ingestion

Event IDs are `<runId>-<seq>`. Events are written with `insert_many(ordered=False)`,
so MongoDB's unique index rejects duplicates while the valid events in the same batch
are still saved. The run totals (`stepCount`, `totalTokens`) are updated only from
events that were actually inserted, so a duplicate never double counts.

### Aggregation pipelines (GET /compare)

1. Per-run totals: `$match` on the two runs, `$group` by `runId` (events, errors, latency, tokens).
2. Step alignment: `$match` on the two runs, `$group` by `seq` with `$push`, `$sort`.
   Each row holds the failed and successful event for the same step.
3. First divergence: the rules run over the aligned steps in order; the first step with a reason wins.
4. Latency/token comparison per step, stored as findings.

Findings use a deterministic `_id` (`failedRun__successRun__rule__seq`) and are
written with upsert, so calling /compare repeatedly never creates duplicates.

### Change streams (GET /stream)

A change stream on `events` (inserts only, filtered by project) is forwarded to the
browser as Server-Sent Events, so new events appear live. Requires a replica set,
which Atlas provides.