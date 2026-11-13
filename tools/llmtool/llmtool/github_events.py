"""Read bounded GitHub activity markers for Tasks owned by one PM session."""

import hashlib
import re
from datetime import datetime, timedelta, timezone

from .errors import LLMToolError
from . import task_completion

MARKER = re.compile(r'<!-- activity: type=([a-z][a-z0-9_]{0,63}) actor=([A-Za-z0-9-]{1,39}) target=([A-Za-z0-9-]{1,39}) status=([A-Z][A-Z0-9_]{0,31}) -->\Z')
MAX_COMMENT_PAGES = 100


def poll(store, pm_session, issues, *, since=None, github_get=None,
         owner_comment_ids=None):
    """Poll explicit issues and PRs, storing metadata only as idempotent events."""
    if (type(issues) is not list or not issues or len(issues) > 50 or
            any(type(n) is not int or not 1 <= n <= 9999999 for n in issues)):
        raise LLMToolError('one or more tracked issue numbers are invalid')
    if owner_comment_ids is not None:
        if (type(owner_comment_ids) not in (list, tuple, set, frozenset) or
                len(owner_comment_ids) > 500 or
                any(type(comment_id) is not int or
                    not 1 <= comment_id <= 2**63 - 1
                    for comment_id in owner_comment_ids)):
            raise LLMToolError('Owner-confirmed comment ids are invalid')
        owner_comment_ids = frozenset(owner_comment_ids)
    if since is not None and (type(since) is not str or
            not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', since)):
        raise LLMToolError('--since must be an ISO UTC timestamp')
    get = task_completion._github_get if github_get is None else github_get
    tasks = [store.get(task_id) for task_id in store.task_ids()]
    tasks = [task for task in tasks if task.get('pm_session_id') == pm_session]
    created = []
    for number in sorted(set(issues)):
        bound = []
        for task in tasks:
            evidence = (task.get('completion') or {}).get('evidence') or {}
            review = evidence.get('review_pr_ci') or {}
            claims = task.get('review_dispatch_claim')
            claims = [claims] if type(claims) is dict else claims if type(claims) is list else []
            claim_bound = any(type(claim) is dict and claim.get('pr') == number
                              for claim in claims)
            if review.get('pr_number') == number or task.get('issue_number') == number or claim_bound:
                bound.append(task)
        if not bound:
            continue
        stream_id = f'github-{pm_session}-{number}'
        prior = [event['received_at'] for task in bound
                 for event in task.get('completion_events', [])
                 if event.get('source') == 'GITHUB' and event.get('session_id') == stream_id]
        cursor = since
        if cursor is None and prior:
            stamp = datetime.strptime(max(prior), '%Y-%m-%dT%H:%M:%SZ').replace(
                tzinfo=timezone.utc) - timedelta(seconds=1)
            cursor = stamp.strftime('%Y-%m-%dT%H:%M:%SZ')
        comments = []
        for page in range(1, MAX_COMMENT_PAGES + 1):
            query = f'per_page=100&page={page}'
            if cursor:
                query += '&since=' + cursor
            batch = get(f'issues/{number}/comments?{query}')
            if type(batch) is not list or len(batch) > 100:
                raise LLMToolError('GitHub comments response is invalid')
            comments.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise LLMToolError('GitHub comments pagination limit reached')
        for comment in comments:
            if type(comment) is not dict or type(comment.get('id')) is not int:
                continue
            body = comment.get('body')
            login = (comment.get('user') or {}).get('login')
            if type(body) is not str or type(login) is not str:
                continue
            lines = body.splitlines()
            marker = next((MARKER.fullmatch(line) for line in lines
                           if MARKER.fullmatch(line)), None)
            if (owner_comment_ids is None or comment['id'] not in owner_comment_ids or
                    marker is None or marker.group(2) != 'Owner' or
                    comment.get('author_association') != 'OWNER'):
                continue
            edited = comment.get('updated_at') != comment.get('created_at')
            if edited:
                continue
            for task in bound:
                event_id = hashlib.sha256(
                    f'GITHUB\0comment\0{comment["id"]}'.encode()).hexdigest()[:32]
                created.append(_record(store, task, stream_id, event_id,
                    number, comment['id'], marker.group(1), marker.group(4), None))
        pr = get(f'pulls/{number}')
        if type(pr) is dict and pr.get('merged') is True:
            merge_sha = pr.get('merge_commit_sha')
            pr_head = pr.get('head')
            pr_head_sha = pr_head.get('sha') if type(pr_head) is dict else None
            if (type(merge_sha) == str and re.fullmatch(r'[0-9a-f]{40}', merge_sha) and
                    type(pr_head_sha) is str and
                    re.fullmatch(r'[0-9a-f]{40}', pr_head_sha)):
                for task in bound:
                    claims = task.get('review_dispatch_claim')
                    claims = [claims] if type(claims) is dict else claims if type(claims) is list else []
                    reviewed_heads = {claim.get('head_sha') for claim in claims
                                      if type(claim) is dict and claim.get('pr') == number and
                                      type(claim.get('head_sha')) is str and
                                      re.fullmatch(r'[0-9a-f]{40}', claim.get('head_sha'))}
                    evidence = (task.get('completion') or {}).get('evidence') or {}
                    result = evidence.get('result') or {}
                    if type(result.get('result_head')) is str:
                        reviewed_heads.add(result['result_head'])
                    if not reviewed_heads:
                        continue
                    matched = False
                    for reviewed_head in reviewed_heads:
                        compare = get(f'compare/{reviewed_head}...{pr_head_sha}')
                        if type(compare) is dict and compare.get('status') in ('ahead', 'identical'):
                            matched = True
                            break
                    if not matched:
                        continue
                    event_id = hashlib.sha256(
                        f'GITHUB\0merge\0{number}\0{merge_sha}'.encode()).hexdigest()[:32]
                    created.append(_record(store, task, stream_id, event_id,
                        number, None, 'merge', 'MERGED', merge_sha))
    return {'pm_session_id': pm_session, 'events': created}


def _record(store, task, session, event_id, issue_number, item_id, marker_type, status, sha):
    # Each durable write changes the task digest and event list. Re-read before
    # deriving the stream position so multiple events in one poll are sequential.
    task = store.get(task['task_id'])
    known = task.get('completion_events', []) + task.get('completion_event_tombstones', [])
    if any(item.get('event_id') == event_id for item in known):
        return next(item for item in known if item.get('event_id') == event_id)
    stream = [item['sequence'] for item in known
              if item.get('source') == 'GITHUB' and item.get('session_id') == session]
    # #1230 A3 B4: SHA-1 only names the event here; it is not a security use.
    source_sha = sha or hashlib.sha1(f'github-comment-{item_id}'.encode(),
                                     usedforsecurity=False).hexdigest()
    metadata = f'{item_id}:{marker_type}:{status}:{sha or ""}'.encode()
    return store.record_completion_event(task['task_id'], event_id=event_id,
        run_id=task['run_id'], session_id=session, source='GITHUB',
        source_sha=source_sha, sequence=max(stream) + 1 if stream else 0,
        payload_sha256=hashlib.sha256(metadata).hexdigest(),
        github_metadata={'issue_number': issue_number, 'comment_id': item_id,
                         'marker_type': marker_type, 'marker_status': status,
                         'merge_sha': sha})['event']
