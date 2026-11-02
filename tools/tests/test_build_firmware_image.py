import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_firmware_image import image_name, read_ota_url, set_ota_url, slugify, store_image  # noqa: E402


class NamingTests(unittest.TestCase):
    def test_slugify_cleans_and_shortens(self):
        self.assertEqual(slugify("Keep Open: 20 s!"), "keep-open-20-s")
        self.assertEqual(slugify("???"), "build")
        self.assertLessEqual(len(slugify("a" * 80)), 40)

    def test_image_name_marks_uncommitted_changes(self):
        created = datetime(2026, 11, 2, 1, 40, 5)
        self.assertEqual(image_name(created, "keep open", "4632dc5", False), "20261102-014005-keep-open-4632dc5")
        self.assertEqual(image_name(created, "x", "4632dc5", True), "20261102-014005-x-4632dc5-dirty")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_reads_url_and_rejects_placeholder(self):
        env = self.dir / "env"
        env.write_text("OTA_URL=http://192.0.2.10:8003/xiaozhi/ota/\n", encoding="utf-8")
        self.assertEqual(read_ota_url(env), "http://192.0.2.10:8003/xiaozhi/ota/")
        env.write_text("OTA_URL=http://YOUR_SERVER_ADDRESS:8003/\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            read_ota_url(env)

    def test_missing_file_stops(self):
        with self.assertRaises(SystemExit):
            read_ota_url(self.dir / "absent")

    def test_replaces_only_the_url(self):
        sdkconfig = self.dir / "sdkconfig"
        sdkconfig.write_text('A=1\nCONFIG_OTA_URL="https://old/"\nB=2\n', encoding="utf-8")
        set_ota_url(sdkconfig, "http://new/")
        self.assertEqual(sdkconfig.read_text(encoding="utf-8"), 'A=1\nCONFIG_OTA_URL="http://new/"\nB=2\n')


class StoreImageTests(unittest.TestCase):
    def test_copies_flash_files_without_merged_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build = root / "build"
            (build / "bootloader").mkdir(parents=True)
            (build / "bootloader" / "bootloader.bin").write_bytes(b"b")
            (build / "app.bin").write_bytes(b"a")
            (build / "merged-binary.bin").write_bytes(b"m")
            (build / "flasher_args.json").write_text(
                json.dumps({"flash_files": {"0x0": "bootloader/bootloader.bin", "0x20000": "app.bin"}}),
                encoding="utf-8",
            )
            sdkconfig = root / "sdkconfig"
            sdkconfig.write_text("X=1\n", encoding="utf-8")
            lock = root / "dependencies.lock"
            lock.write_text("version: 3\n", encoding="utf-8")
            destination = root / "dist" / "image"

            store_image(build, sdkconfig, lock, destination, {"purpose": "test"})

            self.assertTrue((destination / "bootloader" / "bootloader.bin").is_file())
            self.assertTrue((destination / "flasher_args.json").is_file())
            self.assertFalse((destination / "merged-binary.bin").exists())
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["purpose"], "test")
            self.assertEqual(set(manifest["sha256"]), {"bootloader/bootloader.bin", "app.bin", "dependencies.lock"})
            self.assertTrue((destination / "dependencies.lock").is_file())


if __name__ == "__main__":
    unittest.main()


class SelectionTests(unittest.TestCase):
    def test_invalid_board_variant_stops_before_build(self):
        import build_firmware_image as image
        from unittest import mock
        for board, variant in [('elena-stackchan','m5stack-core-s3'), ('missing','missing')]:
            with self.subTest(board=board), mock.patch.object(image, 'run') as build:
                with self.assertRaises((ValueError, SystemExit)):
                    image.resolve_selection(board, variant)
                build.assert_not_called()

    def test_selected_board_reaches_build_and_manifest(self):
        self.check_image('elena-stackchan', 'elena-stackchan', 'CONFIG_BOARD_TYPE_ELENA_STACKCHAN')

    def test_generic_cores3_override(self):
        self.check_image('m5stack/core-s3', 'm5stack-core-s3', 'CONFIG_BOARD_TYPE_M5STACK_CORE_S3')

    def check_image(self, board, variant, symbol):
        import build_firmware_image as image
        from unittest import mock
        # The actual selection resolver uses the real versioned board configuration.
        self.assertEqual(image.resolve_selection(board, variant), symbol)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root/'firmware'
            build = firmware/'build'
            build.mkdir(parents=True)
            (firmware/'dependencies.lock').write_text('version: 3\n')
            calls = []
            def run(*args):
                calls.append(args)
                (firmware/'sdkconfig').write_text(f'{symbol}=y\nCONFIG_OTA_URL="https://test.invalid/ota/"\n')
                (build/'app.bin').write_bytes(b'app')
                (build/'flasher_args.json').write_text(json.dumps({'flash_files': {'0x20000':'app.bin'}}))
            with mock.patch.object(image, 'FIRMWARE', firmware), mock.patch.object(image, 'ROOT', root), \
                 mock.patch.object(image, 'DIST', root/'dist'), mock.patch.object(image, 'git', return_value='abc1234'), \
                 mock.patch.object(image, 'run', side_effect=run), mock.patch.object(image, 'source_fingerprint', return_value='snapshot'), mock.patch.object(image, 'resolve_selection', return_value=symbol), \
                 mock.patch.dict('os.environ', {'IDF_PATH':'fake'}), \
                 mock.patch.object(sys, 'argv', ['image','--board',board,'--variant',variant,'--xiaozhi-cloud']):
                self.assertEqual(image.main(), 0)
            self.assertIn(board, calls[0])
            self.assertIn(variant, calls[0])
            manifest = json.loads(next((root/'dist').glob('*/manifest.json')).read_text())
            self.assertEqual((manifest['board'],manifest['variant']), (board,variant))
            self.assertIn(f'{symbol}=y', next((root/'dist').glob('*/sdkconfig')).read_text())

class SourceIdentityTests(unittest.TestCase):
    def test_fingerprint_includes_tracked_and_new_source_but_not_ignored_build(self):
        import build_firmware_image as image
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'.gitignore').write_text('build/\n')
            (root/'main.cc').write_text('first\n')
            subprocess.run(['git','-C',str(root),'add','.gitignore','main.cc'],check=True)
            a=image.source_fingerprint(root)
            (root/'build').mkdir()
            (root/'build/app.bin').write_bytes(b'generated')
            self.assertEqual(a,image.source_fingerprint(root))
            (root/'main.cc').write_text('second\n')
            b=image.source_fingerprint(root)
            self.assertNotEqual(a,b)
            (root/'new.cc').write_text('new source\n')
            self.assertNotEqual(b,image.source_fingerprint(root))
