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


def _row_events(record):
    """(events, kept only the newest few) of a list row: a TaskStore record, or a row
    built from the task status index, which keeps each Task's newest events only."""
    from .chat_tui_status import INDEX_KEY
    summary = record.get(INDEX_KEY) if type(record) is dict else None
    if type(summary) is not dict:
        return task_events(record), False
    events = [_event(when, kind, text, recorded)
              for when, kind, text, recorded in summary["events"]]
    return events, summary["event_count"] > len(events)


def _text_cut(record, event):
    """Whether the status index shortened this event text (an index row only)."""
    from .chat_tui_status import INDEX_KEY
    from .task_index import TUI_TEXT_LIMIT
    return (type(record) is dict and INDEX_KEY in record and
            len(event["text"]) == TUI_TEXT_LIMIT and event["text"].endswith("…"))


def _recent_rows(records, limit):
    """([(event, task_id)] newest first, untimed last, at most ``limit``; the positions in
    ``records`` of status-index rows that may make those rows differ from a full read).

    The selection is global: every row's events are merged, then the newest ``limit`` are
    kept. A status-index row keeps only its Task's newest events; its dropped older events
    all sort after its last kept event, so they can belong in the selection only when that
    last kept event is itself selected (or it kept none). Such rows, and rows with a
    selected event whose text the index shortened, are returned as open: the selection is
    then not known to be complete. Every other dropped event is provably older than the
    selection, so it equals the selection over full records.
    """
    rows, last_kept = [], {}
    for position, record in enumerate(records):
        task_id = value(record, "task_id")
        events, cut = _row_events(record)
        task_rows = [(event, task_id, position) for event in events]
        rows += task_rows
        if cut:
            last_kept[position] = task_rows[-1] if task_rows else None
    timed = sorted((r for r in rows if r[0]["time"]), key=lambda r: r[0]["time"], reverse=True)
    shown = (timed + [r for r in rows if not r[0]["time"]])[:limit]
    shown_ids = {id(row) for row in shown}
    unsure = {position for position, last in last_kept.items()
              if last is None or id(last) in shown_ids}
    unsure.update(position for event, _task_id, position in shown
                  if _text_cut(records[position], event))
    return [(event, task_id) for event, task_id, _position in shown], sorted(unsure)


def open_rows(records, limit=None):
    """Positions in ``records`` of status-index rows that a full TaskStore read must
    replace before the newest ``limit`` (default RECENT_LIMIT, which also covers the
    shorter CONVERSATION_LIMIT) recent events equal a full read of every record."""
    return _recent_rows(records, RECENT_LIMIT if limit is None else limit)[1]


def _partial_text(open_count):
    from .task_index import TUI_EVENT_LIMIT
    return ("INCOMPLETE: %d Tasks not read in full (status index keeps %d per Task); "
            "older events or full event text may be missing" % (open_count, TUI_EVENT_LIMIT))


CONVERSATION_LIMIT = 20  # Events /task events prints into the conversation (#1838).


def conversation_text(records, unreadable, read_at, note=None, limit=CONVERSATION_LIMIT):
    """#1838: what /task events prints into the conversation: the newest events across
    Tasks, or "no events in TaskStore". Read only, like the F4 pane. A selection that is
    not known to equal a full read says so; it is never presented as complete."""
    rows, partial = _recent_rows(records, limit)
    lines = ["Recent events across %d Tasks (%d unreadable; read %s; newest %d shown; "
             "F4 opens the events pane)" % (len(records), unreadable, read_at, len(rows))]
    if note:
        lines.append("  " + note)
    if partial:
        lines.append("  " + _partial_text(len(partial)))
    lines += ["  " + format_event(event, task_id) for event, task_id in rows]
    if not rows:
        lines.append("  no events in TaskStore")
    return "\n".join(lines)


def recent_text(records, unreadable, refreshed, note=None):
    """Cross-Task recent events pane text; ``note`` (status index fallback) follows the
    header lines. A selection not known to be complete says so in the header."""
    from .task_index import TUI_EVENT_LIMIT
    rows, partial = _recent_rows(records, RECENT_LIMIT)
    lines = ["Recent events across %d Tasks (%d unreadable; refreshed %s; newest %d shown%s)"
             % (len(records), unreadable, refreshed, RECENT_LIMIT,
                ", status index keeps %d per Task; INCOMPLETE: %d Tasks not read in full, "
                "older events may be missing" % (TUI_EVENT_LIMIT, len(partial))
                if partial else ""),
             "F5 or r refreshes, Esc closes, Tab focuses for scrolling; read only, no inbox ack/poll"]
    if note:
        lines.append(note)
    lines += [format_event(event, task_id) for event, task_id in rows]
    if not rows:
        lines.append("no events in TaskStore")
    return "\n".join(lines)
