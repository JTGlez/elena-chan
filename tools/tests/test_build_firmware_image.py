import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_firmware_image import (  # noqa: E402
    avatar_dir, check_avatar_assets, enable_kconfig, image_name, read_ota_url, set_avatar,
    set_ota_url, slugify, store_image,
)


class KconfigTests(unittest.TestCase):
    def write(self, directory, text):
        path = Path(directory) / "sdkconfig"
        path.write_text(text, encoding="utf-8")
        return path

    def test_enable_turns_on_an_unset_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "A=1\n# CONFIG_ELENA_SCREEN_STREAM is not set\nB=2\n")
            enable_kconfig(path, "CONFIG_ELENA_SCREEN_STREAM")
            self.assertEqual(path.read_text(), "A=1\nCONFIG_ELENA_SCREEN_STREAM=y\nB=2\n")

    def test_enable_refuses_an_option_the_build_does_not_have(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "A=1\n")
            with self.assertRaises(SystemExit):
                enable_kconfig(path, "CONFIG_ELENA_SCREEN_STREAM")


class AvatarTests(unittest.TestCase):
    def test_set_avatar_writes_the_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sdkconfig"
            path.write_text('A=1\nCONFIG_ELENA_AVATAR="elena"\nB=2\n', encoding="utf-8")
            set_avatar(path, "emoji")
            self.assertEqual(path.read_text(), 'A=1\nCONFIG_ELENA_AVATAR="emoji"\nB=2\n')

    def test_set_avatar_refuses_a_build_without_the_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sdkconfig"
            path.write_text("A=1\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                set_avatar(path, "emoji")

    def make_avatar(self, firmware, name, renderer="gif"):
        folder = firmware / "main" / "boards" / "elena-stackchan" / "avatars" / name
        folder.mkdir(parents=True)
        (folder / "avatar.json").write_text(json.dumps({"renderer": renderer}), encoding="utf-8")
        return folder

    def test_avatar_dir_refuses_bad_names(self):
        import build_firmware_image as image
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            firmware = Path(tmp)
            self.make_avatar(firmware, "real")
            (firmware / "main" / "boards" / "elena-stackchan" / "avatars" / "empty").mkdir()
            with mock.patch.object(image, "FIRMWARE", firmware):
                for name in ("../x", "/etc", "Emoji", "", "missing", "empty"):
                    with self.subTest(name=name), self.assertRaises(SystemExit) as stopped:
                        avatar_dir(name)
                    self.assertIn(name, str(stopped.exception))

    def test_avatar_dir_returns_the_folder(self):
        import build_firmware_image as image
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            firmware = Path(tmp)
            folder = self.make_avatar(firmware, "real_one-2")
            with mock.patch.object(image, "FIRMWARE", firmware):
                self.assertEqual(avatar_dir("real_one-2"), folder)

    def write_assets(self, directory, data):
        path = Path(directory) / "generated_assets.bin"
        path.write_bytes(data)
        return path

    def test_gif_avatar_assets_must_contain_gifs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_avatar(Path(tmp), "a", "gif")
            check_avatar_assets(self.write_assets(tmp, b"xxGIF89axx"), folder)
            with self.assertRaises(SystemExit):
                check_avatar_assets(self.write_assets(tmp, b"xxPNGxx"), folder)

    def test_image_avatar_assets_must_not_contain_gifs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_avatar(Path(tmp), "a", "image")
            check_avatar_assets(self.write_assets(tmp, b"xxPNGxx"), folder)
            with self.assertRaises(SystemExit):
                check_avatar_assets(self.write_assets(tmp, b"xxGIF89axx"), folder)


class AvatarSelectionTests(unittest.TestCase):
    """main() validates the avatar before it spends minutes on a build."""

    def run_main(self, *arguments):
        import build_firmware_image as image
        from unittest import mock
        with mock.patch.object(image, "run") as build, \
                mock.patch.object(image, "resolve_selection", return_value="CONFIG_X"), \
                mock.patch.dict("os.environ", {"IDF_PATH": "fake"}), \
                mock.patch.object(sys, "argv", ["image", "--xiaozhi-cloud", *arguments]), \
                self.assertRaises(SystemExit) as stopped:
            image.main()
        build.assert_not_called()
        return stopped.exception

    def test_missing_avatar_stops_before_the_build(self):
        self.assertIn("nope", str(self.run_main("--avatar", "nope")))

    def test_avatar_with_another_board_stops_before_the_build(self):
        exit_error = self.run_main("--board", "m5stack/core-s3", "--variant", "m5stack-core-s3",
                                   "--avatar", "emoji")
        self.assertEqual(exit_error.code, 2)


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

    def test_app_built_in_another_language_is_not_packaged(self):
        with self.assertRaises(SystemExit) as stopped:
            self.check_image('elena-stackchan', 'elena-stackchan', 'CONFIG_BOARD_TYPE_ELENA_STACKCHAN',
                             app_bytes='聆听中...'.encode())
        self.assertIn('es-ES', str(stopped.exception))

    def check_image(self, board, variant, symbol, app_bytes=b'app Escuchando... app'):
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
            locale = firmware/'main/assets/locales/es-ES'
            locale.mkdir(parents=True)
            (locale/'language.json').write_text(json.dumps({'strings': {'LISTENING': 'Escuchando...'}}))
            elena=firmware/'main/boards/elena-stackchan/avatars/elena'
            elena.mkdir(parents=True)
            (elena/'avatar.json').write_text(json.dumps({'renderer': 'gif'}))
            calls = []
            def run(*args):
                calls.append(args)
                (firmware/'sdkconfig').write_text(f'{symbol}=y\nCONFIG_OTA_URL="https://test.invalid/ota/"\n')
                (build/'app.bin').write_bytes(app_bytes)
                (build/'generated_assets.bin').write_bytes(b'GIF89a')
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
