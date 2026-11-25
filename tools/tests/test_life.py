import json
import subprocess
import tempfile
import unittest
from pathlib import Path


LIFE = Path(__file__).resolve().parents[1] / "life"
TEMPLATE = "{{title}}\n{{date}}\n{{week}}\n{{slug}}\n"
INDEX = "# Дела\n\n## Активные\n\n## Закрытые\n"


class LifeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for kind in ("day", "week", "case", "knowledge", "post"):
            self.write(f"templates/{kind}.md", TEMPLATE)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read(self, name):
        return (self.root / name).read_text(encoding="utf-8")

    def life(self, *args, expected=0, today="2026-11-25"):
        result = subprocess.run(
            [str(LIFE), "--root", str(self.root), "--date", today, *args],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_today_creates_once(self):
        path = "journal/2026/2026-11-25.md"
        self.write("templates/day.md", TEMPLATE + "{{custom}}\n")
        self.assertEqual(self.life("today").stdout.strip(), f"{path} created")
        self.assertEqual(self.read(path), "2026-11-25\n2026-11-25\n2026-W48\n2026-11-25\n{{custom}}\n")
        self.write(path, "Личная запись\n")
        (self.root / "templates/day.md").unlink()
        self.assertEqual(self.life("today").stdout.strip(), f"{path} exists")
        self.assertEqual(self.read(path), "Личная запись\n")

    def test_week_creates_once(self):
        path = "journal/2026/2026-W48.md"
        self.assertEqual(self.life("week").stdout.strip(), f"{path} created")
        self.assertEqual(self.read(path), "Неделя 2026-W48\n2026-11-25\n2026-W48\n2026-W48\n")
        self.assertEqual(self.life("week", today="2026-11-27").stdout.strip(), f"{path} exists")
        self.assertIn("2026-11-25", self.read(path))

    def test_week_uses_iso_year(self):
        path = "journal/2026/2026-W53.md"
        self.assertEqual(self.life("week", today="2027-01-01").stdout.strip(), f"{path} created")
        self.assertIn("2027-01-01\n2026-W53\n", self.read(path))

    def test_new_renders_each_kind(self):
        destinations = {
            "case": "cases/example-1/README.md",
            "knowledge": "knowledge/example-1.md",
            "post": "content/drafts/example-1.md",
        }
        for kind, path in destinations.items():
            with self.subTest(kind=kind):
                self.assertEqual(self.life("new", kind, "example-1", "--title", "Заголовок").stdout.strip(), path)
                self.assertEqual(self.read(path), "Заголовок\n2026-11-25\n2026-W48\nexample-1\n")
        self.assertEqual(
            self.read("cases/README.md"),
            INDEX.replace("## Активные\n", "## Активные\n- [Заголовок](example-1/README.md) — открыто 2026-11-25.\n"),
        )

    def test_new_case_inserts_under_active_heading(self):
        initial = INDEX.replace("## Активные\n", "## Активные\n- Старое дело\n").replace("\n", "\r\n")
        self.write("cases/README.md", initial)
        self.life("new", "case", "next", "--title", "Новое дело")
        self.assertEqual(
            (self.root / "cases/README.md").read_bytes().decode("utf-8"),
            initial.replace("## Активные\r\n", "## Активные\r\n- [Новое дело](next/README.md) — открыто 2026-11-25.\r\n"),
        )

    def test_new_case_appends_if_heading_is_missing(self):
        self.write("cases/README.md", "# Список дел")
        self.life("new", "case", "next", "--title", "Дело")
        self.assertEqual(self.read("cases/README.md"), "# Список дел\n- [Дело](next/README.md) — открыто 2026-11-25.\n")

    def test_new_does_not_overwrite_existing_case(self):
        self.write("cases/demo/README.md", "Существующее дело\n")
        self.write("cases/README.md", INDEX)
        result = self.life("new", "case", "demo", "--title", "Новая версия", expected=1)
        self.assertIn("Файл уже существует", result.stderr)
        self.assertEqual(self.read("cases/demo/README.md"), "Существующее дело\n")
        self.assertEqual(self.read("cases/README.md"), INDEX)

    def test_new_rejects_bad_slugs(self):
        for slug in ("", "Upper", "bad_slug", "../outside", "дело"):
            with self.subTest(slug=slug):
                result = self.life("new", "knowledge", slug, "--title", "Заметка", expected=1)
                self.assertIn("SLUG", result.stderr)
        self.assertFalse((self.root / "knowledge").exists())

    def test_check_reports_broken_links_and_placeholders(self):
        self.write("README.md", "# Пространство\n[битая](\n  missing.md#part\n)\n{{title}}\n")
        result = self.life("check", "--json", expected=1)
        report = json.loads(result.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(sorted((item["path"], item["line"]) for item in report["errors"]), [("README.md", 2), ("README.md", 5)])
        self.assertEqual(report["warnings"], [])
        human = self.life("check", expected=1).stdout
        self.assertIn("ERROR README.md:2 битая ссылка", human)
        self.assertIn("Ошибок: 2. Предупреждений: 0.", human)

    def test_check_ignores_code_and_excluded_folders(self):
        self.write("README.md", "````md\n[код](code.md)\n```\n[код](code.md)\n````\n`[код](inline.md)`\n``[код]\n(inline.md)``\n")
        for folder in (".git", ".local", "private", "node_modules", "templates"):
            self.write(f"nested/{folder}/skip.md" if folder != "templates" else "templates/skip.md", "[ссылка](missing.md)\n{{title}}\n")
        report = json.loads(self.life("check", "--json").stdout)
        self.assertEqual(report, {"ok": True, "errors": [], "warnings": []})

    def test_check_case_must_be_linked_from_index(self):
        self.write("cases/README.md", INDEX)
        self.write("cases/demo/README.md", "Дело\n")
        self.write("README.md", "[Дело](cases/demo/README.md)\n")
        report = json.loads(self.life("check", "--json", expected=1).stdout)
        self.assertEqual([(item["path"], item["line"]) for item in report["errors"]], [("cases/demo/README.md", 1)])

    def test_check_warns_about_knowledge_without_incoming_links(self):
        self.write("knowledge/solo.md", "[Та же заметка](solo.md#section)\n")
        self.write("knowledge/README.md", "Раздел знаний\n")
        self.write("templates/reference.md", "[Заметка](../knowledge/solo.md)\n")
        result = self.life("check", "--json")
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["errors"], [])
        self.assertEqual([(item["path"], item["line"]) for item in report["warnings"]], [("knowledge/solo.md", 1)])
        self.assertIn("WARN knowledge/solo.md:1", self.life("check").stdout)

    def test_check_accepts_valid_space(self):
        self.life("new", "case", "demo", "--title", "Разобрать [план]")
        self.write("knowledge/два слова.md", "# Заметка\n")
        self.write(
            "README.md",
            "[Папка](cases/)\n[Заметка](knowledge/два%20слова.md#section)\n"
            "[Якорь](#якорь) [Адрес](https://example.org/x) [Почта](mailto:life@example.org)\n",
        )
        self.assertEqual(json.loads(self.life("check", "--json").stdout), {"ok": True, "errors": [], "warnings": []})

    def test_search_is_literal_scoped_and_limited(self):
        self.write("knowledge/a.md", "ЗАМЕТКА.*\n" * 202 + "Заметка\n")
        self.write("cases/a.md", "заметка.*\n")
        result = self.life("search", "заметка.*", "--scope", "knowledge").stdout.splitlines()
        self.assertEqual(len(result), 201)
        self.assertEqual(result[0], "knowledge/a.md:1: ЗАМЕТКА.*")
        self.assertEqual(result[-2], "knowledge/a.md:200: ЗАМЕТКА.*")
        self.assertEqual(result[-1], "Остальные совпадения: 2.")


if __name__ == "__main__":
    unittest.main()
