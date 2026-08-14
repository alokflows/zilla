# ZILLA — STYLE

**Binding.** This is the visual constitution for every Zilla surface: Telegram
menus and message copy (`keyboards.py`, `bot.py`), ZUI renders (`zilla/zui.py`),
and the Textual TUI (`zilla/tui/`). It is binding for every later phase — Phase
T inherits it wholesale. A screen that breaks a rule below is a bug, not a
preference.

**The reference user:** one non-technical person, on a phone, one hand, who
never reads a log and never sees the code. If a label only makes sense to
someone who has read `bot.py`, it is wrong.

Rules are numbered so a review can say "breaks R14".

---

## 1. Words

- **R1 — Sentence case everywhere.** Titles, buttons, captions. Capital on the
  first word and on real names only (`Telegram`, `agy`, `Krishna`). Never
  `Add User`, never `ON`. Values after a colon stay lowercase: `Storage: 30 days`.
- **R2 — No exclamation marks in UI copy.** Ever. Not in success text, not in
  greetings. Calm is the voice.
- **R3 — No jargon, no internals.** No `backend`, `reaper`, `callback`, `uid`,
  `token`, no file paths, no stack traces, no error codes, no version strings
  outside the status card. Say what it does for the owner.
- **R4 — Button labels ≤ 24 characters**, ≤ 18 when the row holds three
  buttons. Longer means it wraps, and a wrapped button reads as broken.
- **R5 — Error copy is one calm sentence plus one action.** Say what happened
  and what happens next, in that order, in one sentence — then one button. No
  apology paragraph, no "please try again later", no blame, no detail the owner
  cannot act on.

## 2. Type and spacing

- **R6 — Every screen opens with one bold title line**, and nothing else on
  that line.
- **R7 — Body is plain text.** No bold inside a sentence for emphasis; bold is
  reserved for titles and for a single value the screen exists to show.
- **R8 — Captions are italic** — timestamps, counts, "last run", hints. Italic
  says *secondary*, so never put an action in italic.
- **R9 — Monospace is for values, not for prose** — table columns, ids, and
  anything the owner will long-press to copy.
- **R10 — One blank line between blocks, never two.** Title, blank line, body,
  blank line, caption. No divider rows of `═` or `─`, no trailing blank line.

## 3. Icons and glyphs

- **R11 — Exactly one accent emoji per screen**, in the title, as the screen's
  icon. One screen, one icon, and the same icon every time that screen opens.
- **R12 — No decorative emoji anywhere else** — not in body prose, not on
  buttons, not one per list row. A grid of ten icons is confetti, not design.
- **R13 — Buttons may carry only these functional glyphs**, and nothing else:
  `✕` close · `◀` back or previous · `▶` next or run · `✓`/`✅` current or on ·
  `⏸` paused · `➕` add · `🗑` delete · `⭐` keep. Anything not on this list is
  decoration and must go.

## 4. Buttons and layout

- **R14 — Primary action first**, top-left of the action rows. The thing the
  owner opened this screen to do is the first thing their thumb reaches.
- **R15 — `✕ Close` is always last, and always rightmost in the last row.**
  When `◀ Back` exists it sits immediately to the left of Close, in the same
  row. Nothing goes below that row.
- **R16 — Actions above navigation.** New / add / run live on their own row
  above the back-and-close row, never mixed into it.
- **R17 — Two buttons per row**, three only when every label is ≤ 18
  characters. Never four.
- **R18 — Every menu fits one phone screen with no scrolling:** at most 8 rows
  and 12 buttons. Past that, paginate with `◀ Previous` / `Next ▶`.
- **R19 — Destructive actions need a confirm screen**, phrased as the action
  itself: `🗑 Delete session` on the left, `Cancel` on the right. Never
  `Yes` / `No`.
- **R20 — State goes at the start of a label, never at the end** —
  `✅ Heartbeat`, `⏸ Nightly memory`, `✓ Sonnet`. One state glyph per label.
  A card attached to a message (keep, error, confirm) carries no Close.

## 5. Numbers and tables

- **R21 — Numbers are right-aligned in tables**, one column per value, same
  number of decimals down a column, unit after the number (`12 files`,
  `30 days`).
- **R22 — A table wider than ~32 characters degrades to field-per-line.**
  A table the owner has to scroll sideways is worse than a list.

---

## How to check a screen

Open it on a phone-width window and tick all seven:

1. One bold title line, one accent emoji in it, and no other emoji on screen
   except the functional glyphs of R13.
2. Every label is sentence case, ≤ 24 characters, jargon-free, and has no `!`.
3. The primary action is the first button; add/new/run sit above the nav row.
4. The last row ends with `✕ Close`, with `◀ Back` to its left if present.
5. It fits without scrolling — count the rows (≤ 8) and buttons (≤ 12).
6. Numbers line up, units are spelled out, and no table scrolls sideways.
7. Trigger its error path: one calm sentence, one action, nothing technical.
