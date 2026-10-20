import unittest

from wiki_translation import translate_markdown


class WikiTranslationTests(unittest.TestCase):
    def test_translates_visible_text_and_preserves_markdown_targets(self):
        values = {
            "Overview": "概览",
            "Uses retrieval with ": "使用检索与 ",
            " and ": " 和 ",
            "documentation": "文档",
            "related concept": "相关概念",
        }

        def fake_translate(value: str) -> str:
            return values.get(value, f"中:{value}")

        source = (
            "# Overview\n\n"
            "- Uses retrieval with [[related-concept]] and "
            "[documentation](https://example.com).\n\n"
            "```python\nprint('Overview')\n```\n"
        )
        translated = translate_markdown(source, fake_translate, workers=2)

        self.assertIn("# 概览", translated)
        self.assertIn("[[related-concept|相关概念]]", translated)
        self.assertIn("[文档](https://example.com)", translated)
        self.assertIn("```python\nprint('Overview')\n```", translated)

    def test_keeps_non_english_and_inline_code_unchanged(self):
        translated = translate_markdown(
            "# 已有中文\n\nUse `Qwen3-8B`.\n",
            lambda value: "使用 " if value.strip() == "Use" else value,
            workers=1,
        )
        self.assertIn("# 已有中文", translated)
        self.assertIn("`Qwen3-8B`", translated)

    def test_keeps_common_research_acronyms_readable_in_wikilinks(self):
        seen = []

        def fake_translate(value: str) -> str:
            seen.append(value)
            return value

        translated = translate_markdown(
            "- [[retrieval-augmented-generation-rag]]\n- [[llm-agents]]\n",
            fake_translate,
            workers=1,
        )
        self.assertIn("retrieval augmented generation RAG", seen)
        self.assertIn("LLM agents", seen)
        self.assertIn("[[retrieval-augmented-generation-rag|", translated)


if __name__ == "__main__":
    unittest.main()
