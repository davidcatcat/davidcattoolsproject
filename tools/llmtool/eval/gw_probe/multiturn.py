#!/usr/bin/env python3
"""Bounded multi-turn tool-use probe for the configured company gateway."""

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))
from probe import NoRedirect, ProbeClient, _config, _local_url, _redact, _response, _usage  # noqa: E402

MAX_SECONDS = 3600
CALL_TIMEOUT = 300
TOOL_NAME = "record_project_summary"
TOOL = {"type": "function", "function": {"name": TOOL_NAME,
        "description": "Record the synthetic project's summary.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}},
                       "required": ["summary"], "additionalProperties": False}}}
EXPECTED = "The demo project contains a greeting function."
SYNTHETIC_TASK = ("Read the generated mini project below. Use the available tool to record its summary. "
                  "The summary must be exactly: " + EXPECTED + "\n\n")
SECRET_RE = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
                       r"github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{20,})", re.I)


class StopRun(Exception):
    pass


class Audit:
    def __init__(self, path):
        self.path = Path(path)
        self.path.write_text("", encoding="utf-8")
        os.chmod(self.path, 0o600)
        self.previous = "0" * 64
        self.count = 0

    def add(self, event):
        record = {"sequence": self.count + 1, "previous_hash": self.previous, **event}
        digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=True).encode()).hexdigest()
        record["hash"] = digest
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self.previous, self.count = digest, self.count + 1

    def verify(self):
        previous = "0" * 64
        try:
            for sequence, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                record = json.loads(line)
                digest = record.pop("hash")
                if record.get("sequence") != sequence or record.get("previous_hash") != previous:
                    return False
                actual = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                                  ensure_ascii=True).encode()).hexdigest()
                if digest != actual:
                    return False
                previous = digest
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False


