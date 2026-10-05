"""Pilot-only deterministic checks. No provider/network calls or quality verdicts."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def validate_schema(value, schema):
    kind = schema["type"]
    kinds = kind if isinstance(kind, list) else [kind]
    actual = ("null" if value is None else "object" if type(value) is dict
              else "array" if type(value) is list else "string" if type(value) is str
              else "unsupported")
    if actual not in kinds:
        raise ValueError("schema type mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("schema enum mismatch")
    if actual == "object":
        if set(schema["required"]) - set(value):
            raise ValueError("missing schema keys")
        if schema.get("additionalProperties") is False and set(value) - set(schema["properties"]):
            raise ValueError("extra schema keys")
        for key, item in value.items():
            validate_schema(item, schema["properties"][key])
    elif actual == "array":
        for item in value:
            validate_schema(item, schema["items"])


def packet_check(name, text):
    if name == "B03_context_pack":
        from llmtool.context import parse_context_packet
        parse_context_packet(text)


def mechanical(name, value):
    """Gold-independent derived-output checks, safe for the measured dogfood prompt."""
    fixture = (ROOT / name / "fixture.txt").read_text()
    serial = json.dumps(value, ensure_ascii=False)
    invalid = []
    result = {"schema_valid": True, "invalid_identifiers": invalid,
              "semantic_correctness": "NOT ESTABLISHED BY THESE CHECKS"}
    if name == "B01_test_triage":
        allowed_tests = set(re.findall(r"TEST (\S+) FILE", fixture))
        returned = [t for group in value["failure_groups"] for t in group["tests"]]
        files = [f for group in value["failure_groups"] for f in group["files"]]
        # Inspect both declared fields and any test/path identifiers elsewhere in text.
        candidates = returned + files + re.findall(r"test_[A-Za-z0-9_]+|tests/[A-Za-z0-9_./-]+\.py", serial)
        invalid.extend(sorted({x for x in candidates if x not in fixture}))
        result.update({"missing_failure_tests": sorted(allowed_tests - set(returned)),
                       "duplicate_failure_tests": sorted({t for t in returned if returned.count(t) > 1}),
                       "failure_coverage_complete": allowed_tests <= set(returned)})
    elif name == "B02_log_triage":
        # This fixture has fixed, explicit identifier/timestamp grammars.
        candidates = re.findall(r"req-[A-Za-z0-9_-]+|synthetic-[A-Za-z0-9_-]+|\b[A-Z][A-Z_]{3,}\b|\bE\d{2}\b|\d{2}:\d{2}:\d{2}(?:Z)?", serial)
        invalid.extend(sorted({x for x in candidates if x not in fixture}))
        required = ["req-01", "req-02", "req-03", "req-04", "UPSTREAM_TIMEOUT", "REQUEST_SUCCESS", "RECOVERY_OBSERVED"]
        result["missing_coverage_identifiers"] = [x for x in required if x not in serial]
        result["event_identifier_coverage_complete"] = not result["missing_coverage_identifiers"]
        result["signature_timestamp_membership"] = not invalid
        result["completeness_claim_semantics"] = "BLIND_REVIEW_REQUIRED; membership is not semantic proof"
    else:
        for item in value["evidence"]:
            for x in (item["source"], item.get("locator")):
                if x is not None and x not in fixture:
                    invalid.append(x)
        result["a4_context_packet_parser"] = "PASS"
        result["source_locator_membership"] = not invalid
    result["identifier_invalid_count"] = len(invalid)
    return result


def grade(name, value):
    """Frozen-gold exact/literal checks; semantic remainder explicitly deferred."""
    gold = json.loads((ROOT / name / "gold.json").read_text())
    checks = {}
    mech = mechanical(name, value)
    checks["identifier_membership"] = mech["identifier_invalid_count"] == 0
    if name == "B01_test_triage":
        expected = sorted((g["category"], tuple(sorted(g["tests"])), tuple(sorted(g["files"]))) for g in gold["expected_groups"])
        actual = sorted((g["category"], tuple(sorted(g["tests"])), tuple(sorted(g["files"]))) for g in value["failure_groups"])
        checks["expected_groups"] = expected == actual
        checks["all_failure_tests_once"] = mech["failure_coverage_complete"] and not mech["duplicate_failure_tests"]
    elif name == "B02_log_triage":
        checks["required_signatures"] = all(x in json.dumps(value["error_signatures"]) for x in gold["required_signatures"])
        checks["event_coverage"] = mech["event_identifier_coverage_complete"]
        for field, key in (("timeline", "required_timeline_terms"), ("repeated_patterns", "required_repeated_terms")):
            checks[key + "_literal"] = all(any(all(t.lower() in row.lower() for t in terms) for row in value[field]) for terms in gold[key])
    else:
        checks["evidence_sources"] = all(x in [i["source"] for i in value["evidence"]] for x in gold["required_evidence_sources"])
        for field, key in (("facts", "required_facts_terms"), ("inferences", "required_inference_terms"), ("hypotheses", "required_hypothesis_terms"), ("verify_next", "required_verify_next_terms")):
            checks[key + "_literal"] = all(any(all(t.lower() in row.lower() for t in terms) for row in value[field]) for terms in gold[key])
    checks["required_unknown_terms_literal"] = all(any(all(t.lower() in row.lower() for t in terms) for row in value["unknowns"]) for terms in gold["required_unknown_terms"])
    return {"checks": checks, "deterministic_checks_all_pass": all(checks.values()),
            "semantic_result": "BLIND_REVIEW_REQUIRED", "semantic_review_items": gold["semantic_review"],
            "note": "Literal misses are retained; semantic equivalence must be decided independently, never by the measured session."}
