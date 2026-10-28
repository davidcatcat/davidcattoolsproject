def ready_tasks(graph, completed):
    """Return unfinished tasks whose prerequisites are all complete."""
    ready = [task for task, needs in graph.items()
             if task not in completed and set(needs) <= completed]
    return sorted(ready)
