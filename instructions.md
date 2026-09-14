# AI Agent Instructions — Distributed Ticket Booking System

These rules apply to **any AI model or agent** (Claude, GPT, Gemini, a coding CLI, etc.) working on this codebase, in any session. They exist so that work is resumable, auditable, and never silently duplicated or lost across sessions or across different agents/tools.

Three files govern the project. Nothing else is a substitute for them:

| File | Purpose |
|---|---|
| `SRS_Distributed_Ticket_Booking_System.md` | The source of truth for *what* the system must do. Never edit requirements here to make a task easier — if a requirement is genuinely wrong, flag it in `logs.md` and ask the human team, don't quietly reinterpret it. |
| `tasks.md` | The current, living breakdown of the SRS into atomic tasks, with status. |
| `logs.md` | The append-only history of what was actually done, in what order, and why. |

---

## 1. Session start protocol (do this every time, before writing any code)

1. Read `SRS_Distributed_Ticket_Booking_System.md` in full.
2. Check whether `logs.md` exists in the repo root.
   - **If `logs.md` does not exist:** treat this as a fresh project. No prior work is assumed to exist, regardless of what files or code you find on disk. Proceed to step 3.
   - **If `logs.md` exists:** read it in full, oldest entry first, to reconstruct exactly what has been done and why. Then read `tasks.md` and cross-check: every task marked done in `tasks.md` must have a matching entry in `logs.md`. If a task is marked done but has no corresponding log entry, do not trust the checkbox — treat that task as unverified and re-inspect the actual code before relying on it.
3. **If code exists on disk but `logs.md` does not** (or is empty): this is an inconsistent state — code with no recorded provenance. Do not silently build on top of it. Options, in order of preference:
   - If the existing code is small and clearly matches an early atomic task, you may keep it, but your first `logs.md` entry must describe exactly what you found, and log it as a reconstructed/adopted task.
   - Otherwise, remove it and restart clean. Removal itself is a task: log what was removed and why *before* deleting it (see Section 4).
4. If `tasks.md` does not exist yet, generate it now from the SRS (see Section 2) before doing anything else.
5. Only after 1–4 are complete: pick the next not-started task from `tasks.md` and begin work.

---

## 2. Turning the SRS into `tasks.md`

An **atomic task** is a single unit of work that:
- Maps to exactly one requirement ID from the SRS (e.g. `FR-BOOK-2`, `FR-RAFT-1`) or one clearly-scoped piece of infrastructure (e.g. "set up proto build pipeline").
- Can be completed, tested, and verified in isolation, without needing half of another task to also be done.
- Is small enough to finish and verify in one focused sitting. If a requirement is too big for that, split it into multiple tasks (e.g. `FR-RAFT-2` might become "implement AppendEntries RPC handler," "implement log-matching consistency check," "implement heartbeat timer" — three atomic tasks, not one).
- Has a concrete, checkable definition of "done" (a test passes, a demo scenario works, a specific behavior is observable) — not a vague goal like "work on Raft."

When generating or updating `tasks.md`, walk every FR, NFR, and milestone deliverable in the SRS and produce at least one atomic task per requirement. Do not skip non-functional requirements (persistence, logging, observability) just because they're less concrete than functional ones — decompose them too.

### `tasks.md` format
```markdown
# Tasks

## M1 — Foundation, gRPC & LLM
- [ ] TASK-AUTH-1 — Implement login/logout RPCs and in-memory session store (FR-AUTH-1, FR-AUTH-2)
- [ ] TASK-AUTH-2 — Enforce token check on all post/get handlers (FR-AUTH-3)
- [ ] TASK-BOOK-1 — Implement single-node BOOK_SEAT with availability check (FR-BOOK-1, FR-BOOK-2)
...

## M2 — Raft Consensus
- [ ] TASK-RAFT-1 — Implement RequestVote handler and election timer (FR-RAFT-1)
...
```

