#!/usr/bin/python3.9
"""Root-owned, fail-closed admission for the two persistent EC2 runners."""
import json
import os
from pathlib import Path
import pwd
import re
import sys

REPO = 'davidcatcat/davidcattoolsproject'
OWNER = 'davidcatcat'
IDENTITIES = {'ec2-self-runner': 'ghrunner', 'ec2-self-runner-2': 'ghrunner2'}
BRANCHES = {'refs/heads/main', 'refs/heads/feat/issue99-ec2-linux-ci-20261025'}

def reject(reason):
    print('::error::EC2 host admission refused: ' + reason)
    raise SystemExit(1)

def need(condition, reason):
    if not condition:
        reject(reason)

def get(mapping, *parts):
    for part in parts:
        if not isinstance(mapping, dict):
            return None
        mapping = mapping.get(part)
    return mapping

runner = os.environ.get('RUNNER_NAME')
need(runner in IDENTITIES, 'runner identity')
need(pwd.getpwuid(os.geteuid()).pw_name == IDENTITIES[runner], 'service account')
need(os.environ.get('GITHUB_REPOSITORY') == REPO, 'repository')
need(os.environ.get('GITHUB_ACTOR') == OWNER, 'actor')
need(os.environ.get('GITHUB_TRIGGERING_ACTOR', OWNER) == OWNER, 'triggering actor')
need(re.fullmatch(r'[0-9a-f]{40}', os.environ.get('GITHUB_SHA', '')) is not None, 'commit SHA')
workflow_ref = os.environ.get('GITHUB_WORKFLOW_REF', '')
need(workflow_ref.startswith(REPO + '/.github/workflows/llmtool-linux-ci.yml@'), 'workflow')
workflow_git_ref = workflow_ref.rsplit('@', 1)[1]
event_name = os.environ.get('GITHUB_EVENT_NAME')
ref = os.environ.get('GITHUB_REF')
path = Path(os.environ.get('GITHUB_EVENT_PATH', '/nonexistent'))
need(path.is_file() and path.stat().st_size <= 1048576, 'event payload')
try:
    with path.open('r', encoding='utf-8') as stream:
        event = json.load(stream)
except (OSError, ValueError):
    reject('event payload parse')
need(get(event, 'repository', 'full_name') == REPO, 'event repository')
need(get(event, 'sender', 'login') == OWNER, 'event sender')
if event_name == 'push':
    need(ref in BRANCHES and workflow_git_ref == ref, 'push ref')
    need(get(event, 'ref') == ref, 'push event ref')
elif event_name == 'pull_request':
    need(re.fullmatch(r'refs/pull/[0-9]+/merge', ref or '') is not None, 'pull request ref')
    need(workflow_git_ref == ref, 'workflow pull request ref')
    need(get(event, 'pull_request', 'head', 'repo', 'full_name') == REPO, 'fork')
    need(get(event, 'pull_request', 'user', 'login') == OWNER, 'pull request author')
    need(get(event, 'pull_request', 'base', 'ref') == 'main', 'pull request base')
elif event_name == 'workflow_dispatch':
    need(ref in BRANCHES and workflow_git_ref == ref, 'dispatch ref')
else:
    reject('event type')
print('EC2_HOST_ADMISSION_PASS runner=' + runner + ' event=' + event_name)
