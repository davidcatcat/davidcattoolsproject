"""Task receipt and detail text for the full-screen chat (display only).

Every value shown comes from the JSON view llmtool Core prints for
``task submit`` / ``task show``; nothing here creates, updates or infers a Task.
A field Core omits is UNKNOWN, a field Core reports as null is NOT_AVAILABLE.
A chat reply never reaches these functions, so it can never become Task evidence.
"""

import json
import re

UNKNOWN = "UNKNOWN"
NOT_AVAILABLE = "NOT_AVAILABLE"
PENDING_DISPATCH = "已建立／待派工"
_TASK_ID = re.compile(r"[0-9a-f]{32}")
# States in which Core accepted the request for PM intake (task_store.STATES).
_INTAKE_OK = frozenset(("PM_PENDING", "PM_RESPONDED"))
# TUI guidance for Core error codes; Core itself returns no PM retry hint.
_RETRY = {
    "PM_PROVIDER_NOT_CONFIGURED":
        "PM profile {profile} must be defined and ready in the user config "
        "(llmtool config path); then submit again",
    "CONFIG_PROVIDER_NOT_READY":
        "PM profile {profile} must have its required provider settings (for example "
        "base_url and model); then submit again",
    "AUTH_CREDENTIAL_MISSING":
        "the API key environment variable named by PM profile {profile} must be set "
        "in the environment that runs llmtool; then submit again",
    "PM_PROVIDER_UNAVAILABLE":
        "PM profile {profile} must be reachable and answer a request; then submit again",
    "NETWORK_PROVIDER_UNREACHABLE":
        "the endpoint of PM profile {profile} must be reachable from this machine "
        "(network, VPN or local service up); then submit again",
    "PROVIDER_TIMEOUT":
        "PM profile {profile} must answer within its configured timeout; then submit again",
    "PROVIDER_RATE_LIMITED":
        "the rate limit of PM profile {profile} must have reset; then submit again",
    "AUTH_PROVIDER_REJECTED":
        "the provider must accept the credential of PM profile {profile}; then submit again",
    "ENV_PROVIDER_RUNTIME_MISSING":
        "the CLI runtime used by PM profile {profile} must be installed and on PATH; "
        "then submit again",
    "PM_INPUT_REJECTED":
        "the request must be non-empty UTF-8 text of at most 64 KiB with no control "
        "characters or credential material; edit it, then submit again",
    "PM_RESPONSE_REJECTED":
        "the PM reply failed Core screening; submit again or choose another PM profile",
}
_FAILED_STATES = frozenset(("PM_UNAVAILABLE", "INPUT_REJECTED", "PM_RESPONSE_REJECTED"))


def parse_core_view(text):
    """The one Task view dict Core printed, else None.

    Every line is checked, so output before or after the view does not hide it.
    Two different Task views are ambiguous and yield None; nothing is guessed.
    """
    views = []
    for line in (text or "").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if (type(value) is dict and type(value.get("task_id")) is str
                and _TASK_ID.fullmatch(value["task_id"]) and value not in views):
            views.append(value)
    return views[0] if len(views) == 1 else None


def error_text(view):
    """Core error_code; null there means the record has no error."""
    if type(view) is dict and "error_code" in view and view["error_code"] is None:
        return "none"
    return value(view, "error_code")


def value(view, key):
    """A Core scalar as text: UNKNOWN when absent, NOT_AVAILABLE when null."""
    if type(view) is not dict or key not in view:
        return UNKNOWN
    item = view[key]
    if item is None:
        return NOT_AVAILABLE
    if type(item) in (str, int):
        return str(item)
    return UNKNOWN


def dispatch_status(view):
    """Developer dispatch as recorded by Core; never claims work is running."""
    if type(view) is not dict or "developer" not in view:
        return UNKNOWN + " (Core view has no developer field)"
    developer = view["developer"]
    if developer is None:
        return PENDING_DISPATCH + " (Core records no Developer dispatch; development has not started)"
    if type(developer) is not dict:
        return UNKNOWN
    return ("Developer record state " + value(developer, "state") + ", provider "
            + value(developer, "provider_profile") + " (as stored by Core; execution result "
            + "not verified here)")


