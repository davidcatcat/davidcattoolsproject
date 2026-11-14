# PR, Task, author route and Reviewer, 2026-11-01 to 2026-11-03

This table answers #1208 step 3a (from audit #1174). It lists every PR merged from 2026-11-01 to 2026-11-03 UTC, one section per merge day.

- **Built:** 2026-11-14, read-only, by sub-agent PMs subpm-gwold4 and subpm-gwold5.
- **Sources:** the merged PR list, PR head branch names, PM `developer_dispatch` markers on Issues (matched by branch), Task IDs named in those markers or PR descriptions, local TaskStore `published_pr` records, and review verdict activity markers in PR comments.
- **Kept:** only PR numbers, merge dates, Task ID prefixes, route names and Reviewer names.
- **`UNKNOWN`:** no record was found. It is not a finding about the PR.
- **Author route:** from the PM dispatch marker for the PR branch, else from the route word in the branch name. Where neither exists it is `UNKNOWN`; no route is guessed from PR titles or text. It was not re-checked against provider logs.
- **Reviewer:** the actor of the last PASS verdict marker on the PR. Where that actor is the same family as the author route, the cell lists every PASS actor on the PR in order, and a note follows the table.

## Merged 2026-11-01

39 PRs. Author route `UNKNOWN`: 34 PRs.

| PR | Merged (UTC) | Task (prefix) | Author route | Route source | Reviewer |
| --- | --- | --- | --- | --- | --- |
| #703 | 2026-11-01 | df224a23 | UNKNOWN | none | Claude |
| #718 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #719 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #720 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #722 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #723 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #724 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #726 | 2026-11-01 | 6e5035ec, d4111dcb | UNKNOWN | none | Claude |
| #727 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #728 | 2026-11-01 | UNKNOWN | Claude | branch name | Codex |
| #729 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #730 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #731 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #732 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #733 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #734 | 2026-11-01 | UNKNOWN | OpenCode | branch name | Codex |
| #735 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #736 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #737 | 2026-11-01 | UNKNOWN | AGY | branch name | Claude |
| #738 | 2026-11-01 | UNKNOWN | company-gw | branch name | Codex |
| #739 | 2026-11-01 | f8711927 | UNKNOWN | none | Codex |
| #740 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #741 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #742 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #743 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #744 | 2026-11-01 | UNKNOWN | OpenCode | branch name | Claude |
| #745 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #746 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #747 | 2026-11-01 | 43ec01ae | UNKNOWN | none | Codex |
| #749 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #750 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #751 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #752 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #753 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #754 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #755 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #756 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #758 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #759 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |

## Merged 2026-11-02

65 PRs. Author route `UNKNOWN`: 6 PRs.

