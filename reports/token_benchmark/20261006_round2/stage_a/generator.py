"""Frozen synthetic fixtures only: explicit seeds, no grading inputs or network."""

import argparse
from pathlib import Path

CONFIG = {
    'L01': ('L01_large_test', 48 * 1024, 2026100601),
    'L02': ('L02_large_log', 64 * 1024, 2026100602),
    'L03': ('L03_large_context', 128 * 1024, 2026100603),
}


class Numbers:
    """Explicit 32-bit LCG: independent of Python's random-module versions."""

    def __init__(self, seed):
        self.state = seed & 0xffffffff

    def next(self, modulus):
        self.state = (1664525 * self.state + 1013904223) & 0xffffffff
        return self.state % modulus


def targets(lane):
    if lane == 'L01':
        return [
            ('FAILURE test_id=tests/test_manifest.py::test_reject_duplicate_name file=tests/test_manifest.py kind=FAILED signature=DUPLICATE_ACCEPTED\n'
             'E assertion: expected duplicate rejection; observed accepted result\n'
             'UNKNOWN duplicate cause: validator branch and reproduction history are not supplied\n'),
            ('FAILURE test_id=tests/test_manifest.py::test_reject_duplicate_alias file=tests/test_manifest.py kind=FAILED signature=DUPLICATE_ACCEPTED\n'
             'E assertion: expected alias duplicate rejection; observed accepted result\n'
             'UNKNOWN duplicate cause: validator branch and reproduction history are not supplied\n'),
            ('FAILURE test_id=tests/test_worker.py::test_wait_deadline file=tests/test_worker.py kind=FAILED signature=WAIT_TIMEOUT\n'
             'E TimeoutError: worker result not available within synthetic 2.0 s bound\n'
             'UNKNOWN timeout cause: scheduling history and worker progress are not supplied\n'),
            ('FAILURE test_id=tests/test_plugin.py::test_load_optional file=tests/test_plugin.py kind=ERROR signature=OPTIONAL_IMPORT_MISSING\n'
             'E ModuleNotFoundError: synthetic_optional_plugin is unavailable during collection\n'
             'UNKNOWN import cause: intended dependency policy and installation state are not supplied\n'),
        ]
    if lane == 'L02':
        return [
            'EVENT event_id=EVT-T01 request_id=REQ-17 signature=UPSTREAM_TIMEOUT ts=2026-10-06T08:00:20Z\n'
            'DETAIL phase=wait_for_response duration_ms=2000 observed=deadline_exceeded\n'
            'UNKNOWN upstream state and causal chain were not captured\n',
            'EVENT event_id=EVT-T02 request_id=REQ-18 signature=UPSTREAM_TIMEOUT ts=2026-10-06T08:00:50Z\n'
            'DETAIL phase=wait_for_response duration_ms=2000 observed=deadline_exceeded\n'
            'UNKNOWN upstream state and causal chain were not captured\n',
            'EVENT event_id=EVT-T03 request_id=REQ-17 signature=UPSTREAM_TIMEOUT ts=2026-10-06T08:01:20Z\n'
            'DETAIL phase=wait_for_response duration_ms=2000 observed=deadline_exceeded\n'
            'UNKNOWN same request label does not establish retry or causal identity\n',
            'EVENT event_id=EVT-R01 request_id=REQ-19 signature=RECOVERY_OBSERVED ts=2026-10-06T08:01:50Z\n'
            'DETAIL later request completed status=200 latency_ms=84\n'
            'UNKNOWN recovery cause is not observed; later success does not explain earlier timeouts\n',
        ]
    return [
        'RECORD record_id=REC-F01 source=metrics-baseline locator=row-17 topic=upload-window kind=fact text=Baseline upload p95 was 80 ms at 08:00Z.\n',
        'RECORD record_id=REC-F02 source=metrics-current locator=row-23 topic=upload-window kind=fact text=Upload p95 was 240 ms at 08:10Z under the same measurement definition.\n',
        'RECORD record_id=REC-F03 source=release-notes locator=line-42 topic=upload-window kind=fact text=Release R17 completed at 08:05Z; no causal measurement is recorded.\n',
        'RECORD record_id=REC-I01 source=analysis-note locator=note-3 topic=upload-window kind=inference text=The two observed p95 values imply a threefold increase, not a causal attribution.\n',
        'RECORD record_id=REC-H01 source=incident-notes locator=note-9 topic=upload-window kind=hypothesis text=A cache policy change might explain the increase; no cache measurement confirms it.\n',
        'RECORD record_id=REC-U01 source=incident-notes locator=note-10 topic=upload-window kind=unknown text=Traffic mix, cache hit rate and queue depth during the window are unknown.\n',
        'RECORD record_id=REC-V01 source=incident-notes locator=note-11 topic=upload-window kind=verify-next text=Compare traffic mix, cache hit rate and queue depth using the same window boundaries.\n',
    ]


