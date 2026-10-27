#!/usr/bin/env python3
"""Bounded synthetic stress measurements for the configured company gw."""

import argparse
import concurrent.futures
import json
import math
import os
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# python -I excludes the script directory. Import the established transport and
# parsing helpers from the adjacent probe without importing the llmtool package.
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))
from probe import (  # noqa: E402
    ProbeClient, _answer_matches, _chat, _config, _local_url, _redact,
    _response, _usage,
)


BURST_LEVELS = (1, 8, 16, 32, 64, 128)
BUDGET_LEVELS = (256, 512, 1024, 2048, 4096, 8192)
NEEDLE_LEVELS = (8192, 32768, 65536)
MODES = ("burst", "sustained", "budget", "quality")


def _percentile(values, percentile):
    values = sorted(value for value in values if isinstance(value, (int, float)))
    if not values:
        return None
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return round(values[index], 3)


def _error_code(result):
    if result.get("error_code"):
        return str(result["error_code"])
    status = result.get("status")
    return "HTTP_" + str(status) if status is not None else "UNKNOWN"


def _safe_chat(client, prompt, *, thinking, max_tokens, timeout=None):
    try:
        return _chat(client, [{"role": "user", "content": prompt}], thinking=thinking,
                     max_tokens=max_tokens, timeout=timeout)
    except Exception as exc:
        return {"status": None, "error_code": "REQUEST_ERROR",
                "error_type": type(exc).__name__, "latency_seconds": None}


