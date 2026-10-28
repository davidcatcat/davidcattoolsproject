// PoC: drive the real `llmtool chat --tui` (fake provider) with @microsoft/tui-test, scenarios A–H.
// Env: LLMTOOL_ROOT (tools/llmtool of the tree under test), POC_PYTHON (venv python),
//      POC_HOME (scratch HOME with .config/llmtool/config.yaml), POC_OUT (output dir), POC_LABEL (main|w42).
import { TuiTest } from "@microsoft/tui-test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const ROOT = process.env.LLMTOOL_ROOT, PY = process.env.POC_PYTHON, HOME = process.env.POC_HOME;
const OUT = path.resolve(process.env.POC_OUT ?? "out"), LABEL = process.env.POC_LABEL ?? "run";
const CHILD = new URL("./tui_child.py", import.meta.url).pathname;
const COPY_FILE = path.join(OUT, `${LABEL}-copied.txt`);
const COLS = 80, ROWS = 24;
fs.mkdirSync(OUT, { recursive: true });
fs.rmSync(COPY_FILE, { force: true });

const results = [], evidence = [], t0 = Date.now();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function record(id, name, verdict, note, screen) {
  results.push({ id, name, verdict, note, at_ms: Date.now() - t0 });
  evidence.push(`### ${id} ${name} — ${verdict}\n${note}\n` + (screen ? "```text\n" + screen + "\n```\n" : ""));
  console.log(`${id} ${verdict}: ${note.split("\n")[0]}`);
}
const lines = (text) => text.split("\n");
function pane(text) {            // conversation rows: everything above the dash rule
  const ls = lines(text); const i = ls.findIndex((l) => l.startsWith("-----"));
  return i < 0 ? ls : ls.slice(0, i);
}
const status = (text) => lines(text).slice(-2).join(" | ");
const top = (text) => pane(text)[0] ?? "";
async function screen(t) { return (await t.text()).replace(/\s+$/gm, ""); }
function childStats() {        // RSS/CPU of the python TUI child (not covered by node's own counters)
  try {
    const out = execFileSync("sh", ["-c", `ps -o pid=,rss=,%cpu=,etime= -p $(pgrep -f tui_child.py | head -1)`]).toString().trim();
    return out || "n/a";
  } catch { return "n/a"; }
}
function launch(mode) {
  const t = TuiTest.ephemeral(`poc-${LABEL}-${mode}`, {
    trace: { mode: "on", directory: path.join(OUT, `${LABEL}-trace-${mode}`) },
    timeouts: { text: 5000 },
  });
  return t.run(PY, [CHILD], {
    cols: COLS, rows: ROWS, cwd: OUT, waitReady: false,
    env: { HOME, LLMTOOL_ROOT: ROOT, FAKE_MODE: mode, FAKE_COPY_FILE: COPY_FILE, TERM: "xterm-256color",
           LANG: "en_US.UTF-8", LC_ALL: "en_US.UTF-8", PATH: process.env.PATH },
  }).then(() => t);
}
async function wheel(t, dir, n, x = 10, y = 5) {
  const tops = [];
  await t.mouse.move(x, y);
  for (let i = 0; i < n; i++) { await t.mouse.scroll(dir, { amount: 1 }); await sleep(90); tops.push(top(await screen(t))); }
  return tops;
}
async function quit(t) {        // idle TUI exits on the first Ctrl-C; a busy one needs a second
  try {
    await t.keyboard.press("Ctrl+C");
    try { await t.waitExit({ timeout: 1500 }); } catch { await t.keyboard.press("Ctrl+C"); await t.waitExit({ timeout: 5000 }); }
  } catch {}
  await t.closeQuiet();
}

// ---------------------------------------------------------------- long-reply session: A B C D E G H
let t = await launch("long");
const tA = Date.now();
try {
  await t.getByText("F1").expect({ timeout: 8000 });
  let s = await screen(t);
  record("A", "start and render", /Ctrl-C exits/.test(s) && /F1/.test(s) ? "PASS" : "FAIL",
         `TUI rendered in ${Date.now() - tA} ms (alternate screen, banner, rule, prompt and status row). child: ${childStats()}`, s);

  await t.submit("hello");
  await t.getByText("END MARKER").expect({ timeout: 8000 });
  await sleep(300);
  s = await screen(t);
  const taller = !s.includes("第 1 行") && pane(s).length <= ROWS;
  record("B", "conversation taller than the terminal", taller ? "PASS" : "FAIL",
         `reply of 73 logical lines rendered; first line "第 1 行" scrolled off: ${!s.includes("第 1 行")}; END MARKER visible at bottom. child: ${childStats()}`, s);

  const before = top(s);
  const ups = await wheel(t, "up", 15);
  const distinctUp = new Set([before, ...ups]).size - 1;
  const sUp = await screen(t);
  const downs = await wheel(t, "down", 15);
  const back = top(await screen(t)) === before;
  const sDown = await screen(t);
  const cOk = distinctUp === 15 && back;
  record("C", "wheel up/down (15 + 15 SGR wheel events)", cOk ? "PASS" : "FAIL",
         `top row changed on ${distinctUp}/15 wheel-up events; returned to the same top row after 15 wheel-down: ${back}.\n` +
         `top rows after each wheel-up: ${JSON.stringify(ups.map((l) => l.slice(0, 22)))}\nstatus after up: ${status(sUp)}`, sUp + "\n--- after 15 down ---\n" + sDown);

  // D: drag-select rows 2..4 inside the pane (0-based cells), then release
  const dragFrom = [2, 2], dragTo = [30, 4];
  const sBefore = await screen(t);
  await t.mouse.drag(dragFrom[0], dragFrom[1], dragTo[0], dragTo[1]);
  await sleep(600);
  const sD = await screen(t);
  await t.screenshot(path.join(OUT, `${LABEL}-after-drag.svg`));
  const cells = await t.cells(0, 2, COLS, 3);
  const inverse = cells.filter((c) => c.inverse || c.reverse).length;
  const st = status(sD);
  const copiedStatus = /已複製|已選/.test(st);
  record("D", "drag-select (press, move, release)", copiedStatus || inverse > 0 ? "PASS" : "FAIL",
         `status after release: "${st}"; cells with inverse style in rows 2-4: ${inverse} (sample cell keys: ${Object.keys(cells[0] ?? {}).join(",")}); pane text unchanged by the drag: ${pane(sBefore).join("\n") === pane(sD).join("\n")}`, sD);

  // E: selection / clipboard content
  await sleep(500);
  const sessionClip = await t.getClipboard();
  const fileClip = fs.existsSync(COPY_FILE) ? fs.readFileSync(COPY_FILE, "utf8") : null;
  const sel = pane(sD).slice(2, 5).join("\n");
  const want = fileClip ? fileClip.replace(/\s+/g, "") : "";
  const onScreen = want && sel.replace(/\s+/g, "").includes(want.slice(0, 40));
  record("E", "obtainable selection / clipboard content",
         fileClip ? "PASS" : "NOT_SUPPORTED",
         `tui-test session clipboard (OSC 52 only): ${JSON.stringify(sessionClip)} -> the app copies with pbcopy, which the tool cannot observe.\n` +
         `harness file stand-in for pbcopy: ${fileClip ? `${fileClip.length} chars` : "not written"}; copied text matches the dragged rows on screen: ${onScreen}\n` +
         (fileClip ? "```text\n" + fileClip + "\n```" : ""));

  // G: resize while scrolled up 5 rows: top row text must survive 80x24 -> 60x30 -> 80x24
  await wheel(t, "up", 5);
  const topBeforeResize = top(await screen(t));
  await t.resize(60, 30); await sleep(500);
  const sMid = await screen(t); const topMid = top(sMid);
  await t.resize(COLS, ROWS); await sleep(500);
  const sAfter = await screen(t); const topAfter = top(sAfter);
  const gOk = topBeforeResize === topMid && topMid === topAfter;
  record("G", "resize 80x24 -> 60x30 -> 80x24 keeps the reading position", gOk ? "PASS" : "FAIL",
         `top row before: "${topBeforeResize}" | at 60x30: "${topMid}" | back at 80x24: "${topAfter}"`, sMid + "\n--- back to 80x24 ---\n" + sAfter);

  // H1: F6 toggles mouse reporting; with it off the wheel reaches the terminal, not the app
  await t.resize(120, ROWS); await sleep(400);
  const wide = await screen(t);
  await t.keyboard.press("F6"); await sleep(300);
  const sOff = await screen(t);
  const offStatus = /滑鼠:關/.test(sOff);
  const topOff = top(sOff);
  await wheel(t, "up", 3);
  const topStill = top(await screen(t)) === topOff;
  await t.keyboard.press("F6"); await sleep(300);
  const sOn = await screen(t);
  const onStatus = /滑鼠:開/.test(sOn);
  const topOn = top(sOn);
  await wheel(t, "up", 2);
  const movesAgain = top(await screen(t)) !== topOn;
  record("H1", "F6 mouse reporting off / on (status field)", offStatus && onStatus ? "PASS" : "FAIL",
         `(at 120 cols so the status row keeps its mouse field; before F6: "${status(wide)}")\nF6 -> status shows 滑鼠:關: ${offStatus}; F6 again -> 滑鼠:開: ${onStatus}; wheel scrolls again afterwards: ${movesAgain}`, sOff);
  record("H1b", "wheel while mouse reporting is off reaches the terminal, not the app", "NOT_SUPPORTED",
         `3 wheel-up events with reporting off left the top row unchanged: ${topStill}. tui-test encodes mouse input as SGR bytes regardless of the DEC private modes the app has set (probe: an app with only ?1049 set still receives \\x1b[<64;..M and motion \\x1b[<35;..M), so a real terminal's "mouse off => terminal selects/scrolls natively" path cannot be exercised.`);
  await t.resize(COLS, ROWS); await sleep(300);

  // H2: input history (Up recalls the previous prompt)
  await t.keyboard.press("Up"); await sleep(300);
  const sHist = await screen(t);
  const inputRows = lines(sHist).slice(pane(sHist).length);
  const recalled = inputRows.some((l) => /hello/.test(l));
  record("H2", "input history not regressed (Up recalls 'hello')", recalled ? "PASS" : "FAIL",
         `rows below the rule after Up: ${JSON.stringify(inputRows.filter(Boolean))}`);
  await t.keyboard.press("Ctrl+U");
} finally {
  const stats = childStats();
  await quit(t);
  evidence.push(`long session child stats at exit: ${stats}\n`);
}

// ---------------------------------------------------------------- streaming session: F, H3 (Esc)
t = await launch("stream");
try {
  await t.getByText("F1").expect({ timeout: 8000 });
  await t.submit("go");
  await t.getByText("字詞010").expect({ timeout: 8000 });
  await wheel(t, "up", 5);
  const s1 = await screen(t); const top1 = top(s1); const st1 = status(s1);
  await sleep(1000);
  const s2 = await screen(t); const top2 = top(s2);
  const grew = s2 !== s1;
  await wheel(t, "down", 40);
  const s3 = await screen(t);
  const following = /最新/.test(status(s3)) || top(s3) !== top2;
  await sleep(400);
  const s4 = await screen(t);
  const followsNew = s4 !== s3;
  record("F", "streaming while scrolled up: position stable, then follow again", top1 === top2 && following ? "PASS" : "FAIL",
         `top row after 5 wheel-up during the stream: "${top1}"; same after 1 s of further chunks: ${top1 === top2} (screen kept changing meanwhile: ${grew}); status then: ${st1}\n` +
         `after wheel-down to the bottom: status "${status(s3)}", screen follows new chunks again: ${followsNew}`, s2 + "\n--- after wheel-down ---\n" + s4);

  const tEsc = Date.now();
  await t.keyboard.press("Escape");          // a lone ESC byte, nothing follows
  let interrupted = true, escMs = 0;
  try { await t.getByText("interrupted").expect({ timeout: 3000 }); escMs = Date.now() - tEsc; } catch { interrupted = false; }
  await sleep(300);
  const s5 = await screen(t);
  const s6 = await sleep(800).then(() => screen(t));
  record("H3", "lone Esc interrupts the stream", interrupted && s6 === s5 ? "PASS" : "FAIL",
         `"interrupt" shown after a lone Esc: ${interrupted} (${escMs} ms after the key; prompt_toolkit flushes a lone Esc after its timeoutlen); screen stable 0.8 s later (no more chunks): ${s6 === s5}`, s5);
  record("H4", "PM streaming", "NOT_VERIFIED", "Not run: needs a fake `claude` CLI on PATH and /pm new + /pm connect in a scratch state root; out of this PoC's budget (the pyte recipe already covers it).");
} finally {
  await quit(t);
}

const usage = process.cpuUsage(), mem = process.memoryUsage();
const summary = { label: LABEL, root: ROOT, tui_test: (await import("@microsoft/tui-test")).VERSION, node: process.version,
  elapsed_ms: Date.now() - t0, node_cpu_ms: Math.round((usage.user + usage.system) / 1000), node_rss_mb: +(mem.rss / 1048576).toFixed(1), results };
fs.writeFileSync(path.join(OUT, `${LABEL}-results.json`), JSON.stringify(summary, null, 2));
fs.writeFileSync(path.join(OUT, `${LABEL}-evidence.md`), `# ${LABEL} (${ROOT})\n\n` + evidence.join("\n"));
console.log(JSON.stringify({ ...summary, results: undefined }));