def retry_condition(view, profile):
    """(source, text) describing what must become true before a retry, or None."""
    if type(view) is dict and type(view.get("pm_recovery")) is dict:
        recovery = view["pm_recovery"]
        argv = recovery.get("resume_argv")
        argv_text = " ".join(argv) if type(argv) is list and all(type(a) is str for a in argv) else UNKNOWN
        return ("Core pm_recovery", "resume the PM session: " + argv_text
                + " (cwd " + value(recovery, "cwd") + ")")
    state = value(view, "state")
    if state not in _FAILED_STATES:
        return None
    code = value(view, "error_code")
    hint = _RETRY.get(code)
    if hint is None:
        return ("Core supplies no recovery hint", UNKNOWN + " for error " + code)
    return ("TUI guidance for Core error " + code + "; Core supplies no recovery hint",
            hint.format(profile=profile))


def receipt_text(code, view, profile, raw=None, error=None):
    """Submission result exactly as Core returned it."""
    if view is None:
        lines = ["Task submit result: FAILED (no Task view returned by Core)",
                 "  PM provider profile requested: " + profile,
                 "  Task ID: " + UNKNOWN, "  Run ID: " + UNKNOWN]
        if error is not None:
            lines.append("  Core error: " + error)
        elif raw:
            lines.append("  Core output (unparsed): " + raw)
        lines.append("  No Task receipt exists; nothing was confirmed as created.")
        return "\n".join(lines)
    ok = code == 0 and value(view, "state") in _INTAKE_OK
    lines = [
        "Task submit result: " + ("CREATED" if ok else "FAILED")
        + " (llmtool Core task submit, exit " + str(code) + ")",
        "  PM provider: " + value(view, "pm_provider_profile") + " (type "
        + value(view, "pm_provider_type") + ", model " + value(view, "pm_model") + ")",
        "  PM session: " + value(view, "pm_session_id") + " (backend "
        + value(view, "backend_session_semantics") + ")",
        "  Task ID: " + value(view, "task_id"),
        "  Run ID: " + value(view, "run_id"),
        "  PM state: " + value(view, "state") + "; error: " + error_text(view),
        "  Dispatch: " + dispatch_status(view),
    ]
    retry = retry_condition(view, value(view, "pm_provider_profile"))
    if retry is not None:
        lines.append("  Retry when (" + retry[0] + "): " + retry[1])
    return "\n".join(lines)


def _json(item):
    if item is None:
        return NOT_AVAILABLE
    return json.dumps(item, ensure_ascii=False, sort_keys=True, indent=2)


def detail_text(view):
    """Task detail from a Core ``task show`` view; long text stays scrollable."""
    lines = ["Task detail (llmtool Core task show; F2 toggles, Esc closes)"]
    for label, key in (("Task ID", "task_id"), ("Run ID", "run_id"),
                       ("PM session", "pm_session_id"),
                       ("PM provider profile", "pm_provider_profile"),
                       ("PM provider type", "pm_provider_type"), ("PM model", "pm_model"),
                       ("Backend session", "backend_session_semantics"),
                       ("Native session", "native_session_id"),
                       ("State", "state"),
                       ("Created", "created_at"), ("Updated", "updated_at"),
                       ("Request sha256", "request_sha256"),
                       ("Usage status", "usage_status"), ("Record sha256", "record_sha256")):
        lines.append(label + ": " + value(view, key))
        if key == "state":
            lines.append("Error: " + error_text(view))
    lines.append("Dispatch: " + dispatch_status(view))
    retry = retry_condition(view, value(view, "pm_provider_profile"))
    if retry is not None:
        lines.append("Retry when (" + retry[0] + "): " + retry[1])
    for label, key in (("PM route binding", "pm_route_binding"), ("Usage", "usage"),
                       ("PM assignment", "pm_assignment"), ("Completion", "completion"),
                       ("Developer", "developer")):
        lines.append(label + ": " + (_json(view[key]) if key in view else UNKNOWN))
    for label, key in (("Request (stored by Core)", "request"),
                       ("PM response (stored by Core)", "response")):
        lines.append(label + ":\n" + value(view, key))
    return "\n".join(lines)
