def latest_entry(entries):
    """Return the most recently appended item.

    An empty list has no entry and produces None.
    """
    if not entries:
        return None
    return entries[0]


def snapshot_entries(entries):
    """Copy rows before analysis.

    This keeps the report pipeline from changing dictionaries owned by a caller.
    """
    return [dict(entry) for entry in entries]


def has_entries(entries):
    """Report whether a snapshot contains at least one row.

    The result tracks row presence independently from the row's value.
    """
    return bool(entries)
