# dev-notes

My personal engineering notebook: Java, Spring, databases, messaging, architecture and DevOps, written as short English summary notes with Obsidian-style `[[wikilinks]]` and a graph view. Published with GitHub Pages as a single page.

## Layout

```
index.html              the one-page app (marked.js + highlight.js + d3, no build step for the page itself)
sections.json           section -> subsection names; every note folder must be declared here
PLAN.md                 full list of note ids per folder: existing, moved, and still to write
notes/<section>/<subsection>/<id>.md   one note per topic, front matter + markdown
notes/assets/           images referenced from notes as assets/<name>.png
build.py                scans notes/ and writes notes.json + graph.json (run before commit, or let the Action do it)
STYLE.md                how a note is written
```

## Adding a note

1. Create `notes/<section>/<subsection>/<id>.md` following `STYLE.md`.
2. Link related notes with `[[other-id]]`.
3. Run `python3 build.py` (or just push: the GitHub Action rebuilds `notes.json` and `graph.json`).

A link to a note that does not exist yet renders grey with a dagger: that is the to-write list.

## Running locally

Any static server works, e.g. `python3 -m http.server 8080` and open http://localhost:8080.
