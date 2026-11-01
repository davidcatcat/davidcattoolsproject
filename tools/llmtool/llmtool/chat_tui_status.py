"""Task list / status projection for the full-screen chat (display only).

Reads persisted TaskStore records and turns them into display text. It never
writes a record, calls a provider or the network, or guesses: a value Core has
no source for is UNKNOWN (field absent) or NOT_AVAILABLE (recorded as null).
Values that the TUI derives from several Core fields (stage, next responsible)
are labelled as derived wherever they are shown.
"""

import json
import re
from datetime import datetime, timezone

from .chat_tui_task import NOT_AVAILABLE, PENDING_DISPATCH, UNKNOWN, retry_condition, value

# Core gates bind PRs to this repository (task_completion, review_authority).
PR_URL = "https://github.com/davidcatcat/davidcattoolsproject/pull/"
LIST_LIMIT = 200  # Newest Tasks shown; matches the task status upper bound.
REFRESH_SECONDS = 15  # Task list re-read interval while shown (local files only).
NAME_WIDTH = 60

# Stage -> who acts next (TUI-derived from the stage; Core stores no owner field).
_NEXT = {
    "PM_INTAKE": "PM (intake reply pending)",
    PENDING_DISPATCH: "PM (Developer dispatch)",
    "RUNNING": "Developer",
    "WAITING_EXECUTOR_EVIDENCE": "Developer / executor evidence",
    "WAITING_PM": "PM",
    "WAITING_REVIEW": "Reviewer",
    "REPAIRING": "Developer (repair)",
    "WAITING_CI": "CI / Integrator",
    "DONE": "none",
    "BLOCKED": "PM (resolve the block)",
    "UNAVAILABLE": "PM / Owner (provider unavailable)",
    "FAILED": "Owner (resubmit)",
}


def _dict(item):
    return item if type(item) is dict else None


def _latest(items):
    """Last dict of a Core list (or a single dict), else None."""
    if type(items) is dict:
        return items
    if type(items) is list and items and type(items[-1]) is dict:
        return items[-1]
    return None


def task_name(record):
    """First line of the request Core stored; Tasks have no separate name field."""
    request = record.get("request") if type(record) is dict else None
    if "request" not in (record or {}):
        return UNKNOWN
    if type(request) is not str or not request.strip():
        return NOT_AVAILABLE
    first = request.strip().splitlines()[0]
    return first if len(first) <= NAME_WIDTH else first[:NAME_WIDTH - 1] + "…"


def _review_handoff(record):
    developer = _dict(record.get("developer")) or {}
    callback = _dict(developer.get("direct_result_callback")) or {}
    return callback, _dict(callback.get("review_handoff")), _dict(callback.get("review_return"))


def _review_ci(record):
    completion = _dict(record.get("completion")) or {}
    evidence = _dict(completion.get("evidence")) or {}
    return completion, _dict(evidence.get("review_pr_ci"))


def _repairing(record):
    """A dispatched/claimed repair newer than any review claim (by head version)."""
    repair = _latest(record.get("review_repair_claims"))
    if repair is None or repair.get("state") not in ("CLAIMED", "DISPATCHED"):
        return None
    reviews = record.get("review_dispatch_claim")
    reviews = [reviews] if type(reviews) is dict else reviews if type(reviews) is list else []
    newer = [r for r in reviews if type(r) is dict and type(r.get("version")) is int
             and type(repair.get("version")) is int and r["version"] > repair["version"]]
    return None if newer else repair


