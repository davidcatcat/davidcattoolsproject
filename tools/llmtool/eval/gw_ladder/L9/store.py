def _copy_needs(task, dependencies):
    return list(dependencies.get(task, []))


def build_graph(tasks, dependencies):
    """Build each task's list of prerequisites in declaration order."""
    graph = {}
    for task in tasks:
        graph[task] = _copy_needs(task, dependencies)
    return graph