Rules for this file:
- Every checkbox line must include a task ID and the SRS requirement ID(s) it satisfies, in parentheses.
- Never mark a checkbox `[x]` yourself as a shortcut — it is only marked complete at the end of the task, per Section 5.
- If you split, merge, or add a task that isn't a direct 1:1 mapping to the SRS, note why in `logs.md` when you do it.
- `tasks.md` reflects the *current* plan. If a task turns out to be wrong-scoped, edit it — but the edit itself and the reason for it go in `logs.md`.

---

## 3. Working on a task

1. Before writing code, think the task through completely: what exactly needs to change, what could break, what the failure modes are, and how you will verify it's correct. Do not start typing code as a way of figuring out the design — figure out the design first.
2. Check the current, official documentation for any library, API, or protocol involved (gRPC, protobuf, asyncio, Ollama, AWS, etc.) rather than relying on memory. Library APIs and best practices change; verify version-specific behavior before writing code against it.
3. Implement the task.
4. Re-read your own change fully, specifically looking for logical errors (off-by-one in log indices, wrong majority calculation, race conditions, incorrect status codes) and syntax/type errors — do not rely solely on the code running once without crashing.
5. Run the relevant tests (write them first if they don't exist) inside the project's virtual environment (see Section 6).
6. Only once it passes and you've double-checked it, mark the task complete in `tasks.md` and write the log entry (Section 4).

If a task turns out to depend on something not yet built, stop, note the dependency in `logs.md`, add the missing prerequisite as a new task in `tasks.md`, and do that first — don't build around a gap with a stub and forget to come back to it.

---

## 4. `logs.md` — the work log

`logs.md` is **append-only**. Never delete or rewrite a past entry — if something was wrong, add a new entry correcting it, and say so.

For every task you attempt — whether it succeeds, fails, or gets abandoned/restarted — add one entry with:
- Timestamp
- Task ID (matching `tasks.md`)
- **1–2 lines**: what was actually done, and any decision made (e.g. a design choice, a trade-off picked, a library version pinned, a deviation from the SRS and why).

### Format
```markdown
## 2026-09-15 14:20 — TASK-BOOK-1
Implemented BOOK_SEAT with an in-process lock per show to serialize checks before Raft exists. Decision: will replace this lock with the Raft apply-loop once TASK-RAFT-3 lands.

## 2026-09-16 09:05 — TASK-RAFT-2 (restart)
Removed the previous AppendEntries handler — it compared terms incorrectly and accepted stale entries. Rewriting from scratch against the Raft paper's rules before re-implementing.
```

If you are removing existing code to restart a task, the log entry documenting *what is being removed and why* must be written **before** the deletion happens, not reconstructed afterward.

---

## 5. Marking work complete

In `tasks.md`, change `[ ]` to `[x]` only when:
- The task's definition of done is actually met and verified (tests pass, behavior observed).
- A corresponding `logs.md` entry exists for it.

Never mark a task complete because it "should work" or because time is short. An incomplete or uncertain task stays unchecked, with a log entry describing exactly what's missing or uncertain.

---

## 6. Virtual environment rule

All Python execution, testing, and package installation for this project happens inside a virtual environment — never in the system/global Python.

- Create once: `python3 -m venv .venv`
- Activate before any work: `source .venv/bin/activate` (or the OS-appropriate equivalent)
- Install/update dependencies only inside the activated environment, and keep `requirements.txt` in sync with what's actually installed (`pip freeze > requirements.txt` after adding anything).
- Never run `pip install` outside an activated virtual environment.
- If a new session starts and `.venv` doesn't exist yet, creating it is itself a task — log it.

---

## 7. Non-negotiables

- Do not skip the session start protocol, even for what seems like a trivial change.
- Do not treat the absence of `logs.md` as permission to assume prior work — assume nothing until you've checked.
- Do not fabricate or backdate log entries.
- Do not mark tasks complete speculatively.
- Do not silently delete code — log the removal and the reason first.
- Do not guess at library/API behavior when official documentation is available — check it.
- Every piece of work must be fully reasoned through and re-checked for logical and syntax errors before being marked done — "it ran once" is not the same as "it's correct."
