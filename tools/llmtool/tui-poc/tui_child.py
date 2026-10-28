"""PoC child: the real `llmtool chat --tui` entry with a synthetic (fake) provider.

Environment:
  LLMTOOL_ROOT    tools/llmtool directory of the tree under test (main or the W42 branch)
  FAKE_MODE       long   -> one non-streaming reply of ~75 lines (wrapped CJK paragraph + short lines)
                  stream -> slow streaming reply, 400 words at 40 ms each
  FAKE_COPY_FILE  when set, chat_tui_view.copy_to_clipboard writes the text here instead of pbcopy
                  (the Owner's real clipboard is never touched; main has no chat_tui_view -> skipped)
HOME must point at a scratch directory holding .config/llmtool/config.yaml (any provider entry;
create_client is replaced, so nothing is ever called over the network).
"""
import os, sys, time

ROOT = os.environ["LLMTOOL_ROOT"]
sys.path.insert(0, ROOT)
import llmtool, llmtool.chat
from llmtool.providers.base import ProviderCapabilities

PARA = ("這是一段很長的中文段落，用來測試在終端機中自動換行之後的捲動與選取行為。" * 6
        + " And here is some English text appended so that the wrapped rows mix CJK and ASCII widths. " * 3)
LONG = "\n".join(["第 %d 行 short line %d" % (i, i) for i in range(1, 31)]
                 + [PARA] + ["row after paragraph %d" % i for i in range(1, 41)] + [PARA, "END MARKER 結尾"])
STORY = ("Streaming reply 串流回覆 " + " ".join("字詞%03d" % i for i in range(1, 401))).split(" ")


class Fake:
    CAPABILITIES = ProviderCapabilities(chat=True, large_context=False, reasoning_control=False,
                                        streaming=os.environ.get("FAKE_MODE") == "stream",
                                        code_edit=False, shell=False, git=False)

    def __init__(self, *a, **k):
        pass

    def complete(self, messages):
        from llmtool.providers.base import Reply
        return Reply(LONG, {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})

    def stream(self, messages):
        for word in STORY:
            time.sleep(0.04)
            yield word + " "
        return {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}


llmtool.chat.create_client = lambda config, key=None: Fake()
if os.environ.get("FAKE_COPY_FILE"):
    try:
        import llmtool.chat_tui_view as view
        view.copy_to_clipboard = lambda text: open(os.environ["FAKE_COPY_FILE"], "w", encoding="utf-8").write(text) or True
    except Exception as exc:          # main (pre-W42) has no chat_tui_view and no app-level copy
        sys.stderr.write("no chat_tui_view: %r\n" % exc)
from llmtool.entrypoint import main
sys.exit(main(["chat", "--tui"]))
