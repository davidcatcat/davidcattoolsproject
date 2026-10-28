from helper import matches
from store import prepare_rules


def select_rule(record, rules):
    """Return the name of the first matching rule, or None when absent."""
    prepared = prepare_rules(rules)
    found = [rule for rule in prepared if matches(record, rule)]
    if not found:
        return None
    return found[0]["name"]
