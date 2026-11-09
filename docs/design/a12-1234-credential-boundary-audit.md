# A12 Credential Boundary Audit and Isolation Design (#1234)

## Requirement Source and Context
- **Requirement Source**: Owner decision 2 relayed verbatim on [#1144 comment 6744205705](https://github.com/davidcatcat/davidcattoolsproject/issues/1144#issuecomment-6744205705).
- **PM Schedule Note**: Relayed on [#1234 comment 6744211052](https://github.com/davidcatcat/davidcattoolsproject/issues/1234#issuecomment-6744211052).
- **Status**: Design only. It runs nothing, changes no setting, and does not claim #1234 isolation is done.

---

## 1. Boundary Goal
Per the Owner text:
- AI runs never read or use the Owner's GitHub login material, SSH private keys, or other Owner secrets.
- A separate `GH_CONFIG_DIR` or a different account name alone does not pass.
- The AI account (`ddavidccatssun`) gets only normal development, PR, and post-approval merge rights.
- Developer, Reviewer, CI, and llmtool workflows stay unchanged.

---

## 2. Owner Secret Access Paths Under the Same macOS User
Under a single macOS user (`uid`), an AI process shares the host environment. The table below lists Owner secret access paths a same-user process can reach.

| Owner Secret Access Path | Why Same `uid` Reaches It | Probe | Class |
| --- | --- | --- | --- |
| `gh` default config dir `~/.config/gh` and its keyring entry in the login Keychain | Files under `~/.config/gh/` are owned by the user, and the login Keychain is unlocked for any process running under the same user session. | Check directory existence/readability (`test -d ~/.config/gh`); query existence of `gh` token entry in login Keychain without printing secret. | O |
| `GH_TOKEN`, `GITHUB_TOKEN`, and `GH_ENTERPRISE_TOKEN` environment names | Ambient process environment variables are inherited from the launching user shell or session. | Python `name in os.environ` presence check that tests variable names without printing values. | M |
| `~/.ssh` | Standard directory mode `0700` permits read and traversal access to the owning `uid`. | Test reachability of the directory as a whole (`test -d ~/.ssh && test -r ~/.ssh`); never list file names. | M |
| The ssh-agent socket (`SSH_AUTH_SOCK`, plus macOS launchd agent and ssh config Keychain use) | The UNIX domain socket in `/private/tmp` is owned by `uid`; the launchd user agent and `UseKeychain` in `~/.ssh/config` access unlocked keys. | `ssh-add -l >/dev/null 2>&1` with all output discarded and only exit code kept (`0` has keys, `1` none, `2` no agent). | M |
| Login Keychain items | The macOS login Keychain automatically unlocks upon user login and permits same-user processes to query items. | Query presence of generic or internet passwords for `github.com` via Keychain services without printing values. | O |
| Git credential helpers (`osxkeychain`, store file `~/.git-credentials`, `~/.netrc`) | Global Git configuration invokes `osxkeychain`; credential files in the home directory are readable by the owning `uid`. | Inspect helper presence reduced to yes/no (`git config --get-all credential.helper >/dev/null` exit code check); file presence check (`test -r ~/.git-credentials`, `test -r ~/.netrc`). | M |
| Browser sessions signed in to `github.com` (browser automation tools act with those sessions) | Browser cookies, profile directories, and session databases under `~/Library/Application Support/` are readable by the same `uid`. | Query active browser process and profile session state for `github.com` without reading or extracting session tokens. | O |
| Environments of other same-user processes (`ps` can show them) | Same-user process listings (`ps -E`, `sysctl`) expose environment blocks of sibling processes running under the same `uid`. | Inspect sibling process table environment blocks for secret names; reading process environment strings is forbidden for AI. | O |
| Shell history files | History files (`~/.zsh_history`, `~/.bash_history`) record interactive commands and potential inline secrets, readable by the user. | Test existence and readability of history files (`test -r ~/.zsh_history`, `test -r ~/.bash_history`) without printing contents. | M |
| AI tool config dirs | Configuration paths (`~/.config/llmtool`, `~/.gemini`, etc.) reside in the user's home directory and are readable by default. | Test existence and permissions of AI tool configuration directories (`test -d ~/.config/llmtool`) without printing contents. | M |

---

## 3. Probe Classes and Design Rules

### Probe Classes
- **Class M (Metadata Only - AI May Run After Review)**: Metadata only, output discarded, result is an exit code or a yes/no boolean.
  - Examples: `test -e` or `test -r` on a fixed directory; `stat` mode bits; variable NAME presence check in Python (`name in os.environ`) that never prints the value; `ssh-add -l` with all output sent to `/dev/null` and only the exit code kept (`0` has keys, `1` none, `2` no agent).
- **Class O (Owner Runs, or Explicit Owner OK Per Run)**: Anything that queries a Keychain item, uses a credential to call GitHub, or opens a secret file.
  - Forbidden for AI always: reading, printing, copying, hashing, listing names of, or enumerating any key, token, or Keychain item; `ssh -T` or any login attempt with Owner keys; `sudo`, `su`, or root.
  - When a probe's class is unclear, it is Class O.

### Probe Design Rules
- Never list `~/.ssh` file names; test reachability of the directory as a whole.
- Never print an environment value.
- Keep every probe output inside the local run; publish only the probe ID, class, and exit code.
- Each probe is run twice:
  1. In the current AI runtime.
  2. Under `github_identity.ai_github_env` (`tools/llmtool/llmtool/github_identity.py`).
- **Meaning of Results**: The first run reveals ambient Owner secret exposure in the current execution context. The second run under `github_identity.ai_github_env` verifies that child environment scrubbing drops `GH_`, `GITHUB_`, `SSH_`, and Git config variables; if a probe still reaches an asset under `github_identity.ai_github_env` (e.g., Keychain, filesystem paths, or socket), it demonstrates that environment variable scrubbing alone does not prevent access.

---

## 4. Finding Stated Plainly
Under one macOS `uid`, file mode bits, the login Keychain, the ssh-agent socket, and process environments are all shared, so same-user isolation cannot be proven by settings alone; `ai_github_env` only scrubs `GH_`, `GITHUB_`, `SSH_`, and git config variables for child processes, which is an accident guard (#926 decision B), not a boundary.

---

## 5. Comparison of Practical Isolation Options

| Option | Effort | What It Proves |
| --- | --- | --- |
| **(A) Remove all davidcatcat GitHub login material from this macOS user; Owner keeps davidcatcat on phone and web elsewhere; AI uses only ddavidccatssun** | Low | Proves complete local absence of Owner credentials on the machine. Any same-user process reading local stores finds no Owner keys or tokens. |
| **(B) A separate standard non-admin macOS user for all AI runs with its own home, Keychain, and ssh key** | Medium to High | Proves genuine operating-system-level boundary between Owner and AI processes. Sockets, Keychain, and file modes are isolated by the OS kernel. |
| **(C) sandbox-exec profiles** | Medium | Deprecated by Apple, weak confinement, and easily bypassable. Does not prove robust boundary isolation. |
| **(D) VM or remote host for AI runs** | High | Proves strong hypervisor or network boundary isolation, but introduces synchronization overhead and infrastructure complexity. |

### Recommendation
- **Recommend A as the first step**: The Owner does not use GitHub from this Mac for daily work and workflows stay unchanged.
- **State B as the stronger follow-up**: If the Owner wants a real OS boundary (it also needs a decision on where the canonical repo lives, and macOS home and `staff`-group read access must be checked from the AI user).
- A different account name alone never passes.

---

## 6. Owner Steps (the Owner runs these; AI never does)
The Owner runs these steps manually; AI never does:
1. Invite `ddavidccatssun` as Write collaborator and accept it.
2. `GH_CONFIG_DIR=$HOME/.config/gh-ai gh auth login --hostname github.com --git-protocol https --web` signed in on the web as `ddavidccatssun`, with the `workflow` scope (needed for `.github/workflows` PRs, #1333 lesson).
3. The PM switches llmtool and PM GitHub writes to the `gh-ai` config and verifies it (`gh api user` shows the AI login; a PR, comment, and `task advance` merge work), since today they run as `davidcatcat`; only then:
4. `gh auth logout --hostname github.com --user davidcatcat`
5. `ssh-add -D` and remove `davidcatcat` SSH keys.
6. `security delete-internet-password -s github.com` until none.
7. Browser sign-out of `github.com`.
8. Revoke the old `gh` OAuth grant in `davidcatcat` settings.

Where the Owner still uses `davidcatcat` after the change: GitHub Mobile on the phone and a browser outside this macOS user (or a separate macOS user), never this user. AI never sees a code, key or secret.

---

## 7. Isolation Acceptance Checklist
Run after the Owner steps:
- [ ] All Class M probes show no Owner path reachable.
- [ ] `gh api user` under the AI env returns the AI login (`ddavidccatssun`; a login is not a secret).
- [ ] A negative test where the AI account tries to approve an owner-approval environment deployment is refused.
- [ ] An Owner-run Keychain and browser check is recorded by the PM as relayed.
- [ ] Only then may Phase B formal authorization be enabled.