def _quality_questions():
    questions = []
    for left in range(11, 16):
        questions.append(("What is " + str(left) + " + " + str(left + 7) + "?", str(2 * left + 7)))
    for left in range(2, 7):
        questions.append(("What is " + str(left) + " times " + str(left + 3) + "?", str(left * (left + 3))))
    for start in range(1, 11):
        questions.append(("Continue the sequence " + ", ".join(str(start + 3 * i) for i in range(4)) +
                          ". What is the next number?", str(start + 12)))
    for left in range(1, 11):
        right = left + (1 if left % 2 else -1)
        larger = str(max(left, right))
        questions.append(("Which number is larger, " + str(left) + " or " + str(right) +
                          "? Reply with only the number.", larger))
    for value in range(1, 11):
        if value % 2:
            questions.append(("All nim group " + str(value) + " are vek group " + str(value) +
                              ". All vek group " + str(value) + " are zor group " + str(value) +
                              ". Are all nim group " + str(value) + " zor group " + str(value) +
                              "? Answer yes or no.", "yes"))
        else:
            questions.append(("No glips of type " + str(value) + " are torps. A glip of type " + str(value) +
                              " is an animal. Is that glip a torp? Answer yes or no.", "no"))
    for value in range(1, 11):
        expressions = (("len([" + ", ".join(str(i) for i in range(value)) + "])" , str(value)),
                       ("sum([" + ", ".join(str(i) for i in range(1, value + 1)) + "])" ,
                        str(value * (value + 1) // 2)))
        expression, expected = expressions[(value - 1) % 2]
        questions.append(("In Python, what is the result of " + expression + "? Reply with the number.", expected))
    return questions


def _burst(client, levels, thinking):
    prompt = "A jar has 19 green marbles and 23 yellow marbles. How many marbles are there? Reply with only the number."
    runs = []
    for parallelism in levels:
        start = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as pool:
            results = list(pool.map(lambda _: _safe_chat(client, prompt, thinking=thinking,
                                                          max_tokens=1024), range(parallelism)))
        wall = time.monotonic() - start
        latencies = [r.get("latency_seconds") for r in results
                     if isinstance(r.get("latency_seconds"), (int, float))]
        errors = {}
        ok_count = 0
        completion_tokens = 0
        accurate = 0
        for result in results:
            response, _, message = _response(result)
            if result.get("status") == 200 and not result.get("error_code"):
                ok_count += 1
                accurate += _answer_matches(message.get("content"), "42")
            code = _error_code(result)
            if code != "HTTP_200":
                errors[code] = errors.get(code, 0) + 1
            usage = _usage(response)
            if usage and isinstance(usage.get("completion_tokens"), (int, float)):
                completion_tokens += usage["completion_tokens"]
        runs.append({"parallelism": parallelism, "ok_count": ok_count, "errors": errors,
                     "wall_seconds": round(wall, 3), "latency_p50_seconds": _percentile(latencies, .50),
                     "latency_p95_seconds": _percentile(latencies, .95),
                     "latency_max_seconds": round(max(latencies), 3) if latencies else None,
                     "aggregate_completion_tokens_per_second": round(completion_tokens / wall, 3) if wall else None,
                     "answer_accuracy": accurate, "answer_total": len(results)})
    return {"runs": runs}


SUSTAINED_PROMPTS = (
    ("What is 15 + 27? Reply with only the number.", "42"),
    ("What is 9 times 7? Reply with only the number.", "63"),
    ("What is 81 divided by 9? Reply with only the number.", "9"),
    ("Continue 3, 6, 9. What is the next number? Reply with only the number.", "12"),
    ("Is 14 an even number? Answer yes or no.", "yes"),
)


def _request_record(client, prompt, expected, thinking, deadline, started_at):
    offset = time.monotonic() - started_at
    result = _safe_chat(client, prompt, thinking=thinking, max_tokens=512)
    response, _, message = _response(result)
    usage = _usage(response)
    answer = message.get("content")
    return {"start_offset_seconds": round(offset, 3), "latency_seconds": result.get("latency_seconds"),
            "status": result.get("status"), "error_code": result.get("error_code"),
            "completion_tokens": usage.get("completion_tokens") if usage else None,
            "correct": _answer_matches(answer, expected)}


def _window_summary(records, start, end):
    subset = [record for record in records if start <= record["start_offset_seconds"] < end]
    latencies = [r["latency_seconds"] for r in subset]
    ok = sum(r["status"] == 200 and not r["error_code"] for r in subset)
    errors = len(subset) - ok
    tokens = sum(r["completion_tokens"] for r in subset if isinstance(r["completion_tokens"], (int, float)))
    duration = max(0, min(end, max((r["start_offset_seconds"] + (r["latency_seconds"] or 0) for r in subset), default=end)) - start)
    return {"start_seconds": round(start, 3), "end_seconds": round(end, 3), "count": len(subset),
            "ok_count": ok, "error_count": errors,
            "http_429_count": sum(r["error_code"] == "HTTP_429" for r in subset),
            "latency_p50_seconds": _percentile(latencies, .50),
            "latency_p95_seconds": _percentile(latencies, .95),
            "completion_tokens_per_second": round(tokens / duration, 3) if duration else None}


def _sustained(client, concurrency, duration, window, thinking):
    started_at = time.monotonic()
    deadline = started_at + duration
    records = []
    lock = threading.Lock()
    sequence = 0

    def worker():
        nonlocal sequence
        while time.monotonic() < deadline:
            with lock:
                index = sequence
                sequence += 1
            prompt, expected = SUSTAINED_PROMPTS[index % len(SUSTAINED_PROMPTS)]
            record = _request_record(client, prompt, expected, thinking, deadline, started_at)
            with lock:
                records.append(record)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker) for _ in range(concurrency)]
        for future in futures:
            try:
                future.result()
            except Exception:
                with lock:
                    records.append({"start_offset_seconds": time.monotonic() - started_at,
                                    "latency_seconds": None, "status": None,
                                    "error_code": "WORKER_ERROR", "completion_tokens": None,
                                    "correct": False})
    elapsed = time.monotonic() - started_at
    records.sort(key=lambda record: record["start_offset_seconds"])
    count = len(records)
    ok = sum(r["status"] == 200 and not r["error_code"] for r in records)
    tokens = sum(r["completion_tokens"] for r in records if isinstance(r["completion_tokens"], (int, float)))
    windows = [_window_summary(records, start, min(start + window, elapsed))
               for start in (i * window for i in range(max(1, math.ceil(elapsed / window))))]
    return {"duration_seconds": round(elapsed, 3), "requested_duration_seconds": duration,
            "concurrency": concurrency, "count": count, "ok_count": ok, "error_count": count - ok,
            "http_429_count": sum(r["error_code"] == "HTTP_429" for r in records),
            "completion_tokens": tokens, "completion_tokens_per_second": round(tokens / elapsed, 3) if elapsed else None,
            "latency_p50_seconds": _percentile([r["latency_seconds"] for r in records], .50),
            "latency_p95_seconds": _percentile([r["latency_seconds"] for r in records], .95),
            "windows": windows, "requests": records}


def _budget(client, thinking):
    prompt = ("Solve this step by step: A library receives 6 boxes with 24 books each. It gives away 37 books, "
              "then receives 19 more. How many books does it have? Give the final answer as a number.")
    arithmetic = []
    for limit in BUDGET_LEVELS:
        result = _safe_chat(client, prompt, thinking=thinking, max_tokens=limit, timeout=120)
        response, choice, message = _response(result)
        answer = message.get("content")
        usage = _usage(response)
        reasoning = message.get("reasoning_content")
        arithmetic.append({"max_tokens": limit, "status": result.get("status"), "error_code": result.get("error_code"),
                           "finish_reason": choice.get("finish_reason"),
                           "completion_tokens": usage.get("completion_tokens") if usage else None,
                           "reasoning_content_length": len(reasoning) if isinstance(reasoning, str) else None,
                           "content_empty": not isinstance(answer, str) or not answer.strip(),
                           "correct": _answer_matches(answer, "126")})
    needles = []
    needle = "SYNTHETIC_BUDGET_NEEDLE_C84F2"
    for approximate_tokens in NEEDLE_LEVELS:
        filler = "amber birch cedar dogwood elm fir " * (approximate_tokens // 6 + 1)
        content = ("Read the synthetic note carefully. The secret marker is " + needle + ".\n" +
                   filler[:approximate_tokens * 4] +
                   "\nWhat is the exact secret marker? Reply with only the marker.")
        timeout = 300 if approximate_tokens >= 32768 else 120
        for limit in (512, 4096):
            result = _safe_chat(client, content, thinking=thinking, max_tokens=limit, timeout=timeout)
            response, choice, message = _response(result)
            usage = _usage(response)
            answer = message.get("content")
            needles.append({"approx_prompt_tokens": approximate_tokens, "max_tokens": limit,
                            "status": result.get("status"), "error_code": result.get("error_code"),
                            "needle_found": needle in answer if isinstance(answer, str) else False,
                            "finish_reason": choice.get("finish_reason"),
                            "prompt_tokens": usage.get("prompt_tokens") if usage else None,
                            "content_empty": not isinstance(answer, str) or not answer.strip()})
    correct_limits = [run["max_tokens"] for run in arithmetic if run["correct"] and not run["content_empty"]]
    all_runs = arithmetic + needles
    return {"arithmetic": arithmetic, "needles": needles,
            "smallest_correct_non_empty_max_tokens": min(correct_limits) if correct_limits else None,
            "length_with_empty_content_count": sum(run["finish_reason"] == "length" and run["content_empty"]
                                                    for run in all_runs)}


def _quality_mode(client, mode, parallelism):
    questions = _quality_questions()

    def run(item):
        question, expected = item
        result = _safe_chat(client, question, thinking=mode, max_tokens=4096,
                            timeout=120)
        response, choice, message = _response(result)
        usage = _usage(response)
        answer = message.get("content")
        return {"question": question, "expected": expected,
                "answer": answer[:200] if isinstance(answer, str) else None,
                "correct": _answer_matches(answer, expected), "status": result.get("status"),
                "error_code": result.get("error_code"), "latency_seconds": result.get("latency_seconds"),
                "completion_tokens": usage.get("completion_tokens") if usage else None,
                "finish_reason": choice.get("finish_reason")}

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as pool:
        runs = list(pool.map(run, questions))
    latencies = [r["latency_seconds"] for r in runs
                 if isinstance(r["latency_seconds"], (int, float))]
    finish_counts = {}
    for run_result in runs:
        reason = run_result["finish_reason"] or "UNKNOWN"
        finish_counts[reason] = finish_counts.get(reason, 0) + 1
    return {"accuracy": sum(r["correct"] for r in runs), "total": len(runs),
            "mean_latency_seconds": round(statistics.mean(latencies), 3) if latencies else None,
            "p95_latency_seconds": _percentile(latencies, .95),
            "completion_tokens": sum(r["completion_tokens"] for r in runs
                                      if isinstance(r["completion_tokens"], (int, float))),
            "finish_reason_counts": finish_counts, "runs": runs}


def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def _levels(value):
    try:
        result = tuple(_positive_int(part.strip()) for part in value.split(","))
    except (ValueError, argparse.ArgumentTypeError):
        raise argparse.ArgumentTypeError("use comma-separated positive integers") from None
    if not result:
        raise argparse.ArgumentTypeError("at least one level is required")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="use canned responses; make no network requests")
    parser.add_argument("--base-url", help="loopback-only endpoint override for fake-server tests")
    parser.add_argument("--model", help="model override for loopback fake-server tests")
    parser.add_argument("--thinking", choices=("on", "off", "unset"), default="on")
    parser.add_argument("--only", choices=MODES, action="append", help="run only selected mode(s); repeatable")
    parser.add_argument("--burst-levels", type=_levels, default=BURST_LEVELS,
                        help="comma-separated burst parallelism values")
    parser.add_argument("--concurrency", type=_positive_int, default=32)
    parser.add_argument("--duration", type=_positive_float, default=300)
    parser.add_argument("--window", type=_positive_float, default=30)
    parser.add_argument("--quality-parallel", type=_positive_int, default=8)
    args = parser.parse_args(argv)
    if (args.base_url or args.model) and not args.dry_run and not _local_url(args.base_url):
        parser.error("endpoint/model overrides require --dry-run or a loopback --base-url")
    credential_value = None
    if args.dry_run:
        base_url, model, key = "http://127.0.0.1:1", "canned-model", None
    else:
        try:
            base_url, model, key = _config()
        except Exception as exc:
            print(json.dumps({"status": "ERROR", "error": type(exc).__name__}), file=sys.stderr)
            return 2
        credential_value = key
        if args.base_url:
            base_url = args.base_url.rstrip("/")
        if args.model:
            model = args.model
    client = ProbeClient(base_url, model, key, args.dry_run)
    selected = args.only or list(MODES)
    results = {}
    for name in selected:
        try:
            if name == "burst":
                results[name] = _burst(client, args.burst_levels, args.thinking)
            elif name == "sustained":
                results[name] = _sustained(client, args.concurrency, args.duration, args.window, args.thinking)
            elif name == "budget":
                results[name] = _budget(client, "on")
            elif name == "quality":
                results[name] = {mode: _quality_mode(client, mode, args.quality_parallel)
                                 for mode in ("off", "on")}
        except Exception as exc:
            results[name] = {"error_code": "MODE_ERROR", "error_type": type(exc).__name__}
    output = {"status": "DONE", "model": model, "thinking": args.thinking,
              "started_at": datetime.now(timezone.utc).isoformat(), "dry_run": args.dry_run,
              "modes": results}
    output = _redact(output, credential_value)
    out_dir = args.out_dir or Path(os.getenv("TMPDIR", "/tmp")) / "gw-stress"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "gw_stress.json"
        markdown_path = out_dir / "gw_stress.md"
        json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                             encoding="utf-8")
        lines = ["# Company gw stress test", "", "- Model: " + str(output["model"]),
                 "- Started: " + output["started_at"], "- Thinking setting: " + str(output["thinking"]),
                 "- Dry run: " + str(output["dry_run"]), "", "| Mode | Summary |", "| --- | --- |"]
        for name, value in results.items():
            if name == "burst":
                summary = "; ".join(str(run["parallelism"]) + ": " + str(run["ok_count"]) + "/" +
                                    str(run["parallelism"]) for run in value.get("runs", []))
            elif name == "sustained":
                summary = str(value.get("ok_count", 0)) + "/" + str(value.get("count", 0)) + " requests ok"
            elif name == "budget":
                summary = "smallest correct non-empty max_tokens: " + str(value.get("smallest_correct_non_empty_max_tokens"))
            elif name == "quality":
                summary = "; ".join(mode + ": " + str(stats["accuracy"]) + "/" + str(stats["total"])
                                    for mode, stats in value.items() if isinstance(stats, dict) and "accuracy" in stats)
            else:
                summary = "error: " + str(value.get("error_code", "unknown"))
            lines.append("| " + name + " | " + summary.replace("|", "\\|") + " |")
        lines.extend(["", "All prompts are synthetic. Full measurements are in `gw_stress.json`.", ""])
        markdown_path.write_text("\n".join(lines), encoding="utf-8")
    except (OSError, ValueError):
        print(json.dumps({"status": "ERROR", "error": "OUTPUT_WRITE_ERROR"}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "DONE", "json": os.fspath(json_path), "markdown": os.fspath(markdown_path)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
