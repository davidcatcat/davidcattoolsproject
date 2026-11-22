# Dependabot pull request rule

This document records the single written rule for how Dependabot pull requests
are handled in this repository. It resolves the gap flagged in issue #2577,
item (2): before this document the repository had no written rule for
Dependabot pull requests.

## The ruling

A Dependabot pull request is treated like any other pull request. It receives
the same gates as any pull request, with no exception and no auto-merge.
Dependabot pull requests do not bypass review or continuous integration, and
they are not merged automatically.

## Gates a Dependabot pull request must pass

The ruling names exactly two gates:

1. **Cross-family formal review.** Every pull request gets formal review. A
   Dependabot pull request is no exception: it must receive cross-family
   formal review before it can be merged.
2. **Exact-head CI.** A Dependabot pull request must pass continuous-
   integration checks against the exact head revision it points at — the
   precise commit of the pull request — not a cached, previously merged, or
   stale head.

These are the only gates the ruling names. There is no exception to them and
no auto-merge for Dependabot pull requests.

## Citations

- Ruling (Advisor), issue #2577:
  <https://github.com/davidcatcat/davidcattoolsproject/issues/2577#issuecomment-7081430161>
- Security policy for this repository: [SECURITY.md](../../SECURITY.md)
