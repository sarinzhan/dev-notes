# Note style guide

Every note is one `.md` file at `notes/<section>/<subsection>/<id>.md`. The file name (without `.md`) is the note id used in wikilinks; kebab-case, English, specific (`spring-bean-lifecycle`, not `beans`).

## Front matter (required)
```
---
title: Human title, sentence case, no trailing period
tags: [lowercase, kebab-case, 2-5 of them]
learned: 2026-10-22
---
```

## Body template (use these exact H2 headings, in this order; drop a section only if truly empty)
```
## TL;DR
2–4 sentences. The whole idea, written so that re-reading only this is enough.

## Key points
Bullets, grouped under `###` sub-headings when there are more than ~6. Tables for comparisons.
Keep interview Q&A content: fold each question into a bullet or sub-heading that answers it.

## Example
One short, realistic, compilable code block (java / sql / yaml / bash). Not toy "Foo/Bar" code.

## Pitfalls
3–6 bullets: what goes wrong in practice, gotchas, deprecations. End with 1–2 bullets linking related notes.
```

## Language
- English, written for a backend developer who already knows the basics. Plain, direct sentences. No filler ("it is worth noting").
- Translate the Russian source faithfully, keep its facts and explanations, but fix errors and fill obvious gaps (mark nothing as "to be written" — write it). Where the source says "дописать"/"to add", add it.
- Use correct current facts (Java 21/24, Spring Boot 3.x, Postgres 16, Kafka 3.x+) and say which version introduced a feature when it matters.
- One note = one topic, 300–900 words. Split a long chapter into several notes; merge tiny questions into one.

## Links
- Wikilinks: `[[note-id]]` or `[[note-id|label text]]`. Link generously to related notes, including ones in other sections.
- Only link to ids that exist or that another converter is creating in this batch (see the id list you were given). If you must mention a topic nobody is writing, write it as plain text.
- Never link to a note from within itself.

## Animations
A fenced block with the language `anim` and an animation name renders a live, interactive diagram:
````
```anim
kafka-consumer-group
```
````
Available: `kafka-partitioning`, `kafka-consumer-group`, `kafka-replication`. New ones are added to the `ANIMS` registry in `index.html` (each is a function that receives the container element and returns `{ stop() }`).

## Images
Source images live in the chapter folder under `media/`. Skip screenshots of text and of trivial diagrams; describe the idea in words or code instead. If a diagram is really needed, copy it to `notes/assets/<descriptive-name>.png` and reference it as `![caption](assets/<descriptive-name>.png)`.

## Do not
- Do not write "Section", "Chapter", or question numbers in headings.
- Do not keep Russian text, except a proper noun in parentheses if it helps.
- Do not add links to external courses/articles inside knowledge notes; those belong in the section's `resources` note.

## Russian translations
The site has an EN/RU switch. Each topic exists once; the Russian text is an alternative body for the same note, never a separate note.
- Path: `notes-ru/<section>/<subsection>/<id>.md`, exactly mirroring the English file. `build.py` refuses a translation whose English note does not exist. A note without a translation falls back to English in the RU view.
- Front matter: translate `title`; copy `tags` and `learned` unchanged from the English note.
- Headings: `## Кратко`, `## Основное`, `## Пример`, `## Подводные камни`, in the same order as the English. Translate `###` sub-headings. Notes with their own layout (project stories, case studies) keep that layout with translated headings.
- Content stays 1:1 with the English: same bullets, tables, numbers and facts, nothing added or dropped. If the English is wrong, fix the English note first.
- Wikilinks: keep the same ids. `[[id]]` renders the target's title in the reader's language; in `[[id|label]]` translate the label.
- Code: never change code, identifiers, annotations, config keys, CLI commands or output. Translate only comments. `anim` blocks and image paths are copied unchanged.
- Terms: use the established Russian term and add the English one in parentheses on first use when developers commonly say it in English (`пул потоков (thread pool)`). Product names and words Russian-speaking developers use as-is (Kafka, consumer group, offset, rebalance, commit, deploy, pod) stay in Latin script.
- Tone: neutral, impersonal sentences. Where the English addresses the reader directly, use «ты».

