# Read-through guide

The checker catches errors that produce non-words or broken punctuation. This guide covers
what only a careful reader catches. Read it before the read-through pass.

## 1. Real-word OCR errors

OCR swaps that land on another valid word sail past any spellchecker. Read every line for
sense, and be suspicious when a word is grammatical but wrong in context.

| Seen | Probably meant | Why |
|---|---|---|
| arid | and | `n` read as `ri` |
| tum, bum, retum | turn, burn, return | `rn` merged into `m` |
| modem, comer | modern, corner | `rn` merged into `m` |
| barn, darn, rnay | ban, dam, may | `m` split into `rn` |
| he'll / hell, we'll / well, she'll / shell | either | apostrophe lost or invented |
| Ill, Id | I'll, I'd | apostrophe lost |
| wail / wall, bad / had, hut / but, tor / for, lo / to | the other | similar glyph shapes |
| clone / done, clear / dear | done, dear | `cl` read for `d` |
| He / lie, I / l / 1 | context | thin vertical strokes |
| Iike, Iook, Iet | like, look, let | capital I for l (checker catches most) |
| 0h, 0K, N0RTH, y0u | Oh, OK, NORTH, you | zero for the letter O (checker catches these) |
| 1OO, 19O5, 1O:3O, 5O% | 100, 1905, 10:30, 50% | letter O for zero (checker catches these; "O2" may be oxygen) |
| 0 Lord | O Lord | standalone zero for the vocative O |
| AI (the name) | Al | capital I for lowercase l; checker flags it in name-like use ("Uncle AI", "AI, wait!") and when the file also uses "Al" |
| lvan, lshikawa, lmpossible | Ivan, Ishikawa, impossible | lowercase l for I at a word start; no English word starts with l + consonant |
| AIex, EIizabeth, OIga, IIya | Alex, Elizabeth, Olga, Ilya | capital I for l inside a name |
| ofthe, inthe, Iam | of the, in the, I am | dropped spaces |
| some thing, any one | something, anyone | inserted spaces (check meaning; "any one" can be right) |

Typos that produce real words are common in translated or fansubbed files too: `here` for
`her`, `the` for `they`, `then`/`than`, `form`/`from`, `of`/`off`. Joined words that a
spellchecker may accept or skip: `Goodmorning`, `everytime`, `phonecall`, `alot`.

