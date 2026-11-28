from . import test_profile

# #2793: fixed code for an unusable TestProfile.
TEST_PROFILE_INVALID = 'TEST_PROFILE_INVALID'


def _remote_test_profile():
    """The live default TestProfile, or None when it is unusable.

    Unusable: its provider is missing or raises, or the value is not a
    well-formed TestProfile. The caller then admits nothing; the default
    registry paths are never a fallback. A profile without a unittest entry is
    valid and admits nothing.
    """
    try:
        profile = test_profile.default_profile()
        if type(profile) is not test_profile.TestProfile or not profile.entries:
            return None
        for pattern, kind in profile.entries:
            if (type(pattern) is not re.Pattern or
                    not isinstance(pattern.pattern, str) or
                    not isinstance(kind, str) or kind not in ('unittest', 'script')):
                return None
    except Exception:
        return None
    return profile


def _remote_module_stem(profile, path):
    """Module stem when the profile classifies the whole path as unittest.

    The first matching entry decides (TestProfile.kind_for), so a script path
    is never admitted through a later unittest entry. The remote lowercase
    module-stem constraint applies on top of the profile.
    """
    if profile.kind_for(path) != 'unittest':
        return None
    name = path.rsplit('/', 1)[-1]
    if not name.endswith('.py') or _MODULE.fullmatch(name[:-3]) is None:
        return None
    return name[:-3]


def _declared_module_paths(profile, task):
    """(stem, path) for each declared Task path that names an admitted module."""
    developer = task.get('developer') if type(task.get('developer')) is dict else {}
    pairs = []
    for field in ('read_paths', 'write_paths'):
        paths = developer.get(field)
        if type(paths) is list:
            pairs.extend((_remote_module_stem(profile, path), path)
                         for path in paths if type(path) is str)
    return tuple(pair for pair in pairs if pair[0] is not None)


# #2573: the Darwin-only classifier and its Mac CI baseline cover this directory.
_DEFAULT_TESTS_PREFIX = 'tools/llmtool/tests/'


def _darwin_candidates(profile, task, required, extras):
    """Required stems that the Darwin-only classifier is allowed to judge.

    It reads the default tests directory, so every declared path the profile
    selects for a stem must be there. A PR-changed stem (extras) carries no
    path: it qualifies only when each unittest entry of the profile is a
    default registry entry, so no other directory can have selected it. Any
    doubt keeps the module required.
    """
    registry = {(pattern.pattern, pattern.flags)
                for pattern, kind in test_profile._DEFAULT_PROFILE.entries
                if kind == 'unittest'}
    elsewhere = {stem for stem, path in _declared_module_paths(profile, task)
                 if path != _DEFAULT_TESTS_PREFIX + stem + '.py'}
    if any(kind == 'unittest' and (pattern.pattern, pattern.flags) not in registry
           for pattern, kind in profile.entries):
        elsewhere.update(extras)
    return required - elsewhere


def _pr_changed_test_modules_for_admission(number, head):
    """PR added or changed test modules when exact-head data is readable, else ().

    #2491: remote-test admission only widens with this set. A missing or
    unreadable PR file list is never an admission refusal; the declared modules
    are used instead, while the pre-review EC2 gate fails closed.
    #2793: only a module with a present path that the ordered TestProfile
    classifies as unittest widens admission.
    """
    profile = _remote_test_profile()
    if profile is None:
        return ()
    listed = []

    def read(path):
        value = _github_get(path)
        if path.startswith(f'pulls/{number}/files?') and type(value) is list:
            listed.extend(value)
        return value

    try:
        from . import task_advance
        snapshot = read(f'pulls/{number}')
        modules, code = task_advance._pr_changed_test_modules(
            number, head, read, snapshot if type(snapshot) is dict else None,
            profile=profile)
        if code is not None:
            return ()
        runnable = {_remote_module_stem(profile, item['filename'])
                    for item in listed if item.get('status') != 'removed'}
    except Exception:
        return ()
    return tuple(module for module in modules if module in runnable)
