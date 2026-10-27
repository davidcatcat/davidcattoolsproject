def _index_of(items, marker):
    for index, item in enumerate(items):
        if item == marker:
            return index
    return -1


def insert_before(items, marker, value):
    result = list(items)
    index = _index_of(result, marker)
    if index:
        result.insert(index, value)
    else:
        result.append(value)
    return result
