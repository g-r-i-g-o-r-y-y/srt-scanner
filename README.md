# SRT Scanner

An offline version of the `srt-qa` Claude skill. It checks OCR'd English subtitles (Blu-ray/DVD rips
through Subtitle Edit) against the same house style, shows you every finding, and writes corrected
copies containing only the fixes you tick. Nothing leaves your computer.

## Running it

You need Python 3.8 or newer. Nothing else: the spellchecker and its English dictionary are bundled.

- **Windows:** double-click `Start SRT Scanner.bat`.
- **macOS:** double-click `Start SRT Scanner.command` (the first time, right-click → Open).
- **Anywhere:** `python3 srt_scanner.py`

Your browser opens on a page served from `127.0.0.1`. Drop in `.srt` files or a `.zip` of them, click
**Check files**, untick anything you don't want, then **Apply selected fixes** and download the zip.

Rows fall into the same three groups as the skill's report. Needs your call holds the guesses
(possible real-word OCR errors, credits that might be official) and stays off until you tick a row.
Corrections (OCR misreads, place names, fansub credits) are on by default. Formatting fixes are
grouped by type (curly quotes, dash spacing, ellipses, file format), so you can tick or untick a whole
group, or click it to see each line. **Edit** on any correction lets you type your own fix; an empty
box deletes the cue.

Unknown words (names, slang) get an **Accept** button so they're never flagged again, and names
spelled two ways in one file (Kenji / Kenzi) are listed at the top of that file.

Your originals are never modified and timestamps are verified unchanged on every corrected file.

## Where things are kept

Everything goes in `~/SRT Scanner` (change it with `--data DIR`):

| Path | What it is |
|---|---|
| `accepted_words.txt` | Words you've accepted. Added to the list bundled in `engine/references/`. |
| `srt_qa_ledger.json` | Files already finished. They're skipped next time unless they change; tick *Re-check files already finished* to include them. |
| `runs/<date-time>/` | One folder per check: the input copies, `report.md`, and `corrected/` with the zip. |

## Command line

```
python3 srt_scanner.py check *.srt --out report.md          # same Markdown report as the skill
python3 srt_scanner.py apply report.items.json "all, !1:240" corrected/
```

The approval syntax for `apply` is the skill's: `all`, `!3:240` (skip a line), `!3:formatting`,
`3:118` (include a needs-your-call line), `!3` (skip a file).

## What's different from the skill

The checker, house-style rules, OCR fix list, place names and the fix/apply code are the skill's own
(`engine/`), so the same file gets the same findings. What's missing is Claude's read-through: in the
skill, Claude reads the flagged cues plus a sample of the dialogue, drops false alarms, and catches
things no rule can (`here` for `her`, garbled lines, clumsy translation, which of two name spellings is
right). Offline, that judgement is yours: expect more rows under **Needs your call**, and give the
names box and any unknown-word rows a look before applying.

## Layout

```
srt_scanner.py        launcher (app, check, apply)
app/server.py         local web server (stdlib only, listens on 127.0.0.1)
app/ui.html           the page
engine/scripts/       srt-qa checker, report builder and fix applier
engine/references/    house-style references: OCR fix list (Subtitle Edit, MIT), place names, accepted words
vendor/spellchecker/  pyspellchecker 0.9.0 (MIT), English dictionary only
samples/              a small damaged file to try it on
```

`engine/` matches the skill's `scripts/` and `references/` except for one addition to
`build_report.py`: each entry in `report.items.json` now carries a `view` block (text, suggested fix,
reason, line numbers) so the app can show and edit rows. The Markdown report is unchanged. To pick up a
newer version of the skill, copy its `scripts/` and `references/` over `engine/` and reapply that
change.
