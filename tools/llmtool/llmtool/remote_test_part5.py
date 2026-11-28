from . import test_profile

# #2793: fixed code for an unusable TestProfile.
TEST_PROFILE_INVALID = 'TEST_PROFILE_INVALID'


def _remote_unittest_patterns():
    """Unittest path patterns of the live default TestProfile, or None.

    None means the profile is unusable: its provider is missing or raises, or
    the value is not a well-formed TestProfile. The caller then admits
    nothing; the default registry paths are never a fallback. An empty tuple
    is a valid profile with no unittest entry; script entries are never
    admitted remotely.
    """
    try:
        profile = test_profile.default_profile()
        if type(profile) is not test_profile.TestProfile or not profile.entries:
            return None
        patterns = []
        for pattern, kind in profile.entries:
            if (type(pattern) is not re.Pattern or
                    not isinstance(pattern.pattern, str) or
                    not isinstance(kind, str) or kind not in ('unittest', 'script')):
                return None
            if kind == 'unittest':
                patterns.append(pattern)
    except Exception:
        return None
    return tuple(patterns)


def _remote_module_stem(patterns, path):
    """Module stem when a unittest pattern matches the whole path, else None.

    The remote lowercase module-stem constraint applies on top of the profile.
    """
    if not any(pattern.fullmatch(path) is not None for pattern in patterns):
        return None
    name = path.rsplit('/', 1)[-1]
    if not name.endswith('.py') or _MODULE.fullmatch(name[:-3]) is None:
        return None
    return name[:-3]


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
