"""Task list / status projection for the full-screen chat (display only).

Reads persisted TaskStore records and turns them into display text. It never
writes a record, calls a provider or the network, or guesses: a value Core has
no source for is UNKNOWN (field absent) or NOT_AVAILABLE (recorded as null).
Values that the TUI derives from several Core fields (stage, next responsible)
are labelled as derived wherever they are shown.
"""

import json
import re
import unicodedata
from datetime import datetime, timezone

from . import trust
from .chat_tui_task import NOT_AVAILABLE, PENDING_DISPATCH, UNKNOWN, retry_condition, value

LIST_LIMIT = 200  # Newest Tasks shown; matches the task status upper bound.
# Key of a list row built from the task status index (task_index) instead of a TaskStore
# record; its value is the index's "tui" projection. Core records never use this key.
INDEX_KEY = "_status_index"
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
    "ABANDONED": "none",
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
    if "abandonment" in record:
        # W60: Core's derive_state puts an abandonment before every other field, so the TUI
        # never shows an abandoned Task as running work (detail, ↓ panel, dev list).
        abandonment = _dict(record["abandonment"]) or {}
        return "ABANDONED", ("abandonment recorded at " + value(abandonment, "abandoned_at")
                             + ": " + value(abandonment, "reason"))
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
    published = _dict(record.get("published_pr")) or {}
    number = published.get("pr")
    if type(number) is int and 1 <= number <= 999999999:
        found.append((number, "published_pr.pr"))
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
        url = pr_url()
        return ("#%d (from %s)" % (number, ", ".join(labels[number])),
                NOT_AVAILABLE if url is None else url + str(number))
    url = pr_url()
    links = NOT_AVAILABLE if url is None else " | ".join(url + str(n) for n in numbers)
    return ("CONFLICT: " + " vs ".join("#%d (from %s)" % (n, ", ".join(labels[n]))
                                       for n in numbers),
            links)


def pr_url():
    """Current trusted repository's pull URL prefix, or None when trust is unavailable."""
    try:
        slug = trust.load_trust().repository["slug"]
    except trust.TrustError:
        return None
    return "https://github.com/" + slug + "/pull/"


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


def _indexed(record):
    """The status-index projection of a list row, or None for a full TaskStore record."""
    if type(record) is not dict:
        return None
    summary = record.get(INDEX_KEY)
    return summary if type(summary) is dict else None


def row(record):
    """One list row's fields (all strings); an unexpected shape is UNKNOWN, never a crash."""
    summary = _indexed(record)
    if summary is not None:
        return {"task_id": value(record, "task_id"), "stage": summary["stage"],
                "updated": value(record, "updated_at"), "name": summary["name"]}
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
        "PR: " + number + ("  " + link if link != NOT_AVAILABLE else
                           ("  " + NOT_AVAILABLE if number != NOT_AVAILABLE else "")),
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


_INDEX_FALLBACK = {
    "unavailable": "status index unavailable",
    "missing": "status index not found",
    "unreadable": "status index unreadable",
    "stale": "status index out of date (validator fingerprint)",
}


def index_summary(record):
    """The list row and newest events of one record, as task status stores them in its
    index (task_index "tui"). Raises on a record shape the list cannot project."""
    from .chat_tui_events import task_events
    from .task_index import TUI_EVENT_LIMIT, TUI_TEXT_LIMIT
    current, _reason = stage(record)
    item = row(record)
    developer = _dict(record.get("developer")) or {}
    executor = developer.get("executor_task_id")
    prs = []
    for number, _label in _pr_sources(record):
        if number not in prs:
            prs.append(number)
    events = task_events(record)
    cut = lambda text: (text if len(text) <= TUI_TEXT_LIMIT
                        else text[:TUI_TEXT_LIMIT - 1] + "…")
    updated = record.get("updated_at")
    return {"updated_at": updated if type(updated) is str else None,
            "stage": item["stage"], "name": item["name"],
            "executor": executor if type(executor) is str and executor else None,
            "prs": prs, "owner": _owner_decision(record) is not None,
            "terminal": _terminal(record, current),
            "events": [[event["time"], event["kind"], cut(event["text"]), event["recorded"]]
                       for event in events[:TUI_EVENT_LIMIT]],
            "event_count": len(events)}