def stage(record):
    """(stage, reason) derived only from recognised persisted values."""
    if type(record) is not dict:
        return UNKNOWN, "record unreadable"
    developer = _dict(record.get("developer"))
    state = record.get("state")
    if developer is None:
        if "developer" not in record:
            return UNKNOWN, "Core record has no developer field"
        return {
            "PM_PENDING": ("PM_INTAKE", "PM_PENDING"),
            "PM_RESPONDED": (PENDING_DISPATCH, "PM_RESPONDED; no Developer dispatch recorded"),
            "PM_UNAVAILABLE": ("UNAVAILABLE", "PM_UNAVAILABLE"),
            "INPUT_REJECTED": ("FAILED", "INPUT_REJECTED"),
            "PM_RESPONSE_REJECTED": ("FAILED", "PM_RESPONSE_REJECTED"),
        }.get(state, (UNKNOWN, "unrecognised Task state " + str(state)))
    blocked = blocked_reason(record)
    if blocked is not None:
        return "BLOCKED", blocked
    if _repairing(record) is not None:
        return "REPAIRING", "review repair claim " + str(_repairing(record).get("state"))
    completion, review_ci = _review_ci(record)
    if completion:
        cstate = completion.get("state")
        if cstate == "WAITING_REVIEW":
            return "WAITING_REVIEW", "completion WAITING_REVIEW"
        if cstate == "WAITING_CI":
            return "WAITING_CI", "completion WAITING_CI"
        if cstate == "WAITING_EXECUTOR":
            return "WAITING_EXECUTOR_EVIDENCE", "completion WAITING_EXECUTOR"
        if cstate in ("WAITING_PM", "PM_EVIDENCE_READY", "PM_DISPATCHING"):
            return "WAITING_PM", "completion " + cstate
        if cstate == "PM_RETURNED":
            decision = (_dict(completion.get("pm_recommendation")) or {}).get("decision")
            if decision == "ACCEPT":
                return "DONE", "completion PM_RETURNED, PM decision ACCEPT"
            return "WAITING_PM", "completion PM_RETURNED, PM decision " + str(decision)
        if cstate == "PM_UNAVAILABLE":
            return "UNAVAILABLE", "completion PM_UNAVAILABLE"
        if cstate == "PM_RESPONSE_REJECTED":
            return "FAILED", "completion PM_RESPONSE_REJECTED"
        return UNKNOWN, "completion " + str(cstate)
    callback, handoff, returned = _review_handoff(record)
    if handoff is not None:
        dispatch = handoff.get("dispatch_state")
        next_step = (returned or handoff).get("next_step")
        if dispatch in ("PENDING", "DISPATCHING"):
            return "WAITING_REVIEW", "review handoff " + dispatch
        if next_step == "INTEGRATION_GATES_PENDING":
            return "WAITING_CI", "review returned; integration gates pending"
        if next_step == "WAIT_FOR_REVIEWER":
            return "WAITING_REVIEW", "next step WAIT_FOR_REVIEWER"
        if next_step == "RETURN_TO_DEVELOPER_ONCE":
            return "WAITING_PM", "review FAIL; return to Developer not yet dispatched"
        if next_step in ("STOP", "STOP_REPAIR_LIMIT"):
            return "BLOCKED", "review next step " + next_step
        return UNKNOWN, "review handoff " + str(dispatch)
    if callback:
        cstate = callback.get("state")
        if cstate in ("WAITING_EXECUTOR", "VERIFYING_EXECUTOR"):
            return "WAITING_EXECUTOR_EVIDENCE", "callback " + cstate
        if cstate in ("WAITING_PM", "PM_EVIDENCE_READY", "PM_DISPATCHING", "PM_RETURNED"):
            return "WAITING_PM", "callback " + cstate
        if cstate == "PM_UNAVAILABLE":
            return "UNAVAILABLE", "callback PM_UNAVAILABLE"
        if cstate == "PM_RESPONSE_REJECTED":
            return "FAILED", "callback PM_RESPONSE_REJECTED"
        return UNKNOWN, "callback " + str(cstate)
    dstate = developer.get("state")
    if dstate in ("DEVELOPER_PENDING", "DEVELOPER_DISPATCHING"):
        return "RUNNING", "developer " + dstate
    if dstate == "DEVELOPER_RESPONDED":
        return "WAITING_EXECUTOR_EVIDENCE", "developer DEVELOPER_RESPONDED; no evidence recorded"
    if dstate == "DEVELOPER_UNAVAILABLE":
        return "UNAVAILABLE", "developer DEVELOPER_UNAVAILABLE"
    if dstate == "DEVELOPER_OUTPUT_REJECTED":
        return "FAILED", "developer DEVELOPER_OUTPUT_REJECTED"
    return UNKNOWN, "developer " + str(dstate)


def blocked_reason(record):
    """Recorded block reason, or None when Core records no block."""
    developer = _dict(record.get("developer")) or {}
    if type(developer.get("blocked_reason")) is str:
        return "Developer: " + developer["blocked_reason"]
    if (developer.get("state") == "DEVELOPER_OUTCOME_UNKNOWN"
            and developer.get("error_code") == "CODEX_BLOCKED"):
        return "Developer outcome unknown (CODEX_BLOCKED)"
    callback = _dict(developer.get("direct_result_callback")) or {}
    gates = callback.get("missing_gates")
    if (callback.get("state") in ("WAITING_EXECUTOR", "VERIFYING_EXECUTOR", "WAITING_PM",
                                  "PM_EVIDENCE_READY", "PM_DISPATCHING")
            and type(gates) is list and gates):
        return "missing gates: " + ", ".join(str(g) for g in gates)
    high_debug = _latest(record.get("high_debug"))
    if high_debug is not None and high_debug.get("status") == "BLOCKED":
        return "High Debug BLOCKED (family " + str(high_debug.get("family")) + ")"
    return None