| PR | Merged (UTC) | Task (prefix) | Author route | Route source | Reviewer |
| --- | --- | --- | --- | --- | --- |
| #757 | 2026-11-02 | UNKNOWN | UNKNOWN | none | Codex |
| #760 | 2026-11-02 | UNKNOWN | UNKNOWN | none | Claude |
| #761 | 2026-11-02 | UNKNOWN | UNKNOWN | none | Claude |
| #762 | 2026-11-02 | UNKNOWN | UNKNOWN | none | Claude |
| #764 | 2026-11-02 | 4c7122ab, 5d77717e, 6139ff5c | Codex | dispatch marker | Claude |
| #765 | 2026-11-02 | b175ed34, ebb07e37 | AGY | dispatch marker | Claude |
| #766 | 2026-11-02 | 904c6c16 | Codex | dispatch marker | Claude |
| #767 | 2026-11-02 | 20ada617, 2acb6e2a, 8ff89a24 | Codex | dispatch marker | Claude |
| #768 | 2026-11-02 | 0507042d, 76911d3d, 9f3dc763 | Codex | dispatch marker | Claude |
| #769 | 2026-11-02 | 459e8cb7, 752ed6d5, 923ae170 | Codex | dispatch marker | Claude |
| #770 | 2026-11-02 | 090bbfde, 15b94014, 214941e5 | Codex | dispatch marker | Claude |
| #771 | 2026-11-02 | 19db7ac8, 3910d24e, 64573ba0 | Codex | dispatch marker | Claude |
| #772 | 2026-11-02 | 8137a868, af486170 | Codex | dispatch marker | Claude |
| #773 | 2026-11-02 | b9bb63a8 | Codex | dispatch marker | Claude |
| #774 | 2026-11-02 | 0010d997, 041a42dc, 1c1f4c1f | Codex | dispatch marker | Claude |
| #775 | 2026-11-02 | 0f43caa3 | Codex | dispatch marker | Claude |
| #776 | 2026-11-02 | 0bebbfe4, 43309d97, 58af464d | Codex | dispatch marker | Claude |
| #777 | 2026-11-02 | 2bf48904 | Codex | dispatch marker | Claude |
| #778 | 2026-11-02 | f3119307 | Codex | dispatch marker | Claude |
| #779 | 2026-11-02 | 1dc3b6f5, 31a87985, 8bac3760 | Codex | dispatch marker | Claude |
| #780 | 2026-11-02 | b85154c6, fcd2c625 | Codex | dispatch marker | Claude |
| #781 | 2026-11-02 | 27d69f77, 3898f40e, d33fd438 | Codex | dispatch marker | Claude |
| #782 | 2026-11-02 | 074938f8, 07c5bc6a, 0c6fceea | Codex | dispatch marker | Claude |
| #783 | 2026-11-02 | 44b77e1c, 505fbc94, 74fb0f65 | Codex | dispatch marker | Claude |
| #784 | 2026-11-02 | 74ab68f8 | Codex | dispatch marker | Claude |
| #786 | 2026-11-02 | f53860c4 | Codex | dispatch marker | Claude |
| #787 | 2026-11-02 | 1774e783, 801c7608, 82fef775 | Codex | dispatch marker | Claude, Codex (all PASS actors; same family) |
| #788 | 2026-11-02 | UNKNOWN | UNKNOWN | none | Codex |
| #789 | 2026-11-02 | 12574214, 14c1b5bb, 4b7b2439 | Codex | dispatch marker | Claude, Codex (all PASS actors; same family) |
| #790 | 2026-11-02 | 2953c98d, b6099f53 | Codex | dispatch marker | Claude |
| #791 | 2026-11-02 | e4d050ca | Codex | dispatch marker | Claude |
| #792 | 2026-11-02 | 04376656, 221668a5, 2a52d7d7 | Codex | dispatch marker | Claude |
| #794 | 2026-11-02 | 00954c9a, 67a30624, 73e7dde3 | Codex | dispatch marker | Claude |
| #795 | 2026-11-02 | 6f0239a4 | Codex | dispatch marker | Claude |
| #796 | 2026-11-02 | 03c210f6, 66ebb325, 69ef7f03 | Codex | dispatch marker | Claude |
| #797 | 2026-11-02 | 0525b359, b25be38c | Codex | dispatch marker | Claude |
| #798 | 2026-11-02 | 0a8fc3a6, fe9e3de0 | Codex | dispatch marker | Claude |
| #799 | 2026-11-02 | 59ff3447, e45cacbe | Codex | dispatch marker | Claude |
| #800 | 2026-11-02 | 64a4d2a9, eb99c739 | Codex | dispatch marker | Claude |
| #801 | 2026-11-02 | 38b02ec8, 3bfcd2e0, 62b326ba | Codex | dispatch marker | Claude |
| #802 | 2026-11-02 | 401b2133, 478415aa, 5b5bfb9b | Codex | dispatch marker | Claude |
| #803 | 2026-11-02 | 2bd29fdb, 38a8b8d9, 4fb5424b | Codex | dispatch marker | Claude |
| #804 | 2026-11-02 | 6b86cd8b | Codex | dispatch marker | Claude |
| #805 | 2026-11-02 | 168627db, 90cf69c2, 9a0db3eb | Codex | dispatch marker | Claude |
| #806 | 2026-11-02 | 82e96f68 | Codex | dispatch marker | Claude |
| #807 | 2026-11-02 | 2da910a1, 2ddb1756, 2e207070 | Codex | dispatch marker | Claude |
| #808 | 2026-11-02 | 376a5439, 8539b7f0, ae752fca | Codex | dispatch marker | Claude |
| #809 | 2026-11-02 | UNKNOWN | Claude | dispatch marker | Codex |
| #810 | 2026-11-02 | UNKNOWN | UNKNOWN | none | Codex |
| #811 | 2026-11-02 | 16aaaf6f, 208d8bea, 2b801f54 | Codex | dispatch marker | Claude |
| #813 | 2026-11-02 | 9eed6df3, bfa1b8d4 | Codex | dispatch marker | Claude |
| #814 | 2026-11-02 | 7083109e, fd97acfc | Codex | dispatch marker | Claude |
| #815 | 2026-11-02 | 1dbd1e9c, 451401ba, 4d1352a7 | Codex | dispatch marker | Claude |
| #817 | 2026-11-02 | 2b801f54 | company-gw | branch name | Claude |
| #818 | 2026-11-02 | UNKNOWN | Claude | branch name | Codex |
| #819 | 2026-11-02 | UNKNOWN | Claude | branch name | Codex |
| #820 | 2026-11-02 | 575b1154, 64c409fb, cd5e3b6e | Codex | dispatch marker | Claude |
| #821 | 2026-11-02 | UNKNOWN | Claude | branch name | Codex |
| #822 | 2026-11-02 | 09cc9fc4, 9b84148b, e7836aae | Codex | dispatch marker | Claude |
| #823 | 2026-11-02 | UNKNOWN | Claude | branch name | Codex |
| #824 | 2026-11-02 | 5e682c00, 9181a1c7, cbd6e52a | Codex | dispatch marker | Claude |
| #825 | 2026-11-02 | 58c7a55f | Codex | dispatch marker | Claude |
| #826 | 2026-11-02 | d69d2f77 | Codex | dispatch marker | Claude |
| #827 | 2026-11-02 | 0a48b540, 19605de1, 3b2f456b | Codex | dispatch marker | Claude |
| #828 | 2026-11-02 | 5fee8a7f, ac4a12a0 | Codex | dispatch marker | Claude, Codex (all PASS actors; same family) |

Same family (author route and last PASS are both Codex): #787, #789, #828.
- #787: it also has a Claude PASS.
- #789: it also has a Claude PASS.
- #828: it also has a Claude PASS; the branch name says `gw`, the dispatch marker says Codex, and the table uses the marker.
