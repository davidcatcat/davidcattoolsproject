"""Small offline diagnostics CLI adapter; render normalized metadata only."""

import json
import sys

from .diagnostics_reader import DiagnosticsReader, ReaderError, READER_OUTPUT_MAX_BYTES


def add_diagnostics_parser(sub):
    sub.add_parser('runs', help='List saved diagnostics metadata offline; never write')
    trace = sub.add_parser('trace', help='Read one saved diagnostics run offline; never write')
    trace.add_argument('run_id', help='32 lowercase hexadecimal characters; never a path')
    trace.add_argument('--errors', action='store_true', help='show structured error/warning events only')
    trace.add_argument('--json', action='store_true', help='output the stable diagnostics-reader schema')


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _runs_text(value):
    lines = ['run_id=' + row['run_id'] + ' status=' + row['status'] +
             ' started_at=' + (row['started_at'] or 'unknown') +
             ' provider_type=' + (row['provider_type'] or 'unknown') +
             ' final_error_code=' + (row['final_error_code'] or 'none') for row in value['runs']]
    if not lines:
        lines.append('No saved diagnostics runs.')
    if value['damaged_count'] or value['unknown_count']:
        lines.append('Diagnostics entries omitted: damaged=' + str(value['damaged_count']) +
                     ' unknown=' + str(value['unknown_count']) + '.')
    if value['truncated']:
        lines.append('Diagnostics run listing truncated to newest 100 runs.')
    return '\n'.join(lines)


def _trace_text(value):
    lines = ['run_id=' + value['run_id'] + ' state=' + value['state']]
    if value['summary'] is not None:
        summary = value['summary']
        lines.append('status=' + summary['status'] + ' duration_ms=' + str(summary['duration_ms']) +
                     ' final_error_code=' + (summary['final_error_code'] or 'none'))
    for event in value['events']:
        lines.append('event_id=' + str(event['event_id']) + ' timestamp=' + event['timestamp'] +
                     ' type=' + event['type'] + ' component=' + event['component'] +
                     ' severity=' + event['severity'] + ' data=' + _json(event['data']))
    if value['omitted_partial_line']:
        lines.append('Incomplete final event omitted.')
    return '\n'.join(lines)


def run_diagnostics(args):
    try:
        reader = DiagnosticsReader()
        if args.command == 'runs':
            output = _runs_text(reader.runs())
        else:
            value = reader.trace(args.run_id, errors=args.errors)
            output = _json(value) if args.json else _trace_text(value)
        if len((output + '\n').encode('utf-8')) > READER_OUTPUT_MAX_BYTES:
            raise ReaderError('LIMIT_EXCEEDED')
        sys.stdout.write(output + '\n')
        return 0
    except ReaderError as exc:
        print(exc.line(), file=sys.stderr)
        return 1
    except BrokenPipeError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        print(ReaderError('STORE_UNAVAILABLE').line(), file=sys.stderr)
        return 1