def _index_record(task_id, entry, summary):
    record = {"task_id": task_id, "created_at": entry["created_at"], INDEX_KEY: summary}
    if summary["updated_at"] is not None:
        record["updated_at"] = summary["updated_at"]
    return record


def load_list(store):
    """(rows newest first, unreadable count, note or None) for the Task list and events.

    Rows come from the task status index when it is current: no per-record TaskStore.get.
    A record whose index entry is absent or out of date (file stat changed) is read from
    TaskStore. A missing, unreadable or stale index falls back to load_records; the note
    says so. Read only: the index is never written, created or repaired here.
    """
    from . import task_index
    entries, reason = task_index.display_entries(store)
    if entries is None:
        records, unreadable = load_records(store)
        return (records, unreadable, _INDEX_FALLBACK.get(reason, "status index unavailable")
                + "; read every TaskStore record")
    # Rows keep the TaskStore ID order before the sort, like load_records, so rows with
    # the same update time keep the same order as a full read.
    slots, unreadable, stale = [], 0, []
    for task_id in store.task_ids():
        entry = entries.get(task_id)
        if entry is not None and entry["stat"] == task_index.record_key(store, task_id):
            if not entry["readable"]:
                unreadable += 1
                continue
            if "tui" in entry:
                slots.append(_index_record(task_id, entry, entry["tui"]))
                continue
        stale.append(len(slots))
        slots.append(task_id)
    for position in stale:
        try:
            slots[position] = store.get(slots[position])
        except Exception:
            slots[position] = None
            unreadable += 1
    records = [record for record in slots if record is not None]
    records.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    note = None
    if stale:
        note = ("status index out of date for %d Tasks; read those TaskStore records"
                % len(stale))
    return records[:LIST_LIMIT], unreadable, note


def complete_recent_events(store, records):
    """The list rows with a full TaskStore read for each status-index row the recent
    events views (F4, /task events) cannot show completely from the index: a row whose
    dropped older events, or shortened event text, could fall within the newest events
    across Tasks (chat_tui_events.open_rows). Every other row stays the index row, so the
    newest events equal those of a full read of every record. A row whose read fails stays
    the index row and the views say they are incomplete. Read only; with nothing to
    complete, the same list is returned and the store is not touched."""
    from .chat_tui_events import open_rows
    positions = open_rows(records)
    if not positions:
        return records
    completed = list(records)
    for position in positions:
        try:
            completed[position] = store.get(completed[position]["task_id"])
        except Exception:
            pass   # the index row stays; the events views mark the result incomplete
    completed.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return completed


# Stages in which PM-dispatched work is running in the background, and who is at work.
BACKGROUND_ROLES = {"RUNNING": "Developer", "WAITING_EXECUTOR_EVIDENCE": "Developer",
                    "REPAIRING": "Developer", "WAITING_REVIEW": "Reviewer"}


def background_items(records):
    """In-flight work of a PM's Tasks, oldest update first."""
    items = []
    for record in records:
        derived, _reason = stage(record)
        role = BACKGROUND_ROLES.get(derived)
        if role is None:
            continue
        numbers = sorted({number for number, _label in _pr_sources(record)})
        developer = _dict(record.get("developer")) or {}
        if derived == "REPAIRING":
            claim = _latest(record.get("review_repair_claims"))
            started_at = claim.get("claimed_at") if claim is not None else None
        elif derived == "WAITING_REVIEW":
            claim = _latest(record.get("review_dispatch_claim"))
            started_at = claim.get("claimed_at") if claim is not None else None
        else:
            started_at = developer.get("created_at")
            if type(started_at) is not str:
                started_at = record.get("created_at")
        if type(started_at) is not str:
            started_at = None
        items.append({"role": role, "task": str(value(record, "task_id")),
                      "pr": " ".join("#%d" % number for number in numbers),
                      "state": derived, "updated_at": record.get("updated_at"),
                      "started_at": started_at})
    items.sort(key=lambda item: str(item["updated_at"] or ""))
    return items


