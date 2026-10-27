def is_slug(value):
    if not isinstance(value, str) or not value:
        return False
    if value[0] == "-" or value[-1] == "-" or "--" in value:
        return False
    return all(("a" <= char <= "z") or ("0" <= char <= "9") or char == "-"
               for char in value)
