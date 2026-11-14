"""Focused offline-ingestion and rendering regressions, no third-party imports."""

import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("taskboard_build", Path(__file__).parents[1] / "build.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class TaskboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docs/taskboard").mkdir(parents=True)
        self.projects = [{"id": "research", "title": "Research", "goal": "Understand",
                          "stage": "Exploration", "next_action": "Review"}]
        (self.root / "docs/taskboard/projects.json").write_text(json.dumps(self.projects), encoding="utf-8")
        self.doc = {"id": "plan", "title": "Plan", "projects": ["research"],
                    "status": "current", "kind": "plan", "reviewed": "2026-01-01"}
        self.task = {"id": "review", "title": "Review result", "project": "research",
                     "status": "review", "milestone": "Learn", "note": "No edge is also a result",
                     "acceptance": "Record uncertainty", "reviewed": "2026-01-01",
                     "depends_on": [], "refs": ["plan"]}
        self.write_doc()

    def write_doc(self, doc=None, task=None, body="", filename="plan.md"):
        metadata = self.doc if doc is None else doc
        item = self.task if task is None else task
        text = "<!-- taskboard\n" + json.dumps(metadata) + "\n-->\n# Plan\n"
        if item:
            text += "```taskboard-task\n" + json.dumps(item) + "\n```\n"
        path = self.root / "docs" / filename
        path.write_text(text + body, encoding="utf-8")
        return path

    def test_opt_in_discovery_and_added_document_need_no_index_entry(self):
        (self.root / "docs/untagged.md").write_text("# Old work\n- [x] Everything done", encoding="utf-8")
        self.write_doc({**self.doc, "id": "new-evidence", "kind": "bug_fix"}, {}, "Fresh evidence", "new.md")
        data = builder.collect(self.root)
        self.assertEqual({d["id"] for d in data["documents"]}, {"plan", "new-evidence"})
        self.assertEqual([t["id"] for t in data["tasks"]], ["review"])
        self.assertEqual(data["tasks"][0]["source"], "plan")

    def test_duplicate_unknown_and_invalid_records_fail(self):
        cases = [
            ({**self.task, "project": "missing"}, "Task project"),
            ({**self.task, "refs": ["missing"]}, "Unknown document"),
            ({**self.task, "depends_on": ["missing"]}, "Unknown depends_on"),
            ({**self.task, "status": "green"}, "Invalid task status"),
            ({**self.task, "reviewed": "not-a-date"}, "Invalid isoformat"),
            ({**self.task, "extra": "ignored?"}, "Unknown fields"),
            ({**self.task, "depends_on": ["review"]}, "Cycle"),
        ]
        for task, message in cases:
            with self.subTest(message=message):
                self.write_doc(task=task)
                with self.assertRaisesRegex(ValueError, message):
                    builder.collect(self.root)
        self.write_doc()
        self.write_doc({**self.doc, "id": "another"}, filename="duplicate.md")
        with self.assertRaisesRegex(ValueError, "Duplicate task"):
            builder.collect(self.root)

    def test_dependency_waiting_and_ready_gate(self):
        self.write_doc({**self.doc, "id": "later"}, {
            **self.task, "id": "later-task", "status": "waiting", "depends_on": ["review"],
        }, filename="later.md")
        data = builder.collect(self.root)
        task = next(t for t in data["tasks"] if t["id"] == "later-task")
        self.assertEqual(task["blocked_by"], ["review"])
        for status in ("ready", "done"):
            self.write_doc({**self.doc, "id": "later"}, {
                **self.task, "id": "later-task", "status": status, "depends_on": ["review"],
            }, filename="later.md")
            with self.assertRaisesRegex(ValueError, "unfinished prerequisites"):
                builder.collect(self.root)

    def test_history_requires_successor_and_cannot_publish_tasks(self):
        self.write_doc({**self.doc, "id": "old", "status": "superseded"}, {}, filename="old.md")
        with self.assertRaisesRegex(ValueError, "need successors"):
            builder.collect(self.root)
        old = {**self.doc, "id": "old", "status": "superseded", "superseded_by": ["plan"]}
        self.write_doc(old, {}, filename="old.md")
        self.assertEqual(len(builder.collect(self.root)["tasks"]), 1)
        self.write_doc(old, {**self.task, "id": "old-task"}, filename="old.md")
        with self.assertRaisesRegex(ValueError, "cannot publish tasks"):
            builder.collect(self.root)

    def test_markdown_preview_escapes_html_and_keeps_safe_links(self):
        body = """
<img src=x onerror=alert(1)>
[Bad](javascript:alert)
[Outside](file:///private.txt)
[Web](https://example.com)
[Inside](plan.md#plan)
`<script>` **Outcome**

| Metric | Result |
| --- | --- |
| Mean | <script>bad</script> |
"""
        self.write_doc(body=body)
        preview = builder.collect(self.root)["documents"][0]["html"]
        self.assertNotIn("<img", preview)
        self.assertNotIn("<script>", preview)
        self.assertNotIn('href="javascript:', preview)
        self.assertNotIn('href="file:', preview)
        self.assertIn('href="#doc/plan/plan"', preview)
        self.assertIn('rel="noopener noreferrer"', preview)
        self.assertIn("<table>", preview)
        self.assertIn("<strong>Outcome</strong>", preview)

    def test_build_is_self_contained_and_preserves_old_output_on_error(self):
        text = '</script><script>alert("x")</script>'
        self.write_doc(task={**self.task, "title": text}, body=text)
        output, _ = builder.build(self.root)
        page = output.read_text(encoding="utf-8")
        self.assertNotIn(text, page)
        payload = re.search(r'<script id="taskboard-data" type="application/json">(.*?)</script>', page, re.DOTALL)[1]
        self.assertEqual(json.loads(payload)["tasks"][0]["title"], text)
        self.assertIn("connect-src 'none'", page)
        self.assertNotRegex(page, r'<script[^>]+src=')
        self.assertNotIn("<!-- DATA -->", page)
        before = output.read_bytes()
        self.write_doc(task={**self.task, "refs": ["missing"]})
        with self.assertRaises(ValueError):
            builder.build(self.root)
        self.assertEqual(output.read_bytes(), before)

    def test_rebuild_updates_document_but_not_review_dates(self):
        _, first = builder.build(self.root)
        self.write_doc(body="A newly appended observation.")
        _, second = builder.build(self.root)
        self.assertNotEqual(first["documents"][0]["sha256"], second["documents"][0]["sha256"])
        self.assertIn("newly appended", second["documents"][0]["html"])
        self.assertEqual(first["tasks"][0]["reviewed"], second["tasks"][0]["reviewed"])

    def test_malformed_fences_and_duplicate_json_keys_fail(self):
        path = self.write_doc(body="\n```taskboard-task\n{")
        with self.assertRaisesRegex(ValueError, "Malformed taskboard-task"):
            builder.collect(self.root)
        path.write_text('<!-- taskboard\n{"id":"a","id":"b"}\n-->', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            builder.collect(self.root)

    def test_indented_standard_example_is_not_ingested(self):
        example = "\n    ```taskboard-task\n    not real JSON\n    ```\n"
        self.write_doc(body=example)
        self.assertEqual(len(builder.collect(self.root)["tasks"]), 1)

    def test_document_classification_and_supersession_fail_closed(self):
        for overrides, message in [
            ({"projects": ["unknown"]}, "unknown project"),
            ({"kind": "misc"}, "Invalid document kind"),
            ({"reviewed": "2999-01-01"}, "cannot be in the future"),
            ({"superseded_by": ["unknown"]}, "Unknown superseded_by"),
            ({"superseded_by": ["plan"]}, "Cycle"),
        ]:
            with self.subTest(overrides=overrides):
                self.write_doc({**self.doc, **overrides})
                with self.assertRaisesRegex(ValueError, message):
                    builder.collect(self.root)
        self.write_doc()
        self.write_doc(self.doc, {}, filename="duplicate.md")
        with self.assertRaisesRegex(ValueError, "Duplicate document"):
            builder.collect(self.root)

    def test_unindexed_and_traversal_links_are_not_active(self):
        self.write_doc(body="[Not indexed](unindexed.md) [Outside](../../outside.md)")
        (self.root / "docs/unindexed.md").write_text("Not opted in", encoding="utf-8")
        (self.root / "outside.md").write_text("Outside docs", encoding="utf-8")
        preview = builder.collect(self.root)["documents"][0]["html"]
        self.assertNotIn("<a ", preview)
        self.assertIn("Not indexed", preview)
        self.assertIn("Outside", preview)


if __name__ == "__main__":
    unittest.main()
