import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lint


class MarkdownTests(unittest.TestCase):
    def test_valid_document(self):
        passed, errors, warnings = lint.check_text('# Title\n## Section\n```python\nx = 1\n```\n')
        self.assertTrue(passed)
        self.assertEqual((errors, warnings), ([], []))

    def test_empty_and_missing_return_consistent_results(self):
        self.assertEqual(len(lint.check_text("")), 3)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(len(lint.check_markdown(Path(directory) / "missing.md")), 3)
            path = Path(directory) / "empty.md"
            path.touch()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(lint.main([str(path), "--json"]), 1)
            self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_code_comments_do_not_count_as_headings(self):
        self.assertFalse(lint.check_text('```bash\n# comment\n## comment\n```')[0])
        self.assertFalse(lint.check_text('    # comment\n    ## comment')[0])

    def test_both_fence_types_and_length(self):
        for content in ('~~~python\nx=1', '````python\nx=1\n```', '~~~python\nx=1\n```'):
            with self.subTest(content=content):
                self.assertFalse(lint.check_text('# Title\n## Section\n' + content)[0])
        self.assertTrue(lint.check_text('# Title\n## Section\n````python\n```\n`````')[0])
        self.assertTrue(lint.check_text('# Title\n## Section\n~~~python\nx=1\n~~~')[0])

    def test_frontmatter_and_bom(self):
        self.assertTrue(lint.check_text('\ufeff---\ntitle: example\n---\n# Title\n## Section')[0])
        self.assertFalse(lint.check_text('---\ntitle: example\n# Title')[0])

    def test_title_order_uniqueness_and_hierarchy(self):
        for content in ('Intro\n# Title\n## Section', '# First\n# Second', '# Title\n#### Section', '# Title\n## Section\n#### Subsection'):
            with self.subTest(content=content):
                self.assertFalse(lint.check_text(content)[0])
        self.assertTrue(lint.check_text('# Title\n## Section\n### Subsection\n## Next')[0])

    def test_tables_validate_separator_and_column_count(self):
        prefix = '# Title\n## Section\n'
        for table in ('| A | B |\n| value | value |', '| A | B |\n| --- |\n| a | b |', '| A | B |\n| --- | --- |\n| a |'):
            with self.subTest(table=table):
                self.assertFalse(lint.check_text(prefix + table)[0])
        self.assertTrue(lint.check_text(prefix + '| A | B |\n| :--- | ---: |\n| a\\|b | `a|b` |')[0])
        self.assertTrue(lint.check_text(prefix + 'A | B\n--- | ---\na | b')[0])

    def test_strict_promotes_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'doc.md'
            path.write_text('# Title\n## Section\n```\nx\n```', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(lint.main([str(path)]), 0)
                self.assertEqual(lint.main([str(path), '--strict']), 1)

    def test_unreadable_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'doc.md'
            path.write_bytes(b'\xff')
            passed, errors, warnings = lint.check_markdown(path)
            self.assertFalse(passed)
            self.assertTrue(errors)
            self.assertEqual(warnings, [])


if __name__ == '__main__':
    unittest.main()
