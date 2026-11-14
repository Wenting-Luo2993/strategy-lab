"""Build the offline taskboard from opt-in repository documents (stdlib only)."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
STATES = ("backlog", "ready", "in_progress", "waiting", "review", "done")
HEADER = re.compile(r"\A<!-- taskboard\s*\n(.*?)\n-->\s*", re.DOTALL)
TASK = re.compile(r"^```taskboard-task\s*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
ID = re.compile(r"[a-z][a-z0-9-]*\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def fields(record, required, optional=()):
    require(isinstance(record, dict), "Expected a JSON object")
    require(set(required) <= record.keys(), f"Missing fields: {set(required) - record.keys()}")
    require(record.keys() <= set(required) | set(optional), f"Unknown fields: {record.keys() - set(required) - set(optional)}")
    for key in set(required) - {"projects", "depends_on", "refs"}:
        require(isinstance(record[key], str) and record[key].strip(), f"{key} must be nonempty text")
    require(bool(ID.fullmatch(record["id"])), f"Invalid stable ID: {record['id']}")


def identifiers(value, name, nonempty=False):
    require(isinstance(value, list), f"{name} must be a list")
    require(all(isinstance(item, str) and ID.fullmatch(item) for item in value), f"Invalid IDs in {name}")
    require(len(value) == len(set(value)), f"Duplicate IDs in {name}")
    require(not nonempty or value, f"{name} must not be empty")


def reviewed(value):
    require(date.fromisoformat(value).isoformat() == value, "reviewed must be YYYY-MM-DD")
    require(date.fromisoformat(value) <= datetime.now(timezone.utc).date(), "reviewed cannot be in the future")


def check_cycles(records, field):
    visiting, visited = set(), set()

    def visit(key):
        require(key not in visiting, f"Cycle in {field}: {key}")
        if key in visited:
            return
        visiting.add(key)
        for target in records[key].get(field, []):
            require(target in records, f"Unknown {field} reference: {target}")
            visit(target)
        visiting.remove(key)
        visited.add(key)

    for key in records:
        visit(key)


def collect(root: Path):
    registry = load_json((root / "docs/taskboard/projects.json").read_text(encoding="utf-8"))
    require(isinstance(registry, list) and registry, "Project registry must be a nonempty list")
    projects, documents, tasks = {}, {}, {}
    for project in registry:
        fields(project, ("id", "title", "goal", "stage", "next_action"))
        require(project["id"] not in projects, f"Duplicate project: {project['id']}")
        projects[project["id"]] = project
    for path in sorted((root / "docs").rglob("*.md")):
        require(path.resolve().is_relative_to((root / "docs").resolve()), f"Document escapes docs: {path}")
        text = path.read_text(encoding="utf-8")
        if not text.startswith("<!-- taskboard"):
            continue
        match = HEADER.match(text)
        require(match is not None, f"Malformed taskboard header: {path}")
        try:
            doc = load_json(match[1])
            fields(doc, ("id", "title", "projects", "status", "kind", "reviewed"), ("superseded_by",))
            identifiers(doc["projects"], "projects", nonempty=True)
            require(set(doc["projects"]) <= projects.keys(), "Document references an unknown project")
            require(doc["status"] in {"current", "historical", "superseded"}, "Invalid document status")
            require(doc["kind"] in {"roadmap", "plan", "bug_fix", "project", "maintenance",
                                   "research", "evidence", "reference"}, "Invalid document kind")
            reviewed(doc["reviewed"])
            identifiers(doc.get("superseded_by", []), "superseded_by")
            require(doc["status"] != "superseded" or doc.get("superseded_by"), "Superseded documents need successors")
            require(doc["id"] not in documents, f"Duplicate document: {doc['id']}")
            body = text[match.end():]
            for block in TASK.finditer(body):
                require(doc["status"] == "current", "Historical/superseded documents cannot publish tasks")
                task = load_json(block[1])
                fields(task, ("id", "title", "project", "status", "milestone", "note", "acceptance",
                              "reviewed", "depends_on", "refs"))
                require(task["project"] in doc["projects"], "Task project must belong to its document")
                require(task["status"] in STATES, "Invalid task status")
                reviewed(task["reviewed"])
                identifiers(task["depends_on"], "depends_on")
                identifiers(task["refs"], "refs", nonempty=True)
                require(task["id"] not in tasks, f"Duplicate task: {task['id']}")
                tasks[task["id"]] = {**task, "source": doc["id"]}
            require(len(re.findall(r"^```taskboard-task(?:\s|$)", body, re.MULTILINE)) == len(list(TASK.finditer(body))),
                    "Malformed taskboard-task fence")
            documents[doc["id"]] = {
                **doc, "path": path.relative_to(root).as_posix(), "body": body,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"{path.relative_to(root)}: {exc}") from exc
    require(documents and tasks, "No tagged documents or tasks found")
    check_cycles(documents, "superseded_by")
    check_cycles(tasks, "depends_on")
    for task in tasks.values():
        require(set(task["refs"]) <= documents.keys(), f"Unknown document reference in {task['id']}")
        task["blocked_by"] = [key for key in task["depends_on"] if tasks[key]["status"] != "done"]
        require(task["status"] not in {"ready", "done"} or not task["blocked_by"],
                f"{task['id']} is {task['status']} with unfinished prerequisites")
    for doc in documents.values():
        doc["html"] = render_markdown(doc["body"], doc, documents, root)
        del doc["body"]
    return {"projects": list(projects.values()), "documents": list(documents.values()),
            "tasks": list(tasks.values())}


def slug(text):
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", text.lower())).strip("-")


def inline(text, doc, documents, root):
    # Like the research viewer, escape raw HTML. This bounded preview is not a
    # general Markdown engine: code, emphasis and safe document/web links only.
    token = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*|(!?)\[([^\]]+)\]\(([^)\s]+)\)")
    rendered, offset = [], 0
    for match in token.finditer(text):
        rendered.append(html.escape(text[offset:match.start()]))
        if match[1] is not None:
            rendered.append(f"<code>{html.escape(match[1])}</code>")
        elif match[2] is not None:
            rendered.append(f"<strong>{html.escape(match[2])}</strong>")
        else:
            label, target = html.escape(match[4]), match[5]
            url = urlsplit(target)
            link = None
            if not match[3] and url.scheme in {"http", "https"} and url.netloc:
                link = f'<a href="{html.escape(target, quote=True)}" target="_blank" rel="noopener noreferrer">{label}</a>'
            elif not match[3] and not url.scheme and not url.netloc:
                relative = (root / doc["path"]).parent / unquote(url.path)
                resolved = relative.resolve() if url.path else (root / doc["path"]).resolve()
                linked = next((d for d in documents.values() if (root / d["path"]).resolve() == resolved), None)
                if linked:
                    fragment = f"/{html.escape(unquote(url.fragment), quote=True)}" if url.fragment else ""
                    link = f'<a href="#doc/{linked["id"]}{fragment}">{label}</a>'
            rendered.append(link or f'{label} <span class="muted">[source: {html.escape(target)}]</span>')
        offset = match.end()
    rendered.append(html.escape(text[offset:]))
    return "".join(rendered)


def render_markdown(body, doc, documents, root):
    result, paragraph, code = [], [], None
    lines = body.splitlines()
    heading_counts = {}

    def flush():
        if paragraph:
            result.append("<p>" + inline(" ".join(paragraph), doc, documents, root) + "</p>")
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if line.startswith("```"):
            flush()
            if code is None:
                code = []
            else:
                result.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
        elif code is not None:
            code.append(line)
        elif re.match(r"^#{1,6} ", line):
            flush()
            marker, title = line.split(" ", 1)
            base = slug(title)
            count = heading_counts.get(base, 0)
            heading_counts[base] = count + 1
            anchor = base + (f"-{count}" if count else "")
            level = min(len(marker) + 1, 6)
            result.append(f'<h{level} id="section-{html.escape(anchor)}">{inline(title, doc, documents, root)}</h{level}>')
        elif line.startswith("|") and index < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[index]):
            flush()
            rows = [line]
            index += 1
            while index < len(lines) and lines[index].startswith("|"):
                rows.append(lines[index])
                index += 1
            table = []
            for number, row in enumerate(rows):
                tag = "th" if number == 0 else "td"
                table.append("<tr>" + "".join(f"<{tag}>{inline(cell.strip(), doc, documents, root)}</{tag}>"
                                               for cell in row.strip("|").split("|")) + "</tr>")
            result.append('<div class="table-scroll"><table>' + "".join(table) + "</table></div>")
        elif re.match(r"^\s*([-*]|\d+\.) ", line):
            flush()
            result.append('<p class="list-line">' + inline(line.strip(), doc, documents, root) + "</p>")
        elif not line.strip() or line.strip() == "---":
            flush()
        else:
            paragraph.append(line)
    flush()
    if code is not None:
        result.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    return "\n".join(result)


def build(root=ROOT, output=None):
    data = collect(root)
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    assets = Path(__file__).parent
    payload = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = (assets / "template.html").read_text(encoding="utf-8")
    for key, content in (
        ("STYLE", (assets / "style.css").read_text(encoding="utf-8")),
        ("SCRIPT", (assets / "app.js").read_text(encoding="utf-8")),
        ("DATA", payload),
    ):
        page = page.replace(f"<!-- {key} -->", content)
    output = output or root / "docs/taskboard/index.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    # Validate everything before replacing the last usable snapshot.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(page)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output, data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate documents without writing HTML")
    args = parser.parse_args()
    try:
        if args.check:
            data = collect(ROOT)
            print(f"Valid: {len(data['projects'])} projects, {len(data['tasks'])} tasks, {len(data['documents'])} documents")
        else:
            output, data = build()
            print(f"Built {output}\n{len(data['projects'])} projects, {len(data['tasks'])} tasks, {len(data['documents'])} documents")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Taskboard build failed: {exc}\n")
