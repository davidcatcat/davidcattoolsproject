def matches(record, rule):
    """Check whether every condition agrees with the supplied record.

    An empty condition mapping matches any record.
    """
    for field, expected in rule["conditions"].items():
        if record.get(field) != expected:
            return False
    return True