class GuardedRun:
    def __init__(self, client, audit, expected_model, started, max_calls=None, max_tokens=None):
        self.client, self.audit, self.expected_model = client, audit, expected_model
        self.started = started
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.calls = self.tokens = self.connection_errors = 0
        self.scenarios = []
        self.stop_reason = None

    def _scan(self, value):
        text = json.dumps(value, ensure_ascii=True, sort_keys=True)
        key = self.client.key
        return bool(SECRET_RE.search(text) or (key and key in text))

    def send(self, body, stream=False):
        if self._scan(body):
            self.audit.add({"event": "stop", "reason": "CREDENTIAL_SCAN_HIT"})
            raise StopRun("CREDENTIAL_SCAN_HIT")
        if time.monotonic() - self.started >= MAX_SECONDS:
            raise StopRun("TIME_CAP")
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise StopRun("CALL_CAP")
        self.calls += 1
        result = self._send_stream(body) if stream else self.client.send(body=body, timeout=CALL_TIMEOUT)
        status = result.get("status")
        response = result.get("response", {})
        if self._scan(response):
            self.audit.add({"event": "stop", "reason": "CREDENTIAL_SCAN_HIT"})
            raise StopRun("CREDENTIAL_SCAN_HIT")
        served = response.get("model") if isinstance(response, dict) else None
        usage = _usage(response)
        used = usage.get("total_tokens", 0) if usage else 0
        if isinstance(used, int) and not isinstance(used, bool) and used >= 0:
            self.tokens += used
        self.audit.add({"event": "request", "scenario": self.current, "call": self.calls,
                        "stream": bool(stream), "status": status, "error": result.get("error_code"),
                        "tokens": used if isinstance(used, int) and not isinstance(used, bool) and used >= 0 else 0})
        if status in (401, 403):
            raise StopRun("AUTH_HTTP_" + str(status))
        if result.get("error_code") in ("TRANSPORT_ERROR", "TIMEOUT"):
            self.connection_errors += 1
            if self.connection_errors >= 2:
                raise StopRun("CONNECTION_ERRORS")
        else:
            self.connection_errors = 0
        if status == 200 and served != self.expected_model:
            raise StopRun("UNEXPECTED_MODEL")
        if self.max_tokens is not None and self.tokens > self.max_tokens:
            raise StopRun("TOKEN_CAP")
        if time.monotonic() - self.started >= MAX_SECONDS:
            raise StopRun("TIME_CAP")
        return result

    def _send_stream(self, body):
        """Consume OpenAI SSE chunks and assemble tool-call deltas in memory."""
        payload = json.dumps({**body, "stream": True}, ensure_ascii=True, allow_nan=False).encode("utf-8")
        headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
        if self.client.key:
            headers["Authorization"] = "Bearer " + self.client.key
        req = request.Request(self.client.base_url + "/v1/chat/completions", data=payload,
                              headers=headers, method="POST")
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        started = time.monotonic()
        try:
            with opener.open(req, timeout=CALL_TIMEOUT) as response:
                model, usage, content, calls = None, None, "", {}
                total = 0
                while True:
                    line = response.readline(1024 * 1024)
                    if not line:
                        break
                    total += len(line)
                    if total > 8 * 1024 * 1024:
                        return {"status": 200, "error_code": "RESPONSE_TOO_LARGE",
                                "latency_seconds": round(time.monotonic() - started, 3)}
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        break
                    try:
                        chunk = json.loads(data.decode("utf-8"))
                    except (ValueError, UnicodeError):
                        return {"status": 200, "error_code": "INVALID_STREAM",
                                "latency_seconds": round(time.monotonic() - started, 3)}
                    if not isinstance(chunk, dict):
                        continue
                    model, usage = chunk.get("model", model), chunk.get("usage", usage)
                    choices = chunk.get("choices")
                    delta = choices[0].get("delta", {}) if isinstance(choices, list) and choices else {}
                    if isinstance(delta.get("content"), str):
                        content += delta["content"]
                    fragments = delta.get("tool_calls", [])
                    for fragment in fragments if isinstance(fragments, list) else []:
                        if not isinstance(fragment, dict):
                            continue
                        index = fragment.get("index", 0)
                        item = calls.setdefault(index, {"id": "", "type": "function",
                                                        "function": {"name": "", "arguments": ""}})
                        if isinstance(fragment.get("id"), str):
                            item["id"] += fragment["id"]
                        function = fragment.get("function", {})
                        if isinstance(function, dict):
                            for field in ("name", "arguments"):
                                if isinstance(function.get(field), str):
                                    item["function"][field] += function[field]
                parsed = {"model": model, "choices": [{"message": {
                    "content": content or None,
                    "tool_calls": [calls[index] for index in sorted(calls)] or None}}], "usage": usage}
                return {"status": response.status, "response": parsed,
                        "latency_seconds": round(time.monotonic() - started, 3)}
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            return {"status": status, "error_code": "HTTP_" + str(status),
                    "latency_seconds": round(time.monotonic() - started, 3)}
        except (error.URLError, socket.timeout, TimeoutError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            code = "TIMEOUT" if isinstance(reason, (socket.timeout, TimeoutError)) else "TRANSPORT_ERROR"
            return {"status": None, "error_code": code,
                    "latency_seconds": round(time.monotonic() - started, 3)}

    @staticmethod
    def tool_call(result):
        _, _, message = _response(result)
        calls = message.get("tool_calls")
        return calls[0] if isinstance(calls, list) and calls and isinstance(calls[0], dict) else None

    @staticmethod
    def valid_call(call):
        if not call or call.get("type") != "function" or call.get("function", {}).get("name") != TOOL_NAME:
            return False
        try:
            args = json.loads(call["function"]["arguments"])
            return args == {"summary": EXPECTED}
        except (KeyError, TypeError, ValueError):
            return False

    def run_scenario(self, name, action):
        self.current = name
        try:
            passed, detail = action()
        except StopRun as exc:
            item = {"id": name, "passed": False, "detail": "STOPPED_" + str(exc)}
            self.scenarios.append(item)
            self.audit.add({"event": "score", **item})
            raise
        except Exception:
            passed, detail = False, "SCENARIO_ERROR"
        item = {"id": name, "passed": bool(passed), "detail": detail}
        self.scenarios.append(item)
        self.audit.add({"event": "score", **item})


def _tool_result(call):
    return {"role": "tool", "tool_call_id": call.get("id", ""),
            "content": "recorded" if GuardedRun.valid_call(call) else "invalid arguments"}


def _native_scenario(run, *, forced=False, thinking=False, match_id=False):
    body = {"model": run.expected_model, "messages": [{"role": "user", "content": run.task_prompt}],
            "max_tokens": 512, "tools": [TOOL],
            "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}} if forced else "auto"}
    if thinking:
        body["chat_template_kwargs"] = {"enable_thinking": True}
    first = run.send(body)
    call = run.tool_call(first)
    if not run.valid_call(call):
        return False, "EXPECTED_TOOL_CALL_MISSING_OR_INVALID"
    messages = body["messages"] + [{"role": "assistant", "content": None, "tool_calls": [call]},
                                   _tool_result(call)]
    if match_id and messages[-1].get("tool_call_id") != call.get("id"):
        return False, "TOOL_CALL_ID_MISMATCH"
    followup = run.send({"model": run.expected_model, "messages": messages,
                         "max_tokens": 128, "tools": [TOOL], "tool_choice": "none"})
    _, _, message = _response(followup)
    answer = message.get("content")
    return EXPECTED in answer if isinstance(answer, str) else False, "FINAL_SUMMARY_SCORE"


def _stream_scenario(run):
    # OpenAI-compatible gateways may return a complete JSON response for stream=true;
    # the test fixture and real server are both accepted if the accumulated call is valid.
    body = {"model": run.expected_model, "messages": [{"role": "user", "content": run.task_prompt}],
            "max_tokens": 512, "tools": [TOOL], "tool_choice": "auto", "stream": True}
    result = run.send(body, stream=True)
    call = run.tool_call(result)
    if not run.valid_call(call):
        return False, "STREAMED_TOOL_CALL_MISSING_OR_INVALID"
    messages = body["messages"] + [{"role": "assistant", "content": None, "tool_calls": [call]},
                                   _tool_result(call)]
    second = run.send({"model": run.expected_model, "messages": messages, "max_tokens": 128})
    _, _, message = _response(second)
    answer = message.get("content")
    return EXPECTED in answer if isinstance(answer, str) else False, "STREAM_TOOL_ROUND_TRIP_SCORE"


def _text_tool_scenario(run):
    prompt = (run.task_prompt + "\nRespond exactly as TOOL_CALL: " + TOOL_NAME +
              " {\"summary\":\"...\"}")
    first = run.send({"model": run.expected_model, "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 256})
    _, _, message = _response(first)
    content = message.get("content")
    match = re.search(r"TOOL_CALL:\s*" + re.escape(TOOL_NAME) + r"\s*(\{[^\n]*\})", content or "")
    if not match:
        return False, "TEXT_TOOL_CALL_MISSING"
    try:
        args = json.loads(match.group(1))
    except ValueError:
        return False, "TEXT_TOOL_CALL_INVALID"
    if args != {"summary": EXPECTED}:
        return False, "TEXT_TOOL_ARGUMENT_SCORE"
    second = run.send({"model": run.expected_model, "messages": [{"role": "user", "content":
                      "Tool result: recorded. Return the exact summary only: " + EXPECTED}], "max_tokens": 128})
    _, _, message = _response(second)
    answer = message.get("content")
    return EXPECTED in answer if isinstance(answer, str) else False, "TEXT_TOOL_ROUND_TRIP_SCORE"


def _run(client, audit_path, expected_model, max_calls=None, max_tokens=None):
    audit = Audit(audit_path)
    started = time.monotonic()
    run = GuardedRun(client, audit, expected_model, started, max_calls, max_tokens)
    # The project is generated and isolated. Nothing from the checkout is read.
    synthetic = Path(tempfile.mkdtemp(prefix="gw-probe-mini-"))
    os.chmod(synthetic, 0o700)
    (synthetic / "README.txt").write_text("Synthetic demo project: a greeting function.\n", encoding="utf-8")
    (synthetic / "greeting.py").write_text('def greet(name):\n    return "Hello, " + name\n', encoding="utf-8")
    run.task_prompt = SYNTHETIC_TASK + (synthetic / "README.txt").read_text(encoding="utf-8") + \
        (synthetic / "greeting.py").read_text(encoding="utf-8")
    audit.add({"event": "start", "scenario_count": 6, "project": "synthetic-mini-project"})
    actions = (("S1", lambda: _native_scenario(run)),
               ("S2", lambda: _native_scenario(run, forced=True)),
               ("S3", lambda: _native_scenario(run, match_id=True)),
               ("S4", lambda: _stream_scenario(run)),
               ("S5", lambda: _native_scenario(run, thinking=True)),
               ("S6", lambda: _text_tool_scenario(run)))
    try:
        for name, action in actions:
            run.run_scenario(name, action)
    except StopRun as exc:
        run.stop_reason = str(exc)
        audit.add({"event": "stop", "reason": run.stop_reason})
    finally:
        try:
            (synthetic / "README.txt").unlink()
            (synthetic / "greeting.py").unlink()
            synthetic.rmdir()
        except OSError:
            pass
    audit_ok = audit.verify()
    return {"status": "STOPPED" if run.stop_reason else "DONE", "stop_reason": run.stop_reason,
            "scenarios": run.scenarios, "calls": run.calls, "total_tokens": run.tokens,
            "caps": {"calls": max_calls, "total_tokens": max_tokens,
                     "wall_seconds": MAX_SECONDS, "call_timeout_seconds": CALL_TIMEOUT},
            "audit_verified": audit_ok, "audit_entries": audit.count}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="authorize a configured non-loopback gateway run")
    parser.add_argument("--owner-authorization-url")
    parser.add_argument("--base-url", help="loopback-only fake-server endpoint")
    parser.add_argument("--model", help="expected model name for loopback fake-server tests")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--max-calls", type=int, help="optional maximum number of gateway calls")
    parser.add_argument("--max-total-tokens", type=int, help="optional maximum total token usage")
    args = parser.parse_args(argv)
    if args.max_calls is not None and args.max_calls < 1:
        parser.error("--max-calls must be positive")
    if args.max_total_tokens is not None and args.max_total_tokens < 1:
        parser.error("--max-total-tokens must be positive")
    if args.base_url and not _local_url(args.base_url):
        parser.error("--base-url must be a loopback URL")
    if args.live and not args.owner_authorization_url:
        parser.error("--live requires --owner-authorization-url")
    if args.owner_authorization_url and not re.fullmatch(
            r"https://github\.com/[^/]+/[^/]+/issues/\d+#issuecomment-\d+", args.owner_authorization_url):
        parser.error("authorization must be an Owner GitHub issue comment URL")
    if args.base_url:
        if not args.model:
            parser.error("loopback fake-server runs require --model")
        base_url, model, key = args.base_url, args.model, None
    else:
        try:
            base_url, model, key = _config()
        except Exception as exc:
            print(json.dumps({"status": "ERROR", "error": type(exc).__name__}), file=sys.stderr)
            return 2
    if args.model:
        model = args.model
    if not _local_url(base_url) and (not args.live or not args.owner_authorization_url):
        parser.error("non-loopback runs require --live and --owner-authorization-url")
    if args.live and _local_url(base_url):
        parser.error("--live is only for the configured non-loopback gateway")
    output_dir = args.out_dir or Path(tempfile.gettempdir()) / "gw-probe-multiturn"
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_dir, 0o700)
    client = ProbeClient(base_url, model, key)
    result = _run(client, output_dir / "audit.jsonl", model, args.max_calls, args.max_total_tokens)
    result["started_at"] = datetime.now(timezone.utc).isoformat()
    result["audit_path"] = os.fspath(output_dir / "audit.jsonl")
    result = _redact(result, key)
    (output_dir / "multiturn.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n",
                                               encoding="utf-8")
    print(json.dumps({"status": result["status"], "audit_verified": result["audit_verified"],
                      "output": os.fspath(output_dir / "multiturn.json")}))
    return 0 if result["audit_verified"] and result["status"] == "DONE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
