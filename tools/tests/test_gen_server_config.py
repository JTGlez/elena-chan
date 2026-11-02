import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gen_server_config import generate  # noqa: E402


class GenerateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.template = self.dir / "template.yaml"
        self.template.write_text("api_key: @BASALT_API_KEY@\n", encoding="utf-8")
        self.env = self.dir / ".env"
        self.env.write_text("# comment\nBASALT_API_KEY=\"abc\"\n", encoding="utf-8")
        self.output = self.dir / "data" / ".config.yaml"

    def test_substitutes_key_and_restricts_permissions(self):
        generate(self.template, self.env, self.output)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "api_key: abc\n")
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)

    def test_keeps_existing_config_without_force(self):
        self.output.parent.mkdir()
        self.output.write_text("mine", encoding="utf-8")
        with self.assertRaises(SystemExit):
            generate(self.template, self.env, self.output)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "mine")

    def test_force_replaces_existing_config(self):
        self.output.parent.mkdir()
        self.output.write_text("mine", encoding="utf-8")
        generate(self.template, self.env, self.output, force=True)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "api_key: abc\n")

    def test_fails_when_key_is_missing(self):
        self.env.write_text("OTHER=1\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            generate(self.template, self.env, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
