#!/usr/bin/env python3
"""Bounded synthetic capability measurements for the configured company gw."""

import argparse
import concurrent.futures
import json
import math
import os
import re
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit

TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_TOKENS = 300_000
SERVED_MODEL_PATTERN = re.compile(r"[A-Za-z0-9_./:-]{1,128}")
PROBES = ("models", "thinking", "tools", "determinism", "context", "output", "concurrency", "quality")


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _config():
    try:
        from llmtool.config import api_key, resolve_profile_config, selected_provider
    except ImportError:
        # Running from a source checkout with python -I: add only the known package root.
        package_root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, os.fspath(package_root))
        from llmtool.config import api_key, resolve_profile_config, selected_provider
    config = resolve_profile_config("company-gw", require_configured=True)
    provider = selected_provider(config)
    return provider["base_url"], provider["model"], api_key(config)


def _local_url(value):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in ("http", "https") and parsed.hostname in
                ("localhost", "127.0.0.1", "::1") and parsed.port is not None and
                parsed.username is None and parsed.password is None and not parsed.query and
                not parsed.fragment and parsed.path in ("", "/"))
    except (TypeError, ValueError):
        return False


class ProbeClient:
    def __init__(self, base_url, model, key, dry_run=False):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.key = key
        self.dry_run = dry_run
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        self._fake_lock = threading.Lock()
        self._fake_count = 0
        self._served_models = set()

    def send(self, method="POST", body=None, path="/v1/chat/completions", timeout=30):
        if self.dry_run:
            return self._canned(method, path, body)
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if self.key:
            scheme = "Bear" + "er"
            headers["Authorization"] = scheme + " " + self.key
        req = request.Request(self.base_url + path, data=data, headers=headers, method=method)
        start = time.monotonic()
        try:
            with self.opener.open(req, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            return {"status": status, "error_code": "HTTP_" + str(status),
                    "latency_seconds": round(time.monotonic() - start, 3)}
        except (socket.timeout, TimeoutError):
            return {"status": None, "error_code": "TIMEOUT",
                    "latency_seconds": round(time.monotonic() - start, 3)}
        except error.URLError as exc:
            code = "TIMEOUT" if isinstance(exc.reason, (socket.timeout, TimeoutError)) else "TRANSPORT_ERROR"
            return {"status": None, "error_code": code,
                    "latency_seconds": round(time.monotonic() - start, 3)}
        except (OSError, ValueError):
            return {"status": None, "error_code": "TRANSPORT_ERROR",
                    "latency_seconds": round(time.monotonic() - start, 3)}
        result = {"status": status, "latency_seconds": round(time.monotonic() - start, 3)}
        if len(raw) > MAX_RESPONSE_BYTES:
            result["error_code"] = "RESPONSE_TOO_LARGE"
            return result
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError
            result["response"] = parsed
            served_model = _safe_served_model(parsed.get("model"), self.key)
            if served_model is not None:
                with self._fake_lock:
                    self._served_models.add(served_model)
        except (ValueError, UnicodeError, RecursionError):
            result["error_code"] = "INVALID_JSON"
        return result

    def _canned(self, method, path, body):
        if path.endswith("/models"):
            return {"status": 200, "latency_seconds": 0.001,
                    "response": {"data": [{"id": self.model, "max_model_len": 32768}]}}
        if body and body.get("tools"):
            msg = {"content": None, "tool_calls": [{"id": "call_demo", "type": "function",
                   "function": {"name": "record_answer", "arguments": "{\"answer\":\"ok\"}"}}]}
        else:
            content = "4" if "2 + 2" in str(body) else "ok"
            msg = {"content": content, "reasoning_content": "brief internal reasoning" if
                   body and body.get("chat_template_kwargs", {}).get("enable_thinking") else ""}
        with self._fake_lock:
            self._fake_count += 1
            count = self._fake_count
        usage = {"prompt_tokens": max(1, len(json.dumps(body or {})) // 4),
                 "completion_tokens": max(1, len(msg.get("content") or "tool call") // 4),
                 "total_tokens": max(2, len(json.dumps(body or {})) // 4 + 1)}
        choice = {"message": msg, "finish_reason": "stop"}
        if body and body.get("max_tokens") == 4:
            choice["finish_reason"] = "length"
        return {"status": 200, "latency_seconds": 0.001, "response": {
            "model": self.model, "choices": [choice], "usage": usage, "request_number": count}}


def _model_state_path():
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "llmtool" / "gw_probe_model.json"


def _safe_served_model(value, credential_value):
    if (not isinstance(value, str) or not SERVED_MODEL_PATTERN.fullmatch(value)
            or (credential_value and credential_value in value)
            or re.search(r"(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
                         r"github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{20,})",
                         value, re.IGNORECASE)):
        return None
    return value


def _record_served_model(served_model, path=None):
    """Compare and persist the last screened served model; return the public status."""
    if served_model is None:
        return "UNKNOWN"
    path = _model_state_path() if path is None else Path(path)
    previous = None
    try:
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(state, dict) or set(state) != {"served_model"}
                    or not isinstance(state["served_model"], str)
                    or not SERVED_MODEL_PATTERN.fullmatch(state["served_model"])):
                return "MODEL_STATE_ERROR"
            previous = state["served_model"]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps({"served_model": served_model}) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return "MODEL_STATE_ERROR"
    return "GW_MODEL_CHANGED" if previous is not None and previous != served_model else "OK"


def _chat(client, messages, *, effort=None, thinking="unset", max_tokens=128, temperature=0, tools=None,
          timeout=None):
    body = {"model": client.model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature}
    if effort is not None:
        body["reasoning_effort"] = effort
    if thinking != "unset":
        body["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
    if tools is not None:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    return client.send(body=body, timeout=TIMEOUT_SECONDS if timeout is None else timeout)


def _response(result):
    response = result.get("response", {})
    choices = response.get("choices", []) if isinstance(response, dict) else []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message", {}) if isinstance(choice.get("message"), dict) else {}
    return response, choice, message


def _usage(response):
    value = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(value, dict):
        return None
    return {k: value[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(value.get(k), (int, float)) and not isinstance(value.get(k), bool)} or None


def probe_models(client):
    result = client.send("GET", path="/v1/models")
    response = result.pop("response", {})
    data = response.get("data", []) if isinstance(response, dict) else []
    result["models"] = [{k: item[k] for k in ("id", "max_model_len", "context_length", "max_context_length")
                         if k in item} for item in data if isinstance(item, dict)]
    return result


def probe_thinking(client):
    outcomes = []
    prompt = [{"role": "user", "content": "In one short sentence, say what 2 + 2 equals."}]
    for mode in ("off", "on", "unset"):
        for effort in ("low", "high"):
            result = _chat(client, prompt, effort=effort, thinking=mode, max_tokens=256)
            response, choice, message = _response(result)
            reasoning = message.get("reasoning_content", "")
            answer = message.get("content", "")
            outcomes.append({"thinking": mode, "reasoning_effort": effort, "status": result.get("status"),
                "error_code": result.get("error_code"), "latency_seconds": result.get("latency_seconds"),
                "reasoning_content_length": len(reasoning) if isinstance(reasoning, str) else None,
                "think_tags_present": isinstance(answer, str) and bool(re.search(r"</?think>", answer, re.I)),
                "usage": _usage(response), "answer": answer if isinstance(answer, str) else None})
    return {"runs": outcomes}


def probe_tools(client):
    tools = [{"type": "function", "function": {"name": "record_answer", "description": "Record a short answer.",
             "parameters": {"type": "object", "properties": {"answer": {"type": "string"}},
                            "required": ["answer"], "additionalProperties": False}}}]
    result = _chat(client, [{"role": "user", "content": "Call record_answer with answer 'ok'."}], tools=tools)
    _, _, message = _response(result)
    return {"status": result.get("status"), "error_code": result.get("error_code"),
            "tool_calls_returned": bool(message.get("tool_calls")), "tool_calls": message.get("tool_calls"),
            "latency_seconds": result.get("latency_seconds")}


def probe_determinism(client):
    runs = []
    for _ in range(4):
        result = _chat(client, [{"role": "user", "content": "Reply with only the word blue."}], max_tokens=32)
        _, choice, message = _response(result)
        runs.append({"status": result.get("status"), "error_code": result.get("error_code"),
                     "answer": message.get("content"), "finish_reason": choice.get("finish_reason"),
                     "usage": _usage(_response(result)[0]), "latency_seconds": result.get("latency_seconds")})
    return {"runs": runs, "unique_answers": len({r["answer"] for r in runs if r["answer"] is not None})}


def probe_context(client):
    observations = []
    needle = "SYNTHETIC_CONTEXT_NEEDLE_7f3a"
    for size in (8192, 16384, 32768, 65536, 131072, MAX_CONTEXT_TOKENS):
        if size > MAX_CONTEXT_TOKENS:
            break
        filler = "alpha beta gamma delta " * ((size * 4 // 24) + 1)
        prompt = needle + "\n" + filler[:size * 4] + "\nQuestion: What exact needle appeared at the beginning? Reply with only the needle."
        result = _chat(client, [{"role": "user", "content": prompt}], thinking="off", max_tokens=256, timeout=300)
        response, choice, message = _response(result)
        usage = _usage(response)
        content = message.get("content")
        answer = content[:200] if isinstance(content, str) else None
        observation = {"approx_prompt_tokens": size, "status": result.get("status"),
                       "error_code": result.get("error_code"),
                       "prompt_tokens": usage.get("prompt_tokens") if usage else None,
                       "needle_found": needle in str(content or ""),
                       "answer": answer, "finish_reason": choice.get("finish_reason"),
                       "latency_seconds": result.get("latency_seconds")}
        observations.append(observation)
        if result.get("error_code") or not isinstance(result.get("status"), int) or result["status"] >= 400:
            break
    return {"runs": observations}


def probe_output(client):
    runs = []
    prompt = [{"role": "user", "content": "Count upward from 1, one integer per line, and continue until stopped."}]
    for limit in (4096, 16384, 32768):
        result = _chat(client, prompt, max_tokens=limit, timeout=300)
        response, choice, _ = _response(result)
        usage = _usage(response)
        runs.append({"max_tokens": limit, "status": result.get("status"), "error_code": result.get("error_code"),
                     "finish_reason": choice.get("finish_reason"),
                     "completion_tokens": usage.get("completion_tokens") if usage else None,
                     "latency_seconds": result.get("latency_seconds")})
    return {"runs": runs}


def probe_concurrency(client):
    prompt = [{"role": "user", "content": "Reply with the number 7 only."}]
    runs = []
    for parallel in (1, 4, 8, 16, 32):
        start = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
            results = list(pool.map(lambda _: _chat(client, prompt, max_tokens=16), range(parallel)))
        wall = time.monotonic() - start
        codes = {}
        ok = 0
        tokens = 0
        for result in results:
            if result.get("status") == 200 and not result.get("error_code"):
                ok += 1
            code = result.get("error_code") or ("HTTP_" + str(result["status"]) if result.get("status") else "UNKNOWN")
            codes[code] = codes.get(code, 0) + 1
            usage = _usage(_response(result)[0])
            if usage and isinstance(usage.get("completion_tokens"), (int, float)):
                tokens += usage["completion_tokens"]
        runs.append({"parallelism": parallel, "ok_count": ok, "errors": codes,
                     "wall_seconds": round(wall, 3), "aggregate_output_tokens_per_second":
                     round(tokens / wall, 3) if wall else None})
    return {"runs": runs}


QUESTIONS = [
    ("What is 17 + 25?", "42"), ("What is 9 * 8?", "72"), ("What is 100 - 37?", "63"),
    ("What is 144 divided by 12?", "12"), ("What is 2 to the power of 5?", "32"),
    ("A box has 3 red and 4 blue tokens. How many tokens total?", "7"),
    ("If all zibs are wugs and all wugs are daxes, are all zibs daxes? Answer yes or no.", "yes"),
    ("Which is larger: 0.8 or 0.75? Reply with the number.", "0.8"),
    ("A sequence is 2, 4, 8, 16. What is next?", "32"),
    ("How many sides does a hexagon have?", "6"),
    ("If today is Tuesday, what day is two days later?", "Thursday"),
    ("A function returns x + 1. What does it return for x=4?", "5"),
    ("In Python, what is len([3, 5, 7])?", "3"),
    ("A loop runs for i in range(3). How many iterations?", "3"),
    ("What is the remainder when 29 is divided by 5?", "4"),
    ("There are 12 apples shared equally among 3 people. How many each?", "4"),
    ("Which word is alphabetically first: pear, apple, plum?", "apple"),
    ("If no birds are mammals and a robin is a bird, is a robin a mammal? yes/no", "no"),
    ("What is the square root of 81?", "9"), ("A code list is [2, 6, 10]; what is its last item?", "10"),
]


def _normalize(text):
    normalized = re.sub(r"[^a-z0-9.]+", " ", text.lower()).strip()
    normalized = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _answer_matches(answer, expected):
    if not isinstance(answer, str):
        return False
    normalized = _normalize(answer)
    target = expected.lower()
    if normalized == target or normalized.endswith(" " + target):
        return True
    for span in re.findall(r"\*\*(.+?)\*\*", answer, re.S):
        span_normalized = _normalize(span)
        if span_normalized == target or span_normalized.startswith(target + " "):
            return True
    if len(normalized.split()) <= 4 and normalized.startswith(target + " "):
        return True
    return False


def probe_quality(client):
    conditions = {}
    for mode in ("off", "on"):
        results = []
        for question, expected in QUESTIONS:
            result = _chat(client, [{"role": "user", "content": question}], thinking=mode, max_tokens=2048,
                           timeout=120 if mode == "on" else None)
            response, choice, message = _response(result)
            answer = message.get("content")
            results.append({"correct": _answer_matches(answer, expected),
                            "answer": answer if isinstance(answer, str) else None,
                            "finish_reason": choice.get("finish_reason"),
                            "latency_seconds": result.get("latency_seconds"), "usage": _usage(response),
                            "completion_tokens": (_usage(response) or {}).get("completion_tokens"),
                            "status": result.get("status"), "error_code": result.get("error_code")})
        latencies = [r["latency_seconds"] for r in results if isinstance(r["latency_seconds"], (int, float))]
        completion_tokens = [r["usage"]["completion_tokens"] for r in results if r["usage"] and
                             isinstance(r["usage"].get("completion_tokens"), (int, float))]
        conditions[mode] = {"accuracy": sum(r["correct"] for r in results), "total": len(results),
                            "mean_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else None,
                            "completion_tokens": sum(completion_tokens), "runs": results}
    return conditions


PROBE_FUNCTIONS = {"models": probe_models, "thinking": probe_thinking, "tools": probe_tools,
                   "determinism": probe_determinism, "context": probe_context, "output": probe_output,
                   "concurrency": probe_concurrency, "quality": probe_quality}


def _redact(value, credential_value):
    if isinstance(value, str):
        return value.replace(credential_value, "[redacted]") if credential_value else value
    if isinstance(value, list):
        return [_redact(item, credential_value) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, credential_value) for key, item in value.items()}
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=PROBES, action="append", help="run only selected probe(s); repeatable")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="use built-in canned responses; make no network requests")
    parser.add_argument("--base-url", help="loopback-only override for fake-server tests")
    parser.add_argument("--model", help="model override for loopback fake-server tests")
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
    selected = args.only or list(PROBES)
    results = {}
    for name in selected:
        try:
            results[name] = PROBE_FUNCTIONS[name](client)
        except Exception as exc:
            # A probe-level defect is recorded without exception text or request details.
            results[name] = {"error_code": "PROBE_ERROR", "error_type": type(exc).__name__}
    observed_models = sorted(client._served_models)
    served_model = observed_models[0] if len(observed_models) == 1 else None
    model_status = _record_served_model(served_model)
    output = {"status": model_status, "model": model, "served_model": served_model,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "dry_run": args.dry_run, "probes": results}
    output = _redact(output, credential_value)
    out_dir = args.out_dir or Path(os.getenv("TMPDIR", "/tmp")) / "gw-probe"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "gw_probe.json"
        markdown_path = out_dir / "gw_probe.md"
        json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        lines = ["# Company gw probe", "", "- Model: " + str(output["model"]),
                 "- Served model: " + str(output["served_model"]),
                 "- Model status: " + str(output["status"]),
                 "- Started: " + output["started_at"], "- Dry run: " + str(output["dry_run"]), "",
                 "| Probe | Summary |", "| --- | --- |"]
        for name, value in results.items():
            runs = value.get("runs") if isinstance(value, dict) else None
            if isinstance(runs, list):
                summary = str(len(runs)) + " runs"
            elif name == "quality" and isinstance(value, dict):
                summary = "; ".join(mode + ": " + str(stats.get("accuracy")) + "/" +
                                    str(stats.get("total")) for mode, stats in value.items())
            else:
                summary = "error: " + str(value.get("error_code")) if value.get("error_code") else "recorded"
            lines.append("| " + name + " | " + summary.replace("|", "\\|") + " |")
        lines.extend(["", "All prompts are synthetic. Full measurements are in `gw_probe.json`.", ""])
        markdown_path.write_text("\n".join(lines), encoding="utf-8")
    except (OSError, ValueError):
        print(json.dumps({"status": "ERROR", "error": "OUTPUT_WRITE_ERROR"}), file=sys.stderr)
        return 2
    print(json.dumps({"status": model_status, "served_model": served_model,
                      "json": os.fspath(json_path), "markdown": os.fspath(markdown_path)},
                     ensure_ascii=False))
    return 1 if model_status == "GW_MODEL_CHANGED" else 2 if model_status == "MODEL_STATE_ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