def high_debug_text(record):
    """The latest High Debug attempt, or None when the Task has none.

    Informational only: a failed attempt (RUN_FAILED) is a tool failure the
    PM may retry or move past, so it does not change the derived stage.
    """
    latest = _latest(record.get("high_debug"))
    if latest is None:
        return None
    status = str(latest.get("status"))
    text = status
    if status == "RUN_FAILED":
        # A failed attempt stores its refusal code as the report.
        code = latest.get("report")
        if type(code) is str and re.fullmatch(r"[A-Z0-9_]{1,64}", code):
            text += " " + code
    return text + " (family " + str(latest.get("family")) + ")"


def recovery(record):
    """What must become true before work continues, as far as Core records it."""
    developer = _dict(record.get("developer")) or {}
    callback = _dict(developer.get("direct_result_callback")) or {}
    gates = callback.get("missing_gates")
    if type(gates) is list and gates:
        return "Core missing_gates must pass: " + ", ".join(str(g) for g in gates)
    retry = retry_condition(record, value(record, "pm_provider_profile"))
    if retry is not None:
        return retry[1] + " (" + retry[0] + ")"
    return NOT_AVAILABLE + " (Core records no recovery condition)"


def reviewer(record):
    """(identity text, latest verdict text) from the review records Core keeps."""
    _callback, handoff, _returned = _review_handoff(record)
    receipt = _dict((handoff or {}).get("receipt"))
    if receipt is not None:
        identity = (value(receipt, "reviewer_identity") + ", task "
                    + value(receipt, "reviewer_task_id") + ", execution "
                    + value(receipt, "execution_status"))
        return identity, value(receipt, "verdict") + " (review receipt)"
    _completion, review_ci = _review_ci(record)
    if review_ci is not None and review_ci.get("stage") != "EXECUTOR_RESULT":
        identity = (value(review_ci, "review_actor") + ", D6 "
                    + value(review_ci, "reviewer_d6_identity"))
        accepted = review_ci.get("formal_review_state") == "ACCEPTED"
        return identity, ("PASS (formal review ACCEPTED)" if accepted
                          else value(review_ci, "formal_review_state"))
    claim = _latest(record.get("review_dispatch_claim"))
    if claim is not None:
        return ("task " + value(claim, "reviewer_task") + ", claim " + value(claim, "state"),
                NOT_AVAILABLE + " (no review receipt recorded)")
    if handoff is not None:
        return ("handoff " + value(handoff, "dispatch_state"),
                NOT_AVAILABLE + " (no review receipt recorded)")
    return NOT_AVAILABLE, NOT_AVAILABLE


def ci(record):
    """CI as recorded; Core stores only NOT_STARTED or a passed required-check set."""
    _completion, review_ci = _review_ci(record)
    if review_ci is None:
        return UNKNOWN + " (Core has no CI record for this Task)"
    if review_ci.get("stage") == "EXECUTOR_RESULT" or review_ci.get("ci_state") == "NOT_STARTED":
        return "NOT_STARTED"
    checks, required = review_ci.get("successful_checks"), review_ci.get("required_checks")
    if (type(checks) is list and type(required) is list and required
            and set(required).issubset(set(checks))):
        return ("PASS (required " + ", ".join(map(str, required)) + "; head "
                + value(review_ci, "ci_head_sha")[:12] + ")")
    return UNKNOWN


def _pr_sources(record):
    """[(number, source label)] for every PR number Core records for this Task."""
    found = []
    for key, label in (("review_dispatch_claim", "review claim"),
                       ("review_repair_claims", "repair claim")):
        items = record.get(key)
        items = [items] if type(items) is dict else items if type(items) is list else []
        found += [(item["pr"], label) for item in items
                  if type(item) is dict and type(item.get("pr")) is int]
    _completion, review_ci = _review_ci(record)
    number = (review_ci or {}).get("pr_number")
    if type(number) is int and 1 <= number <= 999999999:
        found.append((number, "completion review_pr_ci.pr_number"))
    return found