def noise(lane, rng, section, index):
    value = rng.next(997)
    if lane == 'L01':
        if index % 11 == 0:
            return ('tests/test_inventory_%02d.py:%d: DeprecationWarning: synthetic adapter uses legacy marker\n'
                    % (value % 27, 10 + value % 100))
        return ('tests/test_inventory_%02d.py::test_partition_%03d PASSED [%3d%%] duration=0.%03ds\n'
                % (value % 27, value, min(99, section * 25 + index % 25), value))
    if lane == 'L02':
        seconds = section * 30 + min(19, index // 20)
        stamp = '2026-10-06T08:%02d:%02dZ' % (seconds // 60, seconds % 60)
        level, message = ('WARN', 'metric_sample_late nonblocking=true') if index % 17 == 0 else (
            ('ERROR', 'inventory_export_skipped scope=offline_fixture') if index % 29 == 0 else
            ('INFO', 'inventory_read_completed status=200'))
        return ('%s %s service=inventory shard=%02d op_id=OP-%d-%05d %s duration_ms=%d\n'
                % (stamp, level, value % 12, section, index, message, 3 + value % 80))
    return ('RECORD record_id=INV-%d-%05d source=inventory-%02d locator=row-%d topic=inventory kind=observation '
            'text=2026-10-06T07:30Z partition=%02d objects=%d bytes=%d path=synthetic/inventory/batch-%03d.csv\n'
            % (section, index, value % 12, index + 1, value % 27, value, value * 64, value % 100))


def generate(lane, seed=None):
    _, size, default_seed = CONFIG[lane]
    rng = Numbers(default_seed if seed is None else seed)
    blocks = targets(lane)
    header = {
        'L01': '============================= synthetic pytest session ==============================\nplatform synthetic -- Python 3.x; noise tests are ordinary passes and warnings\n',
        'L02': '# Synthetic operational log. Inventory warnings/errors are unrelated observations.\n# Canonical EVENT records preserve timestamp evidence, not a causal explanation.\n',
        'L03': '# Synthetic mixed evidence. Focus: upload-window; inventory records are background.\n# Labels retain observation/inference/hypothesis/unknown distinctions, without external verification.\n',
    }[lane].encode()
    output = bytearray(header)
    for section, block in enumerate(blocks):
        # Put each sparse block near the end of a separate equal-sized region.
        boundary = (section + 1) * (size - 256) // len(blocks)
        target = block.encode()
        index = 0
        while len(output) + len(target) + 160 < boundary:
            line = noise(lane, rng, section, index).encode()
            if len(output) + len(line) + len(target) >= boundary:
                break
            output.extend(line)
            index += 1
        output.extend(target)
    # All semantic lines remain intact; trailing padding is a single explicit comment.
    remaining = size - len(output)
    assert remaining >= 2
    output.extend(b'#' + b'.' * (remaining - 2) + b'\n')
    assert len(output) == size
    return bytes(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    for lane, (directory, _, _) in CONFIG.items():
        destination = args.output / directory
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'fixture.txt').write_bytes(generate(lane))


if __name__ == '__main__':
    main()