Also watch for: a whole line of garbage characters (bitmap noise OCR'd as text), a line
duplicated from the previous cue, words cut off at the edge of the subtitle bitmap, and
music notes (♪) turned into J, #, ¶, or ) and (, and the reverse: a find-and-replace for
music notes that hit real letters (`U.♪.` for `U.S.`). Credit lines from fansub groups or
rippers ("Translation: …", "Synced by …") aren't dialogue; report them as a structure item.

## 2. Interruptions vs full stops

OCR regularly confuses an interruption dash (`--`, `–`, `—`) with a full stop, and the
reverse. Decide from the dialogue, not the punctuation:

- A line that stops mid-thought ("If you would just give me the.") and is followed by
  another speaker cutting in, or by the same speaker restarting, is an interruption.
  Propose the file's majority interruption form.
- A line ending in a dash that is a complete sentence, followed by an unrelated new
  sentence with no cut-in, probably had a full stop. Flag as "check"; don't assume.
- A single hyphen at line end (`was -`, `was-`) is almost always a mangled interruption.
- An interruption followed by `...` in the next cue (`--` / `...continuing`) is a common
  subtitling convention for a speaker resuming. Don't flag the pair.
- A trailing-off speaker (voice fades, not cut off) takes an ellipsis, not a dash. Only
  flag if context clearly points the other way.

Em/en dashes do two different jobs; don't confuse them:

- **Speaker dash (wrong character, convert to `- `):** at the start of a line, or mid-line right
  after a finished sentence and before a new one: `— Seriously?`, `...Formation A. — Roger!`.
- **Interruption or aside (leave alone; majority rule for its form):** mid-sentence or at the end of
  a line: `He said — wait — no.`, `I was going to —`, `of the military command —`.

A speaker dash mid-line on any line means the cue has two speakers, so its dashes are never removed.

A cue that ends with a full stop followed by a cue starting lowercase can be fixed one of two ways,
never both: if the sentence continues, remove the full stop and leave the lowercase; if a new sentence
starts, keep the full stop and capitalise. Decide from the meaning, then drop the other fix.

Single hyphen at the end of a line: decide from context, never ask the user.
- Full stop misread by OCR: the line is a complete sentence and the next cue starts a new, unrelated
  one ("Stop crying-" → "Stop crying.").
- Pause dash (leave): the sentence carries on in the next line or cue ("Miss Donna - / she was the
  reason...").
- Interruption (leave): the thought breaks off and someone else speaks or the scene cuts ("I can't
  let- / - That's an order.").

## 3. Translation, grammar and language problems

These subtitles are English, often translated from another language. Flag lines a native
speaker wouldn't say, but only where the problem is real.

Flag:
- Grammatical errors that read as translation mistakes rather than character voice:
  subject–verb agreement ("She don't knows"), wrong word order in questions or embedded
  questions ("where are the keys" inside a statement), wrong or missing articles ("I go to
  the school by the bus"), wrong tense or aspect ("I am knowing him since years"), wrong
  preposition ("married with", "depends of", "discuss about"), singular/plural slips,
  missing words that leave the sentence broken.
- Literal constructions that aren't English: "He has not the courage of eating with us",
  "I am having twenty years", "Make attention!"
- Idioms translated word for word so they lose meaning.
- Wrong word choice that changes meaning (eventually/possibly, actually/currently,
  sympathetic/likeable: common false friends from European languages).
- Inconsistent names, titles and honorifics (see section 5).
- A sentence split across two cues where the split makes the first cue mean something else
  on its own.
- Pronoun or tense slips that look like translation errors rather than character voice.
- A line that contradicts the line before or after it.

Leave alone:
- British vs American spelling, unless the file mixes them (then majority rule, §5).
- Dialect, slang, broken English and verbal tics that belong to a character. If a character
  consistently speaks oddly, that's characterisation, not an error.
- Profanity, terseness, sentence fragments. Subtitles are condensed on purpose.
- Stylistic choices you'd personally make differently. The bar is "wrong or confusing", not
  "could be smoother".

When suggesting a rewrite, make the smallest change that fixes the problem, keep roughly
the same length (timing is fixed, so a longer line will be harder to read), and keep the
existing line break position where possible.

## 4. Italics

Italics on Blu-ray usually mean an off-screen voice, a voice on a phone/TV, a song, a
thought, or emphasis. Emphasis is short: one to three words, usually mid-sentence. OCR
italic errors look different:

- Italics covering most of a line but stopping or starting a word or two short of the
  edge with no punctuation at the boundary (`<i>I told you we should never have come
  back</i> here.`). Propose extending the italics to the whole line, or removing them if
  the rest of the cue and its neighbours are roman. Look at adjacent cues: if the same
  speaker's previous and next lines are fully italic, extend.
- Tags inside a word (`<i>Wor</i>king`).
- `</i><i>` back to back, empty pairs, spaces just inside tags. Merge or remove.
- Unbalanced tags. An unclosed `<i>` can italicise the rest of the film in some players.
- A short roman word sandwiched between italic spans (`<i>Yet</i> a <i>lone swallow
  soars</i>`). OCR often misses italics on one- and two-letter words, especially "a" and
  "I". Propose one continuous italic span.
- When a passage is italic in one cue and roman in the next with no change of voice (one
  continuous reading, one song), flag the run and say which is likelier from context.
- Italics switching at a dialogue dash or speaker change is normal (one speaker on screen,
  one off). Don't flag it.

Don't try to restore italics that were lost entirely; you can't see the video.

## 5. Consistency within the file

Judge everything against the file's own majority, not an external house style. The
checker reports the counts; you decide what's a real inconsistency.

- **Names and proper nouns.** Every spelling of each name, place and organisation should
  match. When variants differ by one letter, the majority spelling usually wins, but
  check: if the minority form is the standard romanisation or appears in a title card,
  say so.
- **Names mangled by I/l confusion.** Names suffer most from OCR's I/l swaps because no
  dictionary vouches for them: "Al" read as "AI", "Ishikawa" as "lshikawa", "Alex" as "AIex".
  When a name appears in two forms that differ only by I/l, the correct one is almost always the
  one that reads as a plausible name; in a file about computers or robots, "AI" may be genuine.
- **Place names.** Cities, countries, districts and landmarks are checked against
  `references/places.txt` (world cities over ~100k people, countries, US states, and curated
  landmarks and districts with extra Hong Kong and Tokyo coverage). The checker catches places
  one OCR slip away from a listed name; during the read-through, watch for misspelled places that
  aren't on the list (small towns, streets, regional landmarks) and for places transliterated
  two ways in one file (Peking/Beijing in the same film is usually deliberate period usage;
  Shinjuku/Shinjiku is not). If the user confirms a place the list lacks, suggest adding it.
- **Romanised Asian names** that differ by one letter (Hong/Kong, Chan/Chun, Fung/Tung,
  Wong/Hong) are almost always different people. Only treat them as variants when the
  minority spelling appears once or twice and context shows it's the same person.
- **Family titles used as names.** "Dad", "Mom", "Mother" are capitalised when used in
  place of a name ("Tell me, Mother") and lowercase after a possessive ("my mother").
- **Accents.** OCR drops diacritics far more often than it invents them. Loanwords
  (señor, fiancé, déjà vu) and names (José, Ramírez) should carry their accents wherever the
  file uses them at least once. For words English often writes without accents (cafe,
  naive, cliche, resume), only flag when the file also uses the accented form.
- **Majority rule for everything else.** Abbreviation stops (Mr./Mr), British/American
  spelling, OK/Okay/O.K./Ok, alright/all right, no one/no-one, goodbye/good-bye,
  toward/towards, grey/gray, e-mail/email, interruption dash form: the form used more often
  in this file wins. On a tie, report both counts and let the user pick. Don't apply
  majority rule to house-style items (quotes, ellipses, dialogue dashes, ♪ padding,
  tags): those are fixed rules.

Group each consistency finding as one report item with all affected cues.

## 6. SDH and formatting habits of Blu-ray OCR

- In two-speaker cues, OCR frequently drops the dash on the first line
  (`Want to hear it? / - Yes, sing it.`). The checker flags every one (house style: both
  speakers get a dash, whatever the file's habit), except speaker labels, captions and sound
  descriptions; it marks them "check" when line 1 might continue the previous cue's sentence.
  Confirm those against the previous cue.

- Padding inside SDH brackets is always removed: `[ sighs ]` → `[sighs]`,
  `( door closes )` → `(door closes)`.
- Space before punctuation is always removed, including before `?!`, `!?`, `:` and `;`
  (French-style spacing often survives OCR from multilingual discs).
- Two speakers on screen at once frequently become two overlapping cues. That's normal;
  don't propose merging them.
- Forced/sign subtitles are often ALL CAPS or in brackets. Leave them as they are.
- Positioning data after timestamps (X1: Y1:) is harmless. Mention it once, don't flag
  each cue.

## 7. Credits: delete or keep

Delete fansub and ripper credits: anything naming a sync/resync, rip, transcription,
"corrections by", "timed by", a subtitle site (OpenSubtitles, Addic7ed, Subscene), a URL or
an @handle, or a fansub group ("Translated by scannon & kozue, Timed by lordretsudo for ADC").

Keep official credits: the film's own credits OCR'd from the screen ("Edited by YOSHITAMI
KUROIWA", "Sound by …"), and the disc's subtitle credit ("Subtitle translation by Jessica
Ng", "Subtitles: Captions, Inc.", "English subtitles by Lenny Borger"). When it's unclear,
put it in the report as a question rather than deleting it.