def pull_request(record):
    """(number text, link text) with sources named; disagreeing sources are a CONFLICT."""
    found = _pr_sources(record)
    if not found:
        return NOT_AVAILABLE, NOT_AVAILABLE
    numbers = []
    for number, _label in found:
        if number not in numbers:
            numbers.append(number)
    labels = {n: sorted({label for m, label in found if m == n}) for n in numbers}
    if len(numbers) == 1:
        number = numbers[0]
        return ("#%d (from %s)" % (number, ", ".join(labels[number])), PR_URL + str(number))
    return ("CONFLICT: " + " vs ".join("#%d (from %s)" % (n, ", ".join(labels[n]))
                                       for n in numbers),
            " | ".join(PR_URL + str(n) for n in numbers))


def _usage(usage):
    if type(usage) is not dict:
        return None
    try:
        return "%d+%d=%d" % (usage["prompt_tokens"], usage["completion_tokens"],
                             usage["total_tokens"])
    except (KeyError, TypeError):
        return None


def tokens(record):
    """Provider-reported usage per recorded call; never summed or estimated."""
    developer = _dict(record.get("developer")) or {}
    callback, _handoff, returned = _review_handoff(record)
    completion, _ci_record = _review_ci(record)
    parts = []
    for label, usage in (("PM intake", record.get("usage")), ("Developer", developer.get("usage")),
                         ("PM result", callback.get("pm_usage")),
                         ("PM review return", (returned or {}).get("pm_usage")),
                         ("PM completion", (completion or {}).get("pm_usage"))):
        text = _usage(usage)
        if text is not None:
            parts.append(label + " " + text)
    return "; ".join(parts) if parts else UNKNOWN + " (no provider-reported usage recorded)"


def developer_text(record):
    developer = _dict(record.get("developer"))
    if developer is None:
        return NOT_AVAILABLE + " (no Developer dispatch recorded)"
    execution = _dict(developer.get("execution")) or {}
    return (value(developer, "provider_profile") + " (type " + value(developer, "provider_type")
            + ", model " + value(developer, "model") + "), state " + value(developer, "state")
            + ", execution " + value(execution, "status") + ", session "
            + value(developer, "session_id"))


def pm_text(record):
    provenance = _dict(record.get("pm_provenance"))
    text = (value(record, "pm_provider_profile") + " (type " + value(record, "pm_provider_type")
            + ", model " + value(record, "pm_model") + "), session " + value(record, "pm_session_id"))
    if provenance is not None:
        text += ", runtime " + value(provenance, "pm_runtime")
    return text


def last_event(record):
    event = _latest(record.get("completion_events"))
    if event is None:
        return "none"
    return (value(event, "source") + " " + value(event, "state") + " at "
            + value(event, "received_at") + (" (acked)" if event.get("acked_at") else " (not acked)"))


def row(record):
    """One list row's fields (all strings); an unexpected shape is UNKNOWN, never a crash."""
    try:
        current, _reason = stage(record)
        name = task_name(record)
    except Exception:
        current, name = UNKNOWN, UNKNOWN
    return {"task_id": value(record, "task_id"), "stage": current,
            "updated": value(record, "updated_at"), "name": name}


def summary_text(record):
    """Status block shown above the Core task show view; never raises."""
    try:
        return _summary_text(record)
    except Exception:
        return "Status: " + UNKNOWN + " (record shape not recognised by the TUI)"


def _summary_text(record):
    current, reason = stage(record)
    who, verdict = reviewer(record)
    number, link = pull_request(record)
    blocked = blocked_reason(record)
    lines = [
        "Status (from TaskStore record; stage and next are derived by the TUI)",
        "Task: " + value(record, "task_id") + "  " + task_name(record),
        "Stage: " + current + " (" + reason + ")",
        "Updated: " + value(record, "updated_at") + "; created " + value(record, "created_at"),
        "PM: " + pm_text(record),
        "Developer: " + developer_text(record),
        "Reviewer: " + who,
        "Latest review verdict: " + verdict,
        "CI: " + ci(record),
        "PR: " + number + ("" if link == NOT_AVAILABLE else "  " + link),
        "Tokens (provider-reported): " + tokens(record),
        "Next responsible (derived): " + _NEXT.get(current, UNKNOWN),
        "Last inbox event: " + last_event(record),
    ]
    high_debug = high_debug_text(record)
    if high_debug is not None:
        lines.append("High Debug (latest): " + high_debug)
    if current in ("BLOCKED", "UNAVAILABLE", "FAILED"):
        lines.append("Blocked/failed reason: " + (blocked or reason))
        lines.append("Recovery: " + recovery(record))
    return "\n".join(lines)


