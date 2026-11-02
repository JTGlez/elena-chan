import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gen_server_config import PROFILES, generate  # noqa: E402


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


class MinimaxProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.env = self.dir / "minimax.env"
        self.env.write_text("MINIMAX_API_KEY=abc\n", encoding="utf-8")
        self.firmware_env = self.dir / "firmware.local.env"
        self.firmware_env.write_text(
            "OTA_URL=http://192.0.2.10:8003/xiaozhi/ota/\n", encoding="utf-8"
        )
        self.output = self.dir / ".config.yaml"

    def run_generate(self):
        generate(
            PROFILES["minimax"].template,
            self.env,
            self.output,
            key_name="MINIMAX_API_KEY",
            firmware_env=self.firmware_env,
        )
        return self.output.read_text(encoding="utf-8")

    def test_fills_every_placeholder(self):
        content = self.run_generate()
        self.assertNotIn("@", content)
        self.assertIn('api_key: "abc"', content)
        self.assertIn('websocket: "ws://192.0.2.10:8000/xiaozhi/v1/"', content)
        self.assertIn('vision_explain: "http://192.0.2.10:8003/mcp/vision/explain"', content)

    def test_refuses_the_example_address(self):
        self.firmware_env.write_text(
            "OTA_URL=http://YOUR_SERVER_ADDRESS:8003/xiaozhi/ota/\n", encoding="utf-8"
        )
        with self.assertRaises(SystemExit):
            self.run_generate()
        self.assertFalse(self.output.exists())

    def test_refuses_an_empty_key(self):
        self.env.write_text("MINIMAX_API_KEY=\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_generate()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
