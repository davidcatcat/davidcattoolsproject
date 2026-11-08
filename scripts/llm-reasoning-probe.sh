#!/bin/sh
set -eu

BASE_URL=${BASE_URL:?set BASE_URL to the home LLM gateway URL, for example http://home-llm.example:8000}
MODEL=${MODEL:-gw}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
REPORT=${REPORT:-"$ROOT_DIR/reports/llm_reasoning_probe_$STAMP.md"}
RAW_DIR=${RAW_DIR:-"$ROOT_DIR/reports/llm_reasoning_probe_$STAMP"}
mkdir -p "$(dirname "$REPORT")" "$RAW_DIR"
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/llm-reasoning-probe.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM

command -v curl >/dev/null 2>&1 || { echo "curl required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 required" >&2; exit 1; }

cat > "$TMP_DIR/task.txt" <<'EOF'
Analyze this Kubernetes application log.

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

cat > "$REPORT" <<EOF
# Home LLM Reasoning-Control Probe

- Date: $(date)
- Host: $(hostname)
- Base URL: $BASE_URL
- Requested route: $MODEL

Goal: find a reliable way to reduce or disable verbose reasoning so the final answer is emitted before max_tokens is exhausted.
EOF

run_case() {
  name=$1
  max_tokens=$2
  mode=$3
  payload="$TMP_DIR/$name.payload.json"
  raw="$RAW_DIR/$name.raw.json"
  parsed="$TMP_DIR/$name.parsed.json"

  python3 - "$TMP_DIR/task.txt" "$payload" "$MODEL" "$max_tokens" "$mode" <<'PY'
import json, sys
task_path, payload_path, model, max_tokens, mode = sys.argv[1:6]
prompt = open(task_path, encoding='utf-8').read()
p = {'model': model, 'temperature': 0, 'max_tokens': int(max_tokens), 'messages': [{'role':'user','content':prompt}]}
if mode == 'prompt_no_reasoning':
    p['messages'][0]['content'] = 'Do not provide chain-of-thought, analysis, hidden reasoning, or a thinking transcript. Return only the final answer.\n\n' + prompt
elif mode == 'prompt_concise':
    p['messages'][0]['content'] = 'Answer in at most 250 words. Do not narrate your reasoning. Return only the final answer.\n\n' + prompt
elif mode == 'enable_thinking_false':
    p['enable_thinking'] = False
elif mode == 'thinking_false':
    p['thinking'] = False
elif mode == 'reasoning_effort_none':
    p['reasoning_effort'] = 'none'
elif mode == 'reasoning_none':
    p['reasoning'] = {'effort':'none'}
elif mode == 'chat_template_kwargs':
    p['chat_template_kwargs'] = {'enable_thinking': False}
json.dump(p, open(payload_path,'w',encoding='utf-8'), ensure_ascii=False)
PY

  start=$(python3 -c "import time; print(time.time())")
  code=$(curl -sS -o "$raw" -w "%{http_code}" -H "Content-Type: application/json" --data-binary "@$payload" "$BASE_URL/v1/chat/completions" || true)
  end=$(python3 -c "import time; print(time.time())")

  python3 - "$raw" "$parsed" "$code" "$start" "$end" "$name" >> "$REPORT" <<'PY'
import json, sys
raw, parsed, code, start, end, name = sys.argv[1:7]
try:
    obj = json.load(open(raw, encoding='utf-8'))
except Exception as e:
    obj = {'error': {'message': str(e)}}
choice = (obj.get('choices') or [{}])[0]
msg = choice.get('message') or {}
reasoning = msg.get('reasoning') or ''
content = (msg.get('content') or '').strip()
usage = obj.get('usage') or {}
r = {
  'name': name, 'http': code, 'elapsed': round(float(end)-float(start),3),
  'model': obj.get('model'), 'finish': choice.get('finish_reason'),
  'prompt_tokens': usage.get('prompt_tokens'), 'completion_tokens': usage.get('completion_tokens'),
  'total_tokens': usage.get('total_tokens'), 'reasoning_chars': len(reasoning),
  'content_chars': len(content), 'content': content, 'error': obj.get('error')
}
json.dump(r, open(parsed,'w',encoding='utf-8'), ensure_ascii=False, indent=2)
print()
print('## ' + name)
print()
for k in ['http','elapsed','model','finish','prompt_tokens','completion_tokens','total_tokens','reasoning_chars','content_chars']:
    print(f'- {k}: {r[k]}')
if r['error']:
    print('- error: ' + json.dumps(r['error'], ensure_ascii=False))
print()
print('### Final content')
print()
print('    ' + content.replace('\n','\n    ') if content else '    <EMPTY>')
PY
}

echo "Running baseline_1800"; run_case baseline_1800 1800 baseline
echo "Running baseline_6000"; run_case baseline_6000 6000 baseline
echo "Running prompt_no_reasoning"; run_case prompt_no_reasoning 1800 prompt_no_reasoning
echo "Running prompt_concise"; run_case prompt_concise 1800 prompt_concise
echo "Running enable_thinking_false"; run_case enable_thinking_false 1800 enable_thinking_false
echo "Running thinking_false"; run_case thinking_false 1800 thinking_false
echo "Running reasoning_effort_none"; run_case reasoning_effort_none 1800 reasoning_effort_none
echo "Running reasoning_none"; run_case reasoning_none 1800 reasoning_none
echo "Running chat_template_kwargs"; run_case chat_template_kwargs 1800 chat_template_kwargs

python3 - "$REPORT" "$TMP_DIR" <<'PY'
import json, os, sys
report, tmp = sys.argv[1:3]
names = ['baseline_1800','baseline_6000','prompt_no_reasoning','prompt_concise','enable_thinking_false','thinking_false','reasoning_effort_none','reasoning_none','chat_template_kwargs']
rows=[]
for n in names:
    r=json.load(open(os.path.join(tmp,n+'.parsed.json'),encoding='utf-8'))
    rows.append(r)
with open(report,'a',encoding='utf-8') as f:
    f.write('\n# Comparison\n\n')
    f.write('| Variant | HTTP | Finish | Completion | Reasoning chars | Final chars |\n')
    f.write('|---|---:|---|---:|---:|---:|\n')
    for r in rows:
        f.write(f"| {r['name']} | {r['http']} | {r['finish']} | {r['completion_tokens']} | {r['reasoning_chars']} | {r['content_chars']} |\n")
    good=[r for r in rows if r['http']=='200' and r['finish']=='stop' and r['content_chars']>0]
    f.write('\n')
    if good:
        good.sort(key=lambda r: ((r['completion_tokens'] or 10**9), r['reasoning_chars']))
        b=good[0]
        f.write(f"Automatic candidate: **{b['name']}** (completion={b['completion_tokens']}, reasoning_chars={b['reasoning_chars']}).\n")
    else:
        f.write('Automatic candidate: **none**. No variant returned a complete final answer with finish=stop.\n')
PY

echo ""
echo "DONE"
echo "Report: $REPORT"
echo "Raw:    $RAW_DIR"