// Smoke: which byte sequences does tui-test deliver to an app for wheel / press / drag / release / keys?
import { TuiTest } from "@microsoft/tui-test";
const py = process.env.POC_PYTHON ?? "python3";
const t = TuiTest.ephemeral();
const t0 = Date.now();
try {
  await t.run(py, ["-I", new URL("./mouse_echo.py", import.meta.url).pathname], { cols: 80, rows: 24 });
  await t.getByText("READY").expect();
  await t.mouse.scroll("up", { amount: 1 });
  await t.mouse.scroll("down", { amount: 2 });
  await t.mouse.down(5, 3);
  await t.mouse.move(8, 6);
  await t.mouse.up(8, 6);
  await t.mouse.drag(2, 2, 10, 4);
  await t.keyboard.press("Escape");
  await t.keyboard.press("F6");
  await t.keyboard.press("Shift+Enter");
  await t.type("中文");
  await t.waitIdle({ timeout: 2000 });
  console.log(await t.text());
  console.log("VERSION", (await import("@microsoft/tui-test")).VERSION, "state.size", JSON.stringify((await t.state()).size ?? null));
  await t.type("q");
} finally {
  await t.closeQuiet();
}
console.log("elapsed ms", Date.now() - t0);
