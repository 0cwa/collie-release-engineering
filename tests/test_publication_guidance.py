import tempfile
import unittest
from pathlib import Path

from source.layers.publication import write_publication_docs


class PublicationGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.app = Path(self.scratch.name)
        self.engineering = Path(__file__).resolve().parents[1]
        self.introduction = "Welcome! Collie is for people using private messaging. 🐶\n"
        self.readme = "# Collie\n\n" + self.introduction + "\n## Feedback\n\nSend feedback.\n\n## License\n\nAGPL.\n"
        (self.app / "README.md").write_text(self.readme)

    def test_generated_agent_policy_is_the_canonical_template(self):
        write_publication_docs(self.app, self.engineering)
        expected = self.engineering / "source/policies/application-AGENTS.md"
        self.assertEqual((self.app / "AGENTS.md").read_bytes(), expected.read_bytes())

    def test_readme_preserves_introduction_and_puts_links_near_bottom(self):
        write_publication_docs(self.app, self.engineering)
        result = (self.app / "README.md").read_text()
        self.assertEqual(result.split("## Feedback", 1)[0], self.readme.split("## Feedback", 1)[0])
        self.assertIn(self.introduction, result)
        self.assertLess(result.index("## Feedback"), result.index("## Source and development"))
        self.assertLess(result.index("## Source and development"), result.index("## License"))
        self.assertIn("[BUILD.md](BUILD.md)", result)
        self.assertIn("[VERIFY.md](VERIFY.md)", result)

    def test_build_and_verification_guidance_require_patch_first(self):
        write_publication_docs(self.app, self.engineering)
        build = (self.app / "BUILD.md").read_text()
        verify = (self.app / "VERIFY.md").read_text()
        self.assertIn("Do not manually edit application code", build)
        self.assertIn("**Direct build readiness: BLOCKED.**", build)
        self.assertIn("must originate as reviewed patch layers", verify)
        self.assertNotIn("Contribute normal application changes here", verify)


if __name__ == "__main__":
    unittest.main()
