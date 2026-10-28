from helper import ready_tasks
from store import build_graph


def schedule(tasks, dependencies):
    """Return a stable topological order for the supplied task graph."""
    graph = build_graph(tasks, dependencies)
    completed = set()
    result = []
    while len(completed) < len(tasks):
        ready = ready_tasks(graph, completed)
        if not ready:
            raise ValueError("dependency cycle")
        task = ready[0]
        completed.add(task)
        result.append(task)
    return result
