def prepare_rules(rules):
    """Return independent rule rows after checking their shared shape.

    Selection code can then focus on matching and ordering decisions.
    """
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")
    prepared = []
    for rule in rules:
        if (not isinstance(rule, dict) or
                not isinstance(rule.get("name"), str)):
            raise ValueError("each rule needs name and conditions")
        conditions = rule.get("conditions")
        if not isinstance(conditions, dict):
            raise ValueError("each rule needs name and conditions")
        prepared.append(dict(rule))
    return prepared
