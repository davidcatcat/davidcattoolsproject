from store import has_entries, latest_entry, snapshot_entries


def current_value(history):
    """Return a presence flag and value without conflating a stored None."""
    snapshot = snapshot_entries(history)
    if not has_entries(snapshot):
        return False, None
    return True, _entry_value(latest_entry(snapshot))


def _entry_value(entry):
    """Extract the report field from a copied history row."""
    return entry["value"]
