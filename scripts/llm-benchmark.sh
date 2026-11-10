#!/bin/sh
# Company LLM benchmark for the internal Switchyard/Qwen gateway.
# POSIX-sh compatible on macOS: run with ./scripts/llm-benchmark.sh or sh scripts/llm-benchmark.sh

set -eu

# Retired (Owner 2026-11-11, #120): company/home LLM work runs only through the API Agent Gateway (gw-agent).
echo "llm-benchmark.sh is retired: use the API Agent Gateway (gw-agent). See issue #120." >&2
exit 2
MODEL=${MODEL:-gw}
CONTEXT_SIZES=${CONTEXT_SIZES:-"8000 16000 32000 64000"}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
REPORT=${REPORT:-"$ROOT_DIR/reports/llm_benchmark_$STAMP.md"}
RAW_DIR=${RAW_DIR:-"$ROOT_DIR/reports/llm_benchmark_$STAMP"}

mkdir -p "$(dirname "$REPORT")" "$RAW_DIR"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/llm-benchmark.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

need curl
need python3

append() {
  printf '%s\n' "$*" >> "$REPORT"
}

json_payload() {
  prompt_file=$1
  payload_file=$2
  max_tokens=$3
  python3 - "$prompt_file" "$payload_file" "$MODEL" "$max_tokens" <<'PY'
import json, sys
prompt_path, out_path, model, max_tokens = sys.argv[1:5]
with open(prompt_path, "r", encoding="utf-8") as f:
    prompt = f.read()
payload = {
    "model": model,
    "temperature": 0,
    "max_tokens": int(max_tokens),
    "chat_template_kwargs": {"enable_thinking": False},
    "messages": [{"role": "user", "content": prompt}],
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
PY
}

extract_response() {
  raw_file=$1
  out_file=$2
  python3 - "$raw_file" "$out_file" <<'PY'
import json, sys
raw_path, out_path = sys.argv[1:3]
try:
    with open(raw_path, "r", encoding="utf-8") as f:
        obj = json.load(f)
except Exception as e:
    result = {
        "ok": False,
        "parse_error": str(e),
        "model": None,
        "content": "",
        "usage": {},
        "finish_reason": None,
        "reasoning_chars": 0,
    }
else:
    choice = (obj.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reasoning = message.get("reasoning") or ""
    result = {
        "ok": "error" not in obj and bool(obj.get("choices")),
        "model": obj.get("model"),
        "content": message.get("content") or "",
        "usage": obj.get("usage") or {},
        "finish_reason": choice.get("finish_reason"),
        "reasoning_chars": len(reasoning),
        "error": obj.get("error"),
    }
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
PY
}

run_case() {
  case_id=$1
  title=$2
  prompt_file=$3
  max_tokens=$4

  payload="$TMP_DIR/$case_id.payload.json"
  raw="$RAW_DIR/$case_id.raw.json"
  parsed="$TMP_DIR/$case_id.parsed.json"
  meta="$TMP_DIR/$case_id.meta.txt"

  json_payload "$prompt_file" "$payload" "$max_tokens"

  start=$(python3 - <<'PY'
import time
print(time.time())
PY
)

  http_code=$(curl -sS     -o "$raw"     -w '%{http_code}'     -H 'Content-Type: application/json'     --data-binary "@$payload"     "$BASE_URL/v1/chat/completions" || true)

  end=$(python3 - <<'PY'
import time
print(time.time())
PY
)

  python3 - "$start" "$end" "$http_code" > "$meta" <<'PY'
import sys
start, end, status = sys.argv[1:4]
print(f"http_status={status}")
print(f"elapsed_seconds={float(end)-float(start):.3f}")
PY

  extract_response "$raw" "$parsed"

  append ""
  append "## $title"
  append ""
  append "TEXT-BEGIN"
  cat "$meta" >> "$REPORT"
  python3 - "$parsed" >> "$REPORT" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    x = json.load(f)
u = x.get("usage") or {}
print("model=" + str(x.get("model")))
print("finish_reason=" + str(x.get("finish_reason")))
print("prompt_tokens=" + str(u.get("prompt_tokens")))
print("completion_tokens=" + str(u.get("completion_tokens")))
print("total_tokens=" + str(u.get("total_tokens")))
print("reasoning_chars=" + str(x.get("reasoning_chars")))
print("ok=" + str(x.get("ok")))
if x.get("error"):
    print("error=" + json.dumps(x["error"], ensure_ascii=False))
PY
  append "TEXT-END"
  append ""
  append "### Model output"
  append ""
  append "TEXT-BEGIN"
  python3 - "$parsed" >> "$REPORT" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    x = json.load(f)
print((x.get("content") or "").strip())
PY
  append "TEXT-END"
}

cat > "$REPORT" <<EOF
# Company LLM Benchmark

- Date: $(date)
- Host: $(hostname)
- Base URL: $BASE_URL
- Route/model requested: $MODEL
- Context sizes (approximate tokens): $CONTEXT_SIZES
- Script: scripts/llm-benchmark.sh

This benchmark uses the OpenAI Chat Completions endpoint with chat_template_kwargs.enable_thinking=false, which the reasoning-control probe verified suppresses Qwen reasoning on this gateway. Raw JSON responses are saved next to this report.

EOF

echo "Benchmark report: $REPORT"
echo "Raw responses:  $RAW_DIR"
echo

health="$TMP_DIR/health.json"
models="$TMP_DIR/models.json"
stats="$TMP_DIR/stats.json"
curl -sS "$BASE_URL/health" > "$health" || true
curl -sS "$BASE_URL/v1/models" > "$models" || true
curl -sS "$BASE_URL/v1/stats" > "$stats" || true

append "## Preflight"
append ""
append "JSON-BEGIN"
cat "$health" >> "$REPORT"
append ""
cat "$models" >> "$REPORT"
append ""
cat "$stats" >> "$REPORT"
append "JSON-END"

append ""
append "# 1. Long-context retention"
append ""
append "Ground truth: each test contains one unique marker near the end. The model should return exactly that marker and nothing else."

for size in $CONTEXT_SIZES; do
  prompt="$TMP_DIR/context_$size.txt"
  marker="NEEDLE_CONTEXT_${size}_9F3C"
  python3 - "$size" "$marker" "$prompt" <<'PY'
import sys
size, marker, path = int(sys.argv[1]), sys.argv[2], sys.argv[3]
prefix = (
    "You are testing long-context retention. Ignore all filler text. "
    "At the very end there is one marker. Return only that marker, no explanation.\n\n"
)
filler = ("alpha " * size)
text = prefix + filler + "\nMARKER=" + marker + "\nReturn only the marker value."
with open(path, "w", encoding="utf-8") as f:
    f.write(text)
PY
  run_case "context_$size" "Context retention ~${size} tokens" "$prompt" 512
  actual=$(python3 - "$TMP_DIR/context_$size.parsed.json" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    print((json.load(f).get("content") or "").strip())
PY
)
  append ""
  append "Expected marker: $marker"
  normalized=$(printf '%s' "$actual" | sed 's/^MARKER=//')
  if [ "$actual" = "$marker" ]; then
    append ""
    append "**Automatic result: PASS (exact format)**"
  elif [ "$normalized" = "$marker" ]; then
    append ""
    append "**Automatic result: PASS (retrieval), FORMAT_FAIL**"
  else
    append ""
    append "**Automatic result: FAIL**"
  fi
done

append ""
append "# 2. DevOps log diagnosis"
append ""
append "Ground truth: the immediate failure is an unreadable mounted config file caused by permission denied. The earlier DNS warning is a distractor; the log does not prove a Secret, database, or network root cause."

prompt="$TMP_DIR/devops_log.txt"
cat > "$prompt" <<'EOF'
Analyze the following Kubernetes application log.

Return exactly these sections:
ROOT_CAUSE:
EVIDENCE:
DISTRACTORS:
UNKNOWN_OR_UNPROVEN:
NEXT_COMMANDS:

Rules:
- Separate direct evidence from hypotheses.
- Do not invent resource names, namespaces, Secrets, credentials, or network failures.
- Give at most 3 next commands.
- Commands must be safe/read-only.

Log:
2026-09-28T09:00:01Z WARN dns probe for telemetry.example.internal timed out; retry scheduled
2026-09-28T09:00:02Z INFO starting service api version=4.8.2
2026-09-28T09:00:02Z INFO loading config path=/etc/company/app/config.yaml
2026-09-28T09:00:02Z ERROR open /etc/company/app/config.yaml: permission denied
2026-09-28T09:00:02Z ERROR configuration initialization failed
2026-09-28T09:00:02Z INFO shutting down before database initialization
2026-09-28T09:00:02Z INFO process exited status=1
EOF
run_case "devops_log" "Evidence-disciplined Kubernetes log diagnosis" "$prompt" 1600

append ""
append "# 3. Git diff review"
append ""
append "Ground truth includes at least three concrete defects: shell injection via shell=True, cache-key collision because namespace is omitted, and swallowed subprocess failure because check=False/result is ignored."

prompt="$TMP_DIR/git_diff.txt"
cat > "$prompt" <<'EOF'
Review this diff as if it were a production PR.

Return only findings that are supported by the diff. For each finding include:
- severity: high|medium|low
- file and changed line/snippet
- concrete failure/security mode
- minimal fix
Do not praise the change and do not invent surrounding code.

diff --git a/tools/kube_lookup.py b/tools/kube_lookup.py
index 1111111..2222222 100644
--- a/tools/kube_lookup.py
+++ b/tools/kube_lookup.py
@@ -1,13 +1,22 @@
 import subprocess
 
 cache = {}
 
-def get_pod(name, namespace):
-    key = f"{namespace}:{name}"
+def get_pod(name, namespace):
+    key = name
     if key in cache:
         return cache[key]
 
-    result = subprocess.run(
-        ["kubectl", "get", "pod", name, "-n", namespace, "-o", "json"],
-        check=True, capture_output=True, text=True
-    )
+    cmd = f"kubectl get pod {name} -n {namespace} -o json"
+    result = subprocess.run(cmd, shell=True, check=False, capture_output=True, text=True)
     cache[key] = result.stdout
     return result.stdout
EOF
run_case "git_diff" "Production Git diff review" "$prompt" 1800

append ""
append "# 4. Multi-file root cause"
append ""
append "Ground truth: Deployment asks Secret app-db for key password, but the provided Secret contains db_password. Therefore DB_PASSWORD is not populated; this matches application.yaml and the startup log."

prompt="$TMP_DIR/multifile.txt"
cat > "$prompt" <<'EOF'
Find the root cause using only the supplied files. Build the evidence chain across files. Do not assume facts that are not present.

Return:
ROOT_CAUSE:
EVIDENCE_CHAIN:
MINIMAL_FIX:
VERIFY_AFTER_FIX:
UNKNOWNS:

--- deploy.yaml ---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orders-api
spec:
  template:
    spec:
      containers:
      - name: app
        image: registry.internal/orders-api:2026.09.28
        env:
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: app-db
              key: password

--- secret.yaml (keys only; values intentionally redacted) ---
apiVersion: v1
kind: Secret
metadata:
  name: app-db
type: Opaque
data:
  db_password: <redacted>

--- application.yaml ---
database:
  password: ${DB_PASSWORD}

--- pod.log ---
INFO configuration bootstrap started
ERROR required environment variable DB_PASSWORD is not set
ERROR startup aborted
EOF
run_case "multifile" "Cross-file Kubernetes configuration diagnosis" "$prompt" 1800

append ""
append "# 5. Context compression"
append ""
append "Ground truth: the generated input contains eight FACT markers. A useful compressor must retain all eight facts, distinguish observations from hypotheses, and avoid replaying filler."

prompt="$TMP_DIR/compression.txt"
python3 - "$prompt" <<'PY'
import sys
path = sys.argv[1]
facts = [
    "FACT_01 service=payments-api",
    "FACT_02 namespace=prod-f3",
    "FACT_03 first_failure=2026-09-28T10:14:22Z",
    "FACT_04 symptom=HTTP_503",
    "FACT_05 upstream=ledger-service",
    "FACT_06 observed_error=connection_pool_exhausted",
    "FACT_07 deploy_sha=8f4c2d1",
    "FACT_08 rollback_not_attempted",
]
chunks = []
for fact in facts:
    chunks.append(("noise informational heartbeat ok request completed " * 2200))
    chunks.append("\n" + fact + "\n")
chunks.append(("noise informational heartbeat ok request completed " * 2200))
body = "".join(chunks)
instruction = """Compress the incident material below into a context packet for a stronger coding/debugging model.

Hard requirements:
- Keep every explicit FACT_XX item and its exact value.
- Separate OBSERVATIONS from HYPOTHESES.
- Do not invent causes.
- Remove repetitive/noise lines.
- Output no more than 1200 words.
- Use exactly these headings:
OBSERVATIONS:
HYPOTHESES:
UNKNOWN:
NEXT_EVIDENCE_TO_COLLECT:

INCIDENT MATERIAL:
"""
with open(path, "w", encoding="utf-8") as f:
    f.write(instruction + body)
PY
run_case "compression" "Large-context compression and evidence preservation" "$prompt" 2200

python3 - "$TMP_DIR/compression.parsed.json" >> "$REPORT" <<'PY'
import json, sys, re
with open(sys.argv[1], "r", encoding="utf-8") as f:
    content = (json.load(f).get("content") or "")
found = sorted(set(re.findall(r"FACT_0[1-8]", content)))
print()
print("Preserved FACT markers:", ", ".join(found) if found else "none")
print(f"Automatic fact retention: {len(found)}/8")
PY

append ""
append "# Summary checklist"
append ""
append "- [ ] Long context: note the largest passing size."
append "- [ ] DevOps diagnosis: root cause stays evidence-bound."
append "- [ ] Git diff: finds the three seeded defects without invented findings."
append "- [ ] Multi-file diagnosis: connects Secret key mismatch to DB_PASSWORD failure."
append "- [ ] Compression: preserves 8/8 FACT markers and removes filler."
append "- [ ] Verify reasoning_chars=0 for every successful case."
append "- [ ] Record any HTTP errors, max-token stops, or suspicious hallucinations."
append ""
append "Raw JSON directory: $RAW_DIR"

echo
echo "DONE"
echo "Report: $REPORT"
echo "Raw:    $RAW_DIR"
