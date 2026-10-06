"""Frozen exact identifier selection; caller supplies validated company anchors."""

import hashlib
import json
from coverage import parse_fixture


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def select(fixture, validated_anchors):
    lines = fixture.splitlines(keepends=True)
    indexes = {lane: {r['identifier']: r['line'] for r in parse_fixture(lane, fixture)}
               for lane in ('L01', 'L02', 'L03')}
    positions = set()
    for anchor in validated_anchors:
        if set(anchor) != {'lane', 'identifier'}:
            raise ValueError('unexpected anchor fields')
        lane, identifier = anchor['lane'], anchor['identifier']
        if lane not in indexes or identifier not in indexes[lane]:
            raise ValueError('unvalidated canonical anchor')
        positions.add(indexes[lane][identifier])
    if len(positions) > 32:
        raise ValueError('SELECTIVE_READ_CAP_INSUFFICIENT: more than 32 pre-merge ranges')
    padded = [(max(1, n - 2), min(len(lines), n + 2)) for n in sorted(positions)]
    merged = []
    for start, end in padded:
        if merged and start - merged[-1][1] - 1 <= 2:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    ranges = [{'start_line': start, 'end_line': end,
               'start_byte': offsets[start - 1], 'end_byte_exclusive': offsets[end]}
              for start, end in merged]
    raw = b''.join(fixture[x['start_byte']:x['end_byte_exclusive']] for x in ranges)
    cap = min(32 * 1024, len(fixture) // 5)
    if len(raw) > cap or raw == fixture:
        raise ValueError('SELECTIVE_READ_CAP_INSUFFICIENT: selected raw exceeds cap or equals full fixture')
    manifest = {
        'fixture_sha256': hashlib.sha256(fixture).hexdigest(),
        'validated_anchors': sorted(validated_anchors, key=lambda x: (x['lane'], x['identifier'])),
        'pre_merge_range_count': len(padded), 'selected_ranges': ranges,
        'selected_raw_bytes': len(raw), 'selected_percentage': 100 * len(raw) / len(fixture),
        'raw_cap_bytes': cap, 'selected_raw_sha256': hashlib.sha256(raw).hexdigest(),
    }
    # Hash canonical manifest excluding this self-referential field.
    manifest['manifest_sha256'] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    return manifest, raw
