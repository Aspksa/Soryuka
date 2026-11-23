"""Soryuka names and visible UI must not contain leftover names from copied prototypes.

Mandatory third-party copyright/license attribution is intentionally preserved.
"""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT/path).read_text(encoding="utf-8")


class SoryukaBrandingTests(unittest.TestCase):

    def test_homepage_uses_own_name_and_russian_text(self):
        html=text("ui/index.html")
        for required in (
            "СОРЮКА · КАРТА ПАМЯТИ", "Живая трёхмерная карта локальной памяти",
            "узлов", "связей", "событий", "Тест импульса", "Настройки графики"
        ):
            with self.subTest(required=required):
                self.assertIn(required,html)
        for obsolete in ("ZERO", "Zero's", "NOVA", "Debug Pulse",
                         "SORYUKA MEMORY GRAPH", "cross-refs", "0 events"):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete,html)

    def test_live_renderer_has_no_foreign_agent_paths_or_names(self):
        javascript=text("ui/mirror-original.js")
        # The mandatory original source attribution is permitted in the legal header.
        runtime=javascript.split("*/",1)[-1]
        for obsolete in ("clawd", "nova-letters", "Memory Categories",
                         "Access Types", "Debug Pulse", "Debug ON",
                         "Accessed ", "CONNECTED", "DISCONNECTED"):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete,runtime)
        for required in ("Разделы памяти Сорюки","Типы активности",
                         "СВЯЗЬ УСТАНОВЛЕНА","СВЯЗЬ ПОТЕРЯНА",
                         "Обращений к узлу:", "CATEGORY_TITLES",
                         "EVENT_TITLES"):
            with self.subTest(required=required):
                self.assertIn(required,runtime)

    def test_character_personality_and_console_are_still_preserved(self):
        self.assertIn("id=\"chat-form\"", text("ui/console.html"))
        self.assertIn("霜琉華",text("character/soryuka.json"))
        self.assertIn("Soryuka",text("character/SORYUKA_INSTRUCTIONS.md"))

    def test_source_attribution_and_licenses_are_preserved(self):
        notice=text("THIRD_PARTY_NOTICES.md")
        self.assertIn("gnufoo/agent-mirror",notice)
        self.assertIn("Permission is hereby granted",notice)
        self.assertIn("Three.js",notice)

    def test_main_documentation_does_not_describe_retired_canvas_as_active(self):
        readme=text("README.md")
        archive=text("docs/MEMORY_MIRROR.md")
        self.assertIn("## Главная: живая 3D-карта памяти Сорюки",readme)
        self.assertIn("историческая заметка",archive)
        self.assertIn("не используется на главной",archive)
        self.assertNotIn("реализован собственный граф на Canvas вместо",readme)


if __name__=="__main__":
    unittest.main()
