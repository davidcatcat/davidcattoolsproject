# Telegram to PM Inbox instruction channel (#1160)

Status: the offline parts are built (store #2116, gates and commands #2120, PM CLI #2127)
and nothing is wired to Telegram. Owner decision #1230 D1=A
([relayed](https://github.com/davidcatcat/davidcattoolsproject/issues/1230#issuecomment-6723058672));
OpenCode lane ([relayed](https://github.com/davidcatcat/davidcattoolsproject/issues/1160#issuecomment-6890868123)).
It builds on `telegram_channel.py` and `telegram_cursor.py` on main.

## Goal and limits

The Owner sends an instruction from a phone. It lands in a local Owner instruction inbox,
and the PM reads it as a lower-trust Owner request.

- The bot process never runs Git, shell, merge, review, approval, dispatch or Task creation.
- v1 has no second factor (the remote PIN of the #120 design is not in v1). So a Telegram item
  only asks for reading, planning, status or queueing work. Any outward step it asks for (Git,
  merge, push, dispatch, spend, review verdict, config or policy) needs confirmation in the local
  PM chat first.
- Reserved decisions follow the [Owner authority matrix](../../skills/ai-team-delivery/owner-escalation.md).
  Telegram text never authorizes one; the PM marks it waiting for the Owner and asks in the local
  PM chat. v1 has no remote confirm.
- No egress beyond the Telegram Bot API; no listener, webhook or standing service.
- No AI creates, reads, prints or stores the bot token or a chat or user ID.

## Flow

1. The Owner sends `/do <text>` in the private bot chat.
2. An Owner-started foreground run long-polls `getUpdates` with `allowed_updates=["message"]`
   and `offset` = stored `update_id` + 1 (Owner step, not built).
3. The update passes the gates below, in order; the first failure refuses.
4. An accepted instruction is stored as QUEUED and the bot replies with its receipt.
5. The PM runs `llmtool owner-inbox peek`, acts within the limits above, records what it did on
   the work-package Issue, then runs `owner-inbox ack <code>`.

`active_pm_inbox` holds Task completion events keyed by a PM session in the TaskStore. An Owner
instruction is not a Task event and has no PM session, so it gets its own store. The PM never
uses `task inbox` or `task ack` for these items.

## Gates

- **Identity:** private chat, `from.is_bot` false, sender equals chat, not forwarded, no
  `via_bot`. An injected `is_allowed(update)` reads the Owner-held allowlist (0600). Tests use
  synthetic IDs. Pairing is a local TTY step (Owner). Other senders and update types get no
  reply; the offset still advances (`advance_offset(update_id)`).
- **Replay:** `update_id` above the stored offset; message `date` within 120 s of the host clock
  and at most 30 s ahead; instruction ID = SHA-256 over a domain, the alias and `update_id`, so a
  resent update is inert. A message sent while the host is off arrives stale and is refused
  `STALE_COMMAND`, never queued; the Owner resends.
- **Content:** one text message, at most 2000 characters, no media. Screened by
  `safe_diagnostic_text`, `_emitted_sensitive` and the bot-token pattern; a hit is not stored and
  gets `CONTENT_WITHHELD`. Stored text is data, never tool input.
- **Rate:** at most 10 commands a minute and 20 unacked items; else `RATE_LIMITED` or `INBOX_FULL`.

## Receipt, status, revoke

- **Receipt:** `<code> QUEUED`, code = first 12 hex of the ID. Refusals reply a fixed code.
  Replies go through `outbound_text`; text is never echoed.
- **Status:** `/inbox` lists up to 10 codes; `/inbox <code>` shows one. States: QUEUED, SEEN,
  ACKED, REVOKED, EXPIRED, REVOKE_REQUESTED.
- **Revoke:** `/revoke <code>` turns QUEUED or SEEN into REVOKED. On an ACKED item it marks
  REVOKE_REQUESTED for the PM; it carries only the code, and done work is not undone. A local
  `owner-inbox disable` stops intake.
- **Expiry:** QUEUED for 24 h becomes EXPIRED; the PM asks before acting on it.

## Store

`owner_instruction_inbox.py`: one private state file with the `telegram_cursor.py` rules
(create-only init, 0600, flock, atomic replace, fail closed with fixed codes). At most 200
entries; old ACKED, REVOKED or EXPIRED entries compact to ID tombstones.

## Threats

| Threat | Control |
| --- | --- |
| Stolen Telegram account | No outward step without local PM chat; revoke; local disable |
| Prompt injection | Text is data; lower trust; normal PM gates |
| Replay or backlog | Offset, 120 s window, ID dedupe, `STALE_COMMAND` |
| Group, forwarded or bot message | Identity gate, no reply |
| Credential pasted in | Inbound screen; not stored |
| Leak in replies | Codes and states only; `outbound_text` screen |
| Token or ID leak | Owner-held files; fixed error codes, never URLs |
| Same-user agent | Accident guard only, not a security boundary |
| Flood | Rate limit and inbox cap |

## PR plan (each at most 4 files; fake transport; no network)

1. This note (`check (docs)`, formal review).
2. `owner_instruction_inbox.py` and its test: store, states, revoke, expiry, bounds.
3. `telegram_instruction.py` and its test: gates and `/do`, `/inbox`, `/revoke` over the store
   with a fake transport.
4. `llmtool owner-inbox peek|ack|status|disable` for the PM, with a test.
5. Docs: ROADMAP A12.1 and the PM handling note.

Every code PR gets a Codex security review, then a formal Claude review. Owner stops, not
built: the real Bot API transport, token and allowlist setup, pairing, the run command and
go-live, which needs an independent security review.

## PM handling

The PM runs `llmtool owner-inbox peek`, treats each item as lower-trust Owner data, and acts
only on read, plan, status or queue requests. Before any outward or reserved step it asks in
the local PM chat (with a waiting-Owner marker), records what it did on the work-package
Issue, then runs `llmtool owner-inbox ack <code>`.

## Deferred items

- Done in #2134: `owner-inbox peek --include-expired` shows EXPIRED items.
- Done in #2134: `owner-inbox prune-history --keep N` drops old tombstones after a typed
  confirmation, so a full history (`OWNER_INBOX_HISTORY_LIMIT`) needs no manual reset.
- Done in #2134: a resend of a compacted (tombstoned) update reports COMPACTED, not ACKED.
  The Telegram `/do` reply is `<code> COMPACTED`; a test pins it.
- Open: once pruned, an id is unknown again. Only the Telegram cursor offset and the 120 s
  freshness window then keep an old resend out.