def background_summary(item, now=None):
    """Compact live summary for one in-flight item; omit elapsed time when unknown."""
    item = _dict(item) or {}
    prs = item.get("pr")
    label = next((word for word in prs.split() if word), "") if type(prs) is str else ""
    if not label:
        task = item.get("task")
        label = task[:8] if type(task) is str else ""
    role = item.get("role")
    doing = {"Developer": "開發中", "Reviewer": "審查中"}.get(role, item.get("state", ""))
    parts = [part for part in (label, doing) if type(part) is str and part]
    started_at = item.get("started_at")
    if type(started_at) is str:
        try:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            seconds = max(0, int((current - started).total_seconds()))
            if seconds < 3600:
                elapsed = "%d:%02d" % divmod(seconds, 60)
            else:
                hours, remainder = divmod(seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                elapsed = "%d:%02d:%02d" % (hours, minutes, seconds)
            parts.append(elapsed)
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
    return " · ".join(parts)


def group_rows(records, now, show_all):
    """Group Task list rows by D6 executor, returning (visible groups, hidden count).

    Input records are expected newest first, as returned by load_records. Records without
    an executor Task ID remain independent rows. The helper is a display projection only.
    """
    grouped, order = {}, []
    for index, record in enumerate(records):
        record_dict = _dict(record) or {}
        summary = _indexed(record_dict)
        if summary is not None:
            executor = summary["executor"]
        else:
            developer = _dict(record_dict.get("developer")) or {}
            executor = developer.get("executor_task_id")
        key = ("d6", executor) if type(executor) is str and executor else ("task", index)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(record)

    visible, hidden = [], 0

    def timestamp(record, field):
        try:
            parsed = datetime.fromisoformat(str((_dict(record) or {}).get(field)).replace(
                "Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None

    for key in order:
        members = grouped[key]
        # Records arrive ordered by update time. A repair can update an older Task after a
        # newer round was created, so choose the head by creation time instead. Iteration
        # order breaks ties and is the fallback when creation timestamps are missing.
        head = members[0]
        newest_created = None
        for member in members:
            created = timestamp(member, "created_at")
            if created is not None and (newest_created is None or created > newest_created):
                head, newest_created = member, created
        head_dict = _dict(head) or {}
        head_summary = _indexed(head_dict)
        if head_summary is not None:
            terminal_state = head_summary["terminal"]
        else:
            # F3 can briefly render placeholder rows while the list is being replaced.
            # Keep those rows independent and render their unknown stage without raising.
            current, _reason = stage(head_dict)
            terminal_state = _terminal(head_dict, current)
        updates = [stamp for stamp in (timestamp(member, "updated_at") for member in members)
                   if stamp is not None]
        latest_update = max(updates) if updates else None
        reference = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        idle = (latest_update is not None
                and (reference.astimezone(timezone.utc) - latest_update).total_seconds()
                > 72 * 60 * 60)
        if not show_all and (terminal_state or idle):
            hidden += 1
            continue

        numbers = []
        for member in members:
            member_dict = _dict(member) or {}
            member_summary = _indexed(member_dict)
            found = (member_summary["prs"] if member_summary is not None
                     else [number for number, _label in _pr_sources(member_dict)])
            for number in found:
                if number not in numbers:
                    numbers.append(number)
        pr = "CONFLICT" if len(numbers) > 1 else ("#%d" % numbers[0] if numbers else "")
        owner = (head_summary["owner"] if head_summary is not None
                 else _owner_decision(head_dict) is not None)
        visible.append({"record": head, "rounds": len(members) - 1, "pr": pr,
                        "owner": owner})
    return visible, hidden


_TERMINAL = {"DONE", "COMPLETED", "ABANDONED", "FAILED"}


def _terminal(record, current):
    """A finished Task (group_rows hides it unless every Task is shown)."""
    from . import task_status
    completion = _dict(record.get("completion")) or {}
    developer = _dict(record.get("developer")) or {}
    return (current in _TERMINAL or task_status.derive_state(record) in _TERMINAL
            or record.get("state") in _TERMINAL
            or completion.get("state") in _TERMINAL
            or developer.get("state") in _TERMINAL)


def read_background(pm_session, store=None):
    """background_items of the Tasks bound to a PM session (the PM inbox's TaskStore read)."""
    from .active_pm_inbox import _session_records, pm_session_key
    from .task_store import TaskStore
    store = TaskStore() if store is None else store
    records, _unreadable = _session_records(store, pm_session_key(pm_session))
    return background_items(records)


def attention_items(records, ready_task_ids=()):
    """Tasks needing PM attention, plus current evidence ready for PM handling."""
    from .task_status import derive_state

    ready_task_ids = set(ready_task_ids)
    labels = {"BLOCKED": "卡住", "UNAVAILABLE": "供應商不可用", "FAILED": "失敗"}
    items = []
    for record in records:
        state = derive_state(record)
        if state in labels:
            items.append({"label": labels[state], "task": str(value(record, "task_id")),
                          "reason": blocked_reason(record) or ""})
        elif state == "RUNNING" and record.get("task_id") in ready_task_ids:
            items.append({"label": "證據已就緒，等 PM 處理",
                          "task": str(value(record, "task_id")), "reason": ""})
    return items


def read_attention(pm_session, store=None):
    """Read attention facts and unread completion events without reconciling or acking."""
    from .active_pm_inbox import _session_records, peek_inbox, pm_session_key
    from .task_store import TaskStore

    store = TaskStore() if store is None else store
    session_key = pm_session_key(pm_session)
    records, unreadable = _session_records(store, session_key)
    inbox = peek_inbox(store, session_key)
    events = inbox.get("events", [])
    pending = inbox.get("pending", [])
    ready_task_ids = {
        entry.get("task_id") for entry in pending
        if type(entry) is dict and entry.get("state") == "NOT_YET_RECORDED"
    }
    ready_task_ids.update(
        event.get("task_id") for event in events
        if type(event) is dict and event.get("current") is True and event.get("state") != "ACKED"
    )
    return {"items": attention_items(records, ready_task_ids),
            "unread": len(events) + len(pending),
            "task_unreadable_count": unreadable,
            "inbox_unreadable_count": inbox.get("unreadable_task_count", 0)}


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


COPY_HINT = " · c 複製 Task ID"   # W60: the detail's header names the key (any width)
COPY_ALL_KEY = "C 複製全文"        # W60: Shift-C copies the whole detail text
COPY_ALL_HINT = " · " + COPY_ALL_KEY
COPY_ALL_STATUS = "C 全文"         # the same key, short enough for the 80-column status bar


def task_detail_text(record, read_at, reason=None, copy_hint=False):
    """Compact detail: source and read time, Core state, the TUI summary, newest events.

    ``record`` None shows Core state UNKNOWN with ``reason``; nothing is guessed.
    ``copy_hint``: the detail has a Task ID that c copies (W60).
    """
    from .task_status import derive_state
    from .chat_tui_events import format_event, task_events
    lines = ["Task 詳情（唯讀）— Esc 返回 · ↑↓ PgUp/PgDn 捲動"   # one row at 80 columns
             + (COPY_HINT if copy_hint else "") + COPY_ALL_HINT]
    lines += task_owner_summary(record)
    lines += ["來源：TaskStore（本機）｜讀取於 " + read_at]
    if type(record) is not dict:
        lines.append("Core 狀態：" + UNKNOWN + "（" + (reason or "沒有 Task 記錄") + "）")
        return "\n".join(lines)
    try:
        core = derive_state(record)
    except Exception:
        core = UNKNOWN + "（Core 無法從記錄推導狀態）"
    lines.append("Core 狀態：" + str(core) + "（task_status.derive_state）")
    # The summary names the Task first; the W60 evidence rows follow it.
    lines += ["", summary_text(record), "", evidence_text(record, read_at), "",
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


_STAGE_MEANING = {
    "PM_INTAKE": "等待 PM 回覆",
    PENDING_DISPATCH: "等待 Developer 派送",
    "RUNNING": "Developer 執行中",
    "WAITING_EXECUTOR_EVIDENCE": "等待執行證據",
    "WAITING_PM": "等待 PM 處理",
    "WAITING_REVIEW": "等待 Reviewer 審查",
    "REPAIRING": "Developer 修復中",
    "WAITING_CI": "等待 CI",
    "DONE": "已完成",
    "BLOCKED": "已阻塞",
    "UNAVAILABLE": "目前無法執行",
    "FAILED": "已失敗",
    "ABANDONED": "已取消",
}


def _owner_decision(record):
    """Explicit Owner hand-off/action field, or None when the record has none."""
    if type(record) is not dict:
        return None
    for source in (record, _dict(record.get("developer")) or {},
                   _dict(record.get("completion")) or {}):
        if "owner_decision" in source:
            decision = source.get("owner_decision")
            if type(decision) is str and decision.strip():
                return "Owner 決定：" + " ".join(decision.split())
            return "不明"
        if source.get("waiting_owner") is True:
            return "請 Owner 提供決定"
        if type(source.get("waiting_owner")) is str and source["waiting_owner"].strip():
            return " ".join(source["waiting_owner"].split())
    return None


def task_owner_summary(record):
    """Three short, Traditional Chinese orientation lines from shown Task fields."""
    if type(record) is not dict:
        current, next_role, action = "不明", "不明", "目前不用"
    else:
        try:
            current, _reason = stage(record)
        except Exception:
            current = UNKNOWN
        try:
            from .task_status import derive_state
            if derive_state(record) == "COMPLETED":
                current = "DONE"
        except Exception:
            pass
        meaning = _STAGE_MEANING.get(current, "不明")
        if current == "BLOCKED" and type(record.get("developer")) is dict and \
                record["developer"].get("state") == "DEVELOPER_OUTCOME_UNKNOWN":
            meaning = "Developer 結果不明"
        owner_action = _owner_decision(record)
        if owner_action is not None:
            next_role = "Owner"
            action = owner_action
        else:
            next_role = {
                "PM_INTAKE": "PM", PENDING_DISPATCH: "PM", "RUNNING": "Developer",
                "WAITING_EXECUTOR_EVIDENCE": "Developer", "WAITING_PM": "PM",
                "WAITING_REVIEW": "Reviewer", "REPAIRING": "Developer", "WAITING_CI": "CI",
                "BLOCKED": "PM", "UNAVAILABLE": "PM", "FAILED": "不明",
                "DONE": "不明", "ABANDONED": "不明",
            }.get(current, "不明")
            action = "目前不用"
        current = meaning
    return ["狀態：" + current, "下一步輪到誰：" + next_role,
            "你需要做：" + action]


# W60: evidence and provenance rows of that detail, from values TaskStore already holds.
EVIDENCE_HEADER = "證據與出處（只列 TaskStore 已記錄的值；TUI 不查 GitHub、不輪詢）"
EVIDENCE_LABELS = ("PR", "PR 狀態", "CI head SHA", "Reviewer 來源", "讀取時間")
_SHA = re.compile(r"[0-9a-f]{7,64}")


def _cells(text):
    """Terminal columns of ``text`` (East Asian wide/full-width characters take two)."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _label(text):
    """``text`` padded so every evidence value starts in the same display column."""
    width = max(_cells(label) for label in EVIDENCE_LABELS)
    return "  " + text + " " * (width - _cells(text)) + "："


def _unknown(reason):
    return UNKNOWN + "（" + reason + "）"


def _pr_numbers(record):
    """PR numbers in first-seen order, each with the Core fields that name it."""
    found = {}
    for number, label in _pr_sources(record):
        labels = found.setdefault(number, [])
        if label not in labels:
            labels.append(label)
    return found


def _pr_evidence(record, review_ci):
    found = _pr_numbers(record)
    if not found:
        return _unknown("Core 沒有記錄這個 Task 的 PR")
    shown = ["#%d（來源 %s）" % (number, "、".join(labels)) for number, labels in found.items()]
    if len(shown) == 1:
        return shown[0]
    # Only wide or narrow characters (no East Asian "ambiguous" ones such as ≠ or —), so the
    # rows keep their width on terminals that draw ambiguous characters wide.
    return "CONFLICT：" + " vs ".join(shown) + "；各來源的 PR 編號不同，照實列出，TUI 不選其一"


def _pr_state_evidence(record, review_ci):
    stored = None
    if review_ci is not None and review_ci.get("stage") != "EXECUTOR_RESULT":
        state, number = review_ci.get("pr_state"), review_ci.get("pr_number")
        if state in ("OPEN", "MERGED") and type(number) is int and 1 <= number <= 999999999:
            stored = "#%d 為 %s（completion review_pr_ci.pr_state，Core 收集證據時的狀態）" % (
                number, state)
    if len(_pr_numbers(record)) > 1:
        return ("CONFLICT（PR 編號不一致，無法判定單一 PR 的狀態）"
                + ("；review_pr_ci 記錄 " + stored if stored else ""))
    events = record.get("completion_events")
    if type(events) is list and any(
            type(event) is dict and event.get("source") == "GITHUB"
            and event.get("marker_type") == "merge" and event.get("marker_status") == "MERGED"
            for event in events):
        # The merge event is newer than evidence collected while the PR was open (Core's
        # derive_state also reads it as COMPLETED), so it comes first.
        return ("MERGED（GitHub merge 事件，completion_events）"
                + ("；review_pr_ci 收集時記錄 " + stored if stored else ""))
    if stored is not None:
        return stored.split(" 為 ", 1)[1]
    return _unknown("Core 只在 review／CI 證據或 GitHub merge 事件中記錄 PR 狀態；這個 Task 還沒有")


def _ci_head_evidence(review_ci):
    if review_ci is None:
        return _unknown("Core 沒有這個 Task 的 CI 記錄")
    if review_ci.get("stage") == "EXECUTOR_RESULT" or review_ci.get("ci_state") == "NOT_STARTED":
        return _unknown("CI 尚未開始：Core 記錄 NOT_STARTED")
    head = review_ci.get("ci_head_sha")
    if type(head) is not str or not _SHA.fullmatch(head):
        return _unknown("CI 記錄中沒有可辨識的 ci_head_sha")
    return head + "（completion review_pr_ci.ci_head_sha）"


def _reviewer_evidence(record, review_ci):
    _callback, handoff, _returned = _review_handoff(record)
    receipt = _dict((handoff or {}).get("receipt"))
    if receipt is not None:
        return ("review receipt（provenance " + value(receipt, "provenance") + "）；reviewer "
                + value(receipt, "reviewer_identity"))
    if review_ci is not None and review_ci.get("stage") != "EXECUTOR_RESULT":
        source = review_ci.get("review_source")
        if source not in ("PR_REVIEW", "PR_COMMENT"):
            return _unknown("CI 記錄中沒有 review_source")
        return ("GitHub " + source + "；actor " + value(review_ci, "review_actor")
                + "（completion review_pr_ci.review_source）")
    if _latest(record.get("review_dispatch_claim")) is not None:
        return _unknown("只有 review claim，沒有 review receipt；claim 不等於 PASS")
    if handoff is not None:
        return _unknown("只有 review handoff，沒有 review receipt")
    return _unknown("Core 沒有 Reviewer 記錄")


def evidence_text(record, read_at):
    """W60 rows: PR (CONFLICT as stored), PR state, CI head, Reviewer source, read time.

    Every value names the Core field it came from; a missing one is UNKNOWN with the reason.
    Never raises: a record shape the TUI does not recognise is UNKNOWN as a whole.
    """
    try:
        _completion, review_ci = _review_ci(record)
        values = (_pr_evidence(record, review_ci), _pr_state_evidence(record, review_ci),
                  _ci_head_evidence(review_ci), _reviewer_evidence(record, review_ci))
    except Exception:
        values = (_unknown("記錄格式 TUI 無法辨識"),) * 4
    values += (read_at + "（本機時間，只讀一次、不自動更新）；記錄更新於 "
               + value(record, "updated_at"),)
    return "\n".join([EVIDENCE_HEADER] + [_label(label) + text
                                          for label, text in zip(EVIDENCE_LABELS, values)])
