"""Task event timelines for the full-screen chat (display only).

Events come from what TaskStore records already hold: completion (inbox) events
and their tombstones, GitHub marker fields, dispatch audit, remote test receipts
and outcomes, review and repair claims, and High Debug entries. Nothing here
writes, reconciles, acknowledges or polls; in particular the inbox read that
records missing events is never called. An event Core would record but has not
yet stored is shown as NOT YET RECORDED and is never presented as stored.
"""

from datetime import datetime, timezone
import re

from .chat_tui_task import UNKNOWN, value
from .worker_dispatch import stored_worker_id

_DISPATCH_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z$")


def _valid_dispatch_timestamp(value) -> bool:
    if type(value) is not str or not _DISPATCH_TIMESTAMP.fullmatch(value):
        return False
    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
        datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        return True
    except ValueError:
        return False

RECENT_LIMIT = 100  # Events shown in the cross-Task pane.


def _items(item):
    """A Core list field (or a single dict) as a list of dicts."""
    if type(item) is dict:
        return [item]
    if type(item) is list:
        return [entry for entry in item if type(entry) is dict]
    return []


def _time(entry, key):
    item = entry.get(key)
    return item if type(item) is str else None


def _event(when, kind, text, recorded=True):
    return {"time": when, "kind": kind, "text": text, "recorded": recorded}


def _completion_events(record):
    events = []
    for item in _items(record.get("completion_events")):
        source = value(item, "source")
        text = (source + " event " + value(item, "state") + ", sequence "
                + value(item, "sequence") + ", source sha " + value(item, "source_sha")[:12])
        if source == "GITHUB":
            text += (" — issue/PR #" + value(item, "issue_number") + ", comment "
                     + value(item, "comment_id") + ", marker " + value(item, "marker_type")
                     + "/" + value(item, "marker_status"))
            if item.get("merge_sha") is not None:
                text += ", merge " + value(item, "merge_sha")[:12]
        events.append(_event(_time(item, "received_at"), "INBOX", text))
        if _time(item, "consumed_at"):
            events.append(_event(item["consumed_at"], "INBOX",
                                 source + " event consumed by the PM"))
        intent = item.get("dispatch_intent")
        if type(intent) is dict and _time(intent, "persisted_at"):
            events.append(_event(intent["persisted_at"], "INBOX",
                                 "PM dispatch intent " + value(intent, "state")))
        if _time(item, "acked_at"):
            events.append(_event(item["acked_at"], "INBOX", source + " event acknowledged"))
    for item in _items(record.get("completion_event_tombstones")):
        events.append(_event(_time(item, "acked_at"), "INBOX",
                             value(item, "source") + " event acknowledged and compacted "
                             "(details no longer stored)"))
    return events


def _not_yet_recorded(record):
    """The inbox event Core would record for a ready PM handoff, if it is missing."""
    try:
        from .active_pm_inbox import _handoff_event
        from .task_submission_cli import _active_pm_handoff
        handoff = _active_pm_handoff(record)
        if handoff is None:
            return []
        event_id = _handoff_event(record, handoff)["event_id"]
    except Exception:
        return []
    known = {item.get("event_id") for item in
             _items(record.get("completion_events"))
             + _items(record.get("completion_event_tombstones"))}
    if event_id in known:
        return []
    return [_event(None, "NOT YET RECORDED",
                   "PM handoff evidence is ready but its inbox event is not stored yet "
                   "(derived from the record; not a stored event)", recorded=False)]


