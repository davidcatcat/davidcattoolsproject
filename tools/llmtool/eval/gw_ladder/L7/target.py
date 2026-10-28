from helper import current_value


def report_latest(history):
    """Build the public latest-value report from a chronological history."""
    candidates = history[1:]
    present, value = current_value(candidates)
    return {"value": value, "present": present}
