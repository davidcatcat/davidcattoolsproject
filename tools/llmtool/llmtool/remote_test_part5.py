def _pr_changed_test_modules_for_admission(number, head):
    """PR added or changed test modules when exact-head data is readable, else ().

    #2491: remote-test admission only widens with this set. A missing or
    unreadable PR file list is never an admission refusal; the declared modules
    are used instead, while the pre-review EC2 gate fails closed.
    """
    try:
        from . import task_advance
        snapshot = _github_get(f'pulls/{number}')
        modules, code = task_advance._pr_changed_test_modules(
            number, head, _github_get, snapshot if type(snapshot) is dict else None)
    except Exception:
        return ()
    return modules if code is None else ()