def _other_events(record):
    events = []
    developer = record.get("developer") if type(record.get("developer")) is dict else None
    if developer is not None:
        events.append(_event(_time(developer, "created_at"), "DISPATCH",
                             "Developer assignment recorded: " + value(developer, "provider_profile")
                             + " (model " + value(developer, "model") + "), state "
                             + value(developer, "state")))
    for item in _items(record.get("dispatch_audit")):
        if "worker_key" in item:  # a retired alias (#1791) reads as its current id
            item = dict(item, worker_key=stored_worker_id(item["worker_key"]))
        events.append(_event(_time(item, "at"), "DISPATCH",
                             "dispatch gate " + value(item, "action") + " " + value(item, "decision")
                             + " for " + value(item, "worker_key") + " (" + value(item, "reason_code")
                             + ")"))
    for item in _items(record.get("remote_test_receipts")):
        failures = item.get("failures")
        failed = str(len(failures)) if type(failures) is list else UNKNOWN
        actions = item.get("actions")
        hosted = ""
        if type(actions) is dict:
            run_id = actions.get("run_id")
            if type(run_id) is int and not isinstance(run_id, bool) and 1 <= run_id <= (1 << 53):
                hosted = ", GitHub-hosted run " + value(actions, "run_id")
        events.append(_event(_time(item, "ended_at") or _time(item, "started_at"), "TEST",
                             "remote test receipt " + value(item, "result") + ", exit "
                             + value(item, "exit_code") + ", ran " + value(item, "ran_total")
                             + ", failed modules " + failed + ", sha "
                             + value(item, "source_sha")[:12] + hosted))
    for item in _items(record.get("remote_test_outcomes")):
        events.append(_event(_time(item, "at"), "TEST",
                             "remote test outcome " + value(item, "result") + " ("
                             + value(item, "code") + "), sha " + value(item, "sha")[:12]))
    for item in _items(record.get("remote_test_dispatches")):
        dispatched_at = item.get("dispatched_at")
        if not _valid_dispatch_timestamp(dispatched_at):
            continue
        run = value(item, "run_id") if item.get("run_id") is not None else "pending"
        events.append(_event(dispatched_at, "TEST",
                             "hosted remote-test dispatch " + value(item, "state")
                             + ", run " + run
                             + ", sha " + value(item, "sha")[:12]))
    for item in _items(record.get("review_dispatch_claim")):
        text = ("review claim PR #" + value(item, "pr") + " v" + value(item, "version") + " "
                + value(item, "state") + ", reviewer task " + value(item, "reviewer_task"))
        events.append(_event(_time(item, "claimed_at"), "REVIEW", text + " (claimed)"))
        if _time(item, "updated_at") and item["updated_at"] != item.get("claimed_at"):
            events.append(_event(item["updated_at"], "REVIEW", text + " (updated)"))
    for item in _items(record.get("review_repair_claims")):
        events.append(_event(_time(item, "claimed_at"), "REPAIR",
                             "repair claim PR #" + value(item, "pr") + " v" + value(item, "version")
                             + " attempt " + value(item, "attempt") + " " + value(item, "state")))
    for item in _items(record.get("high_debug")):
        events.append(_event(_time(item, "recorded_at"), "HIGH DEBUG",
                             "High Debug " + value(item, "status") + ", family "
                             + value(item, "family") + ", model " + value(item, "model_observed")))
    return events


def task_events(record):
    """Every event in one record, newest first; untimed entries last."""
    if type(record) is not dict:
        return []
    events = [_event(_time(record, "created_at"), "TASK",
                     "Task recorded (PM state now " + value(record, "state") + ")")]
    for build in (_completion_events, _other_events, _not_yet_recorded):
        try:
            events += build(record)
        except Exception:
            events.append(_event(None, UNKNOWN, "some events could not be read from the record"))
    timed = sorted((e for e in events if e["time"]), key=lambda e: e["time"], reverse=True)
    return timed + [e for e in events if not e["time"]]


def format_event(event, task_id=None):
    when = event["time"] or "time " + UNKNOWN
    task = "" if task_id is None else task_id[:8] + "  "
    return when + "  " + task + "[" + event["kind"] + "] " + event["text"]


def timeline_text(record):
    events = task_events(record)
    lines = ["Events (newest first; from the TaskStore record, nothing acknowledged or polled)"]
    lines += ["  " + format_event(event) for event in events]
    return "\n".join(lines)


def recent_text(records, unreadable, refreshed):
    """Cross-Task recent events pane text."""
    rows = []
    for record in records:
        task_id = value(record, "task_id")
        rows += [(event, task_id) for event in task_events(record)]
    timed = sorted((r for r in rows if r[0]["time"]), key=lambda r: r[0]["time"], reverse=True)
    rows = (timed + [r for r in rows if not r[0]["time"]])[:RECENT_LIMIT]
    lines = ["Recent events across %d Tasks (%d unreadable; refreshed %s; newest %d shown)"
             % (len(records), unreadable, refreshed, RECENT_LIMIT),
             "F5 or r refreshes, Esc closes, Tab focuses for scrolling; read only, no inbox ack/poll"]
    lines += [format_event(event, task_id) for event, task_id in rows]
    if not rows:
        lines.append("no events in TaskStore")
    return "\n".join(lines)
