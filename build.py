#!/usr/bin/env python3
"""Scan notes/<section>/<subsection>/*.md and write notes.json + graph.json.

Front matter per note:
    ---
    title: ...
    tags: [a, b]
    learned: YYYY-MM-DD
    ---
Wikilinks: [[note-id]] or [[note-id|label]], where note-id is the file name without .md.
Russian translations live in notes-ru/<section>/<subsection>/<id>.md (same path and id as the
English note). They are optional; a note without one falls back to English in the UI.
Run: python3 build.py   (no dependencies)
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).parent
NOTES = ROOT / "notes"
NOTES_RU = ROOT / "notes-ru"
SECTIONS = json.loads((ROOT / "sections.json").read_text(encoding="utf-8"))

FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
WL = re.compile(r"\[\[([a-z0-9][a-z0-9-]*)(?:\|([^\]]+))?\]\]")
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)


def parse_front_matter(text):
    m = FM.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        meta[k.strip()] = v
    return meta, text[m.end():]


def main():
    notes, ids = [], set()
    for path in sorted(NOTES.glob("*/*/*.md")):
        sec, sub = path.parts[-3], path.parts[-2]
        if sec not in SECTIONS or sub not in SECTIONS[sec]["subs"]:
            sys.exit(f"{path}: folder {sec}/{sub} is not declared in sections.json")
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        nid = path.stem
        if nid in ids:
            sys.exit(f"duplicate note id: {nid}")
        ids.add(nid)
        notes.append({
            "id": nid, "sec": sec, "sub": sub,
            "title": meta.get("title", nid),
            "tags": meta.get("tags", []),
            "learned": meta.get("learned", ""),
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "links": sorted({m.group(1) for m in WL.finditer(CODE.sub("", body))}),
            "words": len(body.split()),
        })
        ru = NOTES_RU / sec / sub / path.name
        if ru.exists():
            ru_meta, _ = parse_front_matter(ru.read_text(encoding="utf-8"))
            notes[-1]["ru"] = str(ru.relative_to(ROOT)).replace("\\", "/")
            notes[-1]["title_ru"] = ru_meta.get("title", notes[-1]["title"])

    orphans = sorted(str(p.relative_to(ROOT)) for p in NOTES_RU.glob("*/*/*.md")
                     if not (NOTES / p.relative_to(NOTES_RU)).exists()) if NOTES_RU.exists() else []
    if orphans:
        sys.exit("translations without an English note: " + ", ".join(orphans))

    missing = sorted({l for n in notes for l in n["links"] if l not in ids})
    (ROOT / "notes.json").write_text(json.dumps({"sections": SECTIONS, "notes": notes}, ensure_ascii=False, indent=1), encoding="utf-8")

    edges = sorted({tuple(sorted((n["id"], l))) for n in notes for l in n["links"] if l in ids})
    graph = {"nodes": [{"id": n["id"], "sec": n["sec"], "sub": n["sub"], "title": n["title"], "tags": n["tags"]} for n in notes],
             "edges": [{"source": a, "target": b} for a, b in edges]}
    (ROOT / "graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")

    assets = sorted(str(a.relative_to(ROOT)).replace("\\", "/") for a in (NOTES / "assets").glob("*") if a.is_file())
    (ROOT / "assets.json").write_text(json.dumps(assets), encoding="utf-8")
    ru_count = sum(1 for n in notes if "ru" in n)
    print(f"{len(notes)} notes ({ru_count} with Russian), {len(edges)} links, {len(assets)} assets")
    if missing:
        print("links to notes that do not exist yet:", ", ".join(missing))


if __name__ == "__main__":
    main()