def load_records(store):
    """(records newest first, unreadable count); reads every ID like task status."""
    records, unreadable = [], 0
    for task_id in store.task_ids():
        try:
            records.append(store.get(task_id))
        except Exception:
            unreadable += 1
    records.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return records[:LIST_LIMIT], unreadable


# Stages in which PM-dispatched work is running in the background, and who is at work.
BACKGROUND_ROLES = {"RUNNING": "Developer", "WAITING_EXECUTOR_EVIDENCE": "Developer",
                    "REPAIRING": "Developer", "WAITING_REVIEW": "Reviewer"}


def background_items(records):
    """In-flight work of a PM's Tasks, oldest update first: role, Task, PR, state, update."""
    items = []
    for record in records:
        derived, _reason = stage(record)
        role = BACKGROUND_ROLES.get(derived)
        if role is None:
            continue
        numbers = sorted({number for number, _label in _pr_sources(record)})
        items.append({"role": role, "task": str(value(record, "task_id")),
                      "pr": " ".join("#%d" % number for number in numbers),
                      "state": derived, "updated_at": record.get("updated_at")})
    items.sort(key=lambda item: str(item["updated_at"] or ""))
    return items


def read_background(pm_session, store=None):
    """background_items of the Tasks bound to a PM session (the PM inbox's TaskStore read)."""
    from .active_pm_inbox import _session_records, pm_session_key
    from .task_store import TaskStore
    store = TaskStore() if store is None else store
    records, _unreadable = _session_records(store, pm_session_key(pm_session))
    return background_items(records)


def age_text(timestamp, now=None):
    """Compact age of a Core timestamp ("45s", "12m", "3h", "2d"); "?" when unreadable."""
    try:
        when = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return "?"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - when).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return "%d%s" % (seconds // size, unit)
    return "%ds" % seconds


def detail_extra(record):
    """Raw review/repair/CI-related records Core keeps but task show omits."""
    lines = []
    for label, key in (("Review dispatch claims", "review_dispatch_claim"),
                       ("Review repair claims", "review_repair_claims"),
                       ("Remote test outcomes", "remote_test_outcomes")):
        if key in record:
            lines.append(label + ": " + json.dumps(record[key], ensure_ascii=False,
                                                   sort_keys=True, indent=2))
    return "\n".join(lines)


# W59: read-only Task detail opened from the ↓ background panel (non-dev).
DETAIL_EVENTS = 10   # Newest timeline entries shown in that detail.


def read_task_detail(task_id, store=None):
    """The TaskStore record of one Task (reader thread; never written, never polled)."""
    from .task_store import TaskStore
    store = TaskStore() if store is None else store
    return store.get(task_id)


def task_detail_text(record, read_at, reason=None):
    """Compact detail: source and read time, Core state, the TUI summary, newest events.

    ``record`` None shows Core state UNKNOWN with ``reason``; nothing is guessed.
    """
    from .task_status import derive_state
    from .chat_tui_events import format_event, task_events
    lines = ["Task 詳情（唯讀）— Esc 返回對話 · ↑↓ PageUp/PageDown 捲動",
             "來源：TaskStore（本機）｜讀取於 " + read_at]
    if type(record) is not dict:
        lines.append("Core 狀態：" + UNKNOWN + "（" + (reason or "沒有 Task 記錄") + "）")
        return "\n".join(lines)
    try:
        core = derive_state(record)
    except Exception:
        core = UNKNOWN + "（Core 無法從記錄推導狀態）"
    lines.append("Core 狀態：" + str(core) + "（task_status.derive_state）")
    lines += ["", summary_text(record), "",
              "最近事件（最多 %d 筆，新的在前；只讀 TaskStore 記錄，不確認、不輪詢）" % DETAIL_EVENTS]
    try:
        events = task_events(record)[:DETAIL_EVENTS]
    except Exception:
        events = None
    if events is None:
        lines.append("  " + UNKNOWN + "（事件無法從記錄讀出）")
    elif not events:
        lines.append("  沒有事件")
    else:
        lines += ["  " + format_event(event) for event in events]
    return "\n".join(lines)
