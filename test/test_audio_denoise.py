import io
import shutil
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import audio_denoise as denoise


class PureFunctionTests(unittest.TestCase):
    def test_build_filter_chain_uses_confirmed_narrow_notches(self):
        self.assertTrue(hasattr(denoise, "build_filter_chain"))
        self.assertEqual(
            denoise.build_filter_chain("hum"),
            ",".join(
                (
                    "highpass=f=28:p=2:r=f64",
                    "bandreject=f=50.16:t=h:w=1.8:r=f64",
                    "bandreject=f=150.49:t=h:w=3.5:r=f64",
                )
            ),
        )

    def test_build_filter_chain_broadband_uses_arnndn_and_afftdn(self):
        chain = denoise.build_filter_chain("broadband")
        self.assertIn("highpass=f=28", chain)
        self.assertIn("arnndn=m=", chain)
        self.assertIn("mix=0.78", chain)
        self.assertIn("cb.rnnn", chain)
        self.assertIn("afftdn=", chain)
        self.assertIn("treble=", chain)

    def test_build_filter_chain_rejects_unknown_mode(self):
        with self.assertRaisesRegex(ValueError, "Unsupported mode"):
            denoise.build_filter_chain("magic")

    def test_find_audio_files_is_recursive_case_insensitive_and_excludes_output(self):
        self.assertTrue(hasattr(denoise, "find_audio_files"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_root = root / "out"
            expected = [root / "a.mp3", root / "nested" / "b.WAV", root / "z.flac"]
            processed = root / "a_processed.mp3"
            for path in (
                *expected,
                root / "notes.txt",
                output_root / "generated.mp3",
                processed,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            self.assertEqual(denoise.find_audio_files(root, output_root), sorted(expected))
            self.assertEqual(
                denoise.find_audio_files(root, None),
                sorted([*expected, output_root / "generated.mp3"]),
            )

    def test_find_audio_files_rejects_unsupported_single_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "recording.aac"
            source.touch()

            with self.assertRaisesRegex(ValueError, "Unsupported audio format"):
                denoise.find_audio_files(source, None)

    def test_output_paths_use_processed_suffix_and_optional_output_root(self):
        self.assertTrue(hasattr(denoise, "resolve_output_root"))
        self.assertTrue(hasattr(denoise, "destination_for"))
        input_root = Path("C:/input")
        source = input_root / "album" / "track.mp3"
        output_root = Path("C:/output")

        self.assertIsNone(denoise.resolve_output_root(input_root, None))
        self.assertEqual(
            denoise.resolve_output_root(input_root, output_root), output_root.resolve()
        )
        self.assertEqual(
            denoise.destination_for(input_root, source, None),
            (source.parent / "track_processed.mp3").resolve(),
        )
        self.assertEqual(
            denoise.destination_for(input_root, source, output_root),
            (output_root / "album" / "track_processed.mp3").resolve(),
        )
        self.assertEqual(
            denoise.destination_for(source, source, output_root),
            (output_root / "track_processed.mp3").resolve(),
        )
        self.assertNotEqual(
            denoise.destination_for(source, source, None),
            source.resolve(),
        )

    def test_build_ffmpeg_command_uses_argument_list_metadata_and_output_codec(self):
        self.assertTrue(hasattr(denoise, "build_ffmpeg_command"))
        source = Path("C:/含 空格/输入.mp3")
        temporary_output = Path("C:/含 空格/.输出.tmp.mp3")

        command = denoise.build_ffmpeg_command("ffmpeg.exe", source, temporary_output)

        self.assertEqual(command[0], "ffmpeg.exe")
        self.assertIn(str(source), command)
        self.assertIn(str(temporary_output), command)
        self.assertIn("-map_metadata", command)
        self.assertIn("-af", command)
        self.assertIn(denoise.build_filter_chain("hum"), command)
        self.assertEqual(command[-5:], ["-c:a", "libmp3lame", "-q:a", "2", str(temporary_output)])

    def test_build_ffmpeg_command_passes_broadband_filter_chain(self):
        command = denoise.build_ffmpeg_command(
            "ffmpeg",
            Path("C:/input.mp3"),
            Path("C:/output.tmp.mp3"),
            mode="broadband",
        )
        self.assertIn(denoise.build_filter_chain("broadband"), command)

    def test_build_ffmpeg_command_selects_wav_and_flac_codecs(self):
        cases = {
            ".wav": ["-c:a", "pcm_s24le"],
            ".flac": ["-c:a", "flac"],
        }
        for suffix, expected_codec in cases.items():
            with self.subTest(suffix=suffix):
                output = Path(f"C:/output.tmp{suffix}")
                command = denoise.build_ffmpeg_command(
                    "ffmpeg", Path(f"C:/input{suffix}"), output
                )
                self.assertEqual(command[-3:], [*expected_codec, str(output)])


class ProcessFileTests(unittest.TestCase):
    def test_keep_existing_skips_output_without_running_ffmpeg(self):
        self.assertTrue(hasattr(denoise, "process_file"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp3"
            destination = root / "output.mp3"
            source.write_bytes(b"source")
            destination.write_bytes(b"existing")

            def runner(*_args, **_kwargs):
                self.fail("runner must not be called for an existing output")

            result = denoise.process_file(
                "ffmpeg", source, destination, True, runner=runner
            )

            self.assertEqual(result.status, "skipped")
            self.assertEqual(destination.read_bytes(), b"existing")

    def test_existing_output_is_replaced_by_default(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp3"
            destination = root / "output.mp3"
            source.write_bytes(b"source")
            destination.write_bytes(b"existing")

            def runner(command, **_kwargs):
                Path(command[-1]).write_bytes(b"replacement")
                return CompletedProcess(command, 0, "", "")

            result = denoise.process_file(
                "ffmpeg", source, destination, False, runner=runner
            )

            self.assertEqual(result.status, "processed")
            self.assertEqual(destination.read_bytes(), b"replacement")

    def test_successful_process_atomically_publishes_temporary_output(self):
        self.assertTrue(hasattr(denoise, "process_file"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp3"
            destination = root / "nested" / "output.mp3"
            source.write_bytes(b"source")

            def runner(command, **_kwargs):
                Path(command[-1]).write_bytes(b"processed")
                return CompletedProcess(command, 0, "", "")

            result = denoise.process_file(
                "ffmpeg", source, destination, keep_existing=False, runner=runner
            )

            self.assertEqual(result.status, "processed")
            self.assertEqual(destination.read_bytes(), b"processed")
            self.assertEqual(list(destination.parent.glob("*.tmp.mp3")), [])

    def test_failed_process_removes_partial_temporary_output(self):
        self.assertTrue(hasattr(denoise, "process_file"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp3"
            destination = root / "output.mp3"
            source.write_bytes(b"source")

            def runner(command, **_kwargs):
                Path(command[-1]).write_bytes(b"partial")
                return CompletedProcess(command, 1, "", "encoder failed")

            result = denoise.process_file(
                "ffmpeg", source, destination, keep_existing=False, runner=runner
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("encoder failed", result.detail)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob("*.tmp.mp3")), [])

    def test_success_return_without_temporary_output_is_failure(self):
        self.assertTrue(hasattr(denoise, "process_file"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp3"
            destination = root / "output.mp3"
            source.write_bytes(b"source")

            def runner(command, **_kwargs):
                return CompletedProcess(command, 0, "", "")

            result = denoise.process_file(
                "ffmpeg", source, destination, keep_existing=False, runner=runner
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("no output", result.detail.lower())
            self.assertFalse(destination.exists())

    def test_uncreatable_output_parent_is_reported_as_file_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp3"
            blocker = root / "not-a-directory"
            destination = blocker / "output.mp3"
            source.write_bytes(b"source")
            blocker.write_bytes(b"file blocks directory creation")

            def runner(*_args, **_kwargs):
                self.fail("runner must not be called when output setup fails")

            result = denoise.process_file(
                "ffmpeg", source, destination, keep_existing=False, runner=runner
            )

            self.assertEqual(result.status, "failed")
            self.assertTrue(result.detail)


class CliTests(unittest.TestCase):
    def test_parse_args_defaults_to_replacing_existing_outputs(self):
        self.assertTrue(hasattr(denoise, "parse_args"))
        arguments = denoise.parse_args(["-i", "input"])
        self.assertTrue(hasattr(arguments, "keep_existing"))
        self.assertFalse(arguments.keep_existing)
        self.assertEqual(arguments.mode, "hum")

    def test_parse_args_accepts_mode_short_and_long_options(self):
        for option in ("-m", "--mode"):
            with self.subTest(option=option):
                arguments = denoise.parse_args(["-i", "input", option, "broadband"])
                self.assertEqual(arguments.mode, "broadband")

    def test_parse_args_accepts_keep_existing_short_and_long_options(self):
        for option in ("-k", "--keep-existing"):
            with self.subTest(option=option):
                try:
                    arguments = denoise.parse_args(["-i", "input", option])
                except SystemExit:
                    self.fail(f"{option} was not accepted")
                self.assertTrue(arguments.keep_existing)

    def test_parse_args_accepts_output_short_and_long_options(self):
        for option in ("-o", "--output"):
            with self.subTest(option=option):
                arguments = denoise.parse_args(["-i", "input", option, "output"])
                self.assertEqual(arguments.input, Path("input"))
                self.assertEqual(arguments.output, Path("output"))

    def test_run_reports_missing_input(self):
        self.assertTrue(hasattr(denoise, "run"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            error_output = io.StringIO()
            with redirect_stderr(error_output):
                exit_code = denoise.run(
                    Path(temporary_directory) / "missing", None, False, "ffmpeg"
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("does not exist", error_output.getvalue())

    def test_run_reports_directory_without_supported_audio(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "notes.txt").write_text("not audio", encoding="utf-8")
            error_output = io.StringIO()
            with redirect_stderr(error_output):
                exit_code = denoise.run(root, None, False, "ffmpeg")

        self.assertEqual(exit_code, 1)
        self.assertIn("no supported audio", error_output.getvalue())

    def test_default_output_writes_processed_beside_source(self):
        self.assertTrue(hasattr(denoise, "run"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp3"
            source.write_bytes(b"source")
            destination = root / "source_processed.mp3"

            def fake_runner(command, **_kwargs):
                Path(command[-1]).write_bytes(b"processed")
                return CompletedProcess(command, 0, stdout="", stderr="")

            with patch("audio_denoise.subprocess.run", side_effect=fake_runner):
                with redirect_stdout(io.StringIO()):
                    exit_code = denoise.run(source, None, False, "ffmpeg")

            self.assertEqual(exit_code, 0)
            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(), b"source")
            self.assertTrue(destination.exists())
            self.assertEqual(destination.read_bytes(), b"processed")

    def test_run_rejects_output_that_would_replace_source(self):
        self.assertTrue(hasattr(denoise, "run"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp3"
            source.write_bytes(b"source")
            error_output = io.StringIO()
            with patch(
                "audio_denoise.destination_for",
                return_value=source.resolve(),
            ):
                with redirect_stderr(error_output), redirect_stdout(io.StringIO()):
                    exit_code = denoise.run(source, root, True, "ffmpeg")

        self.assertEqual(exit_code, 1)
        self.assertIn("source file", error_output.getvalue())

    def test_main_reports_unavailable_ffmpeg(self):
        self.assertTrue(hasattr(denoise, "main"))
        error_output = io.StringIO()
        with patch("audio_denoise.shutil.which", return_value=None):
            with redirect_stderr(error_output):
                exit_code = denoise.main(["-i", "input"])

        self.assertEqual(exit_code, 1)
        self.assertIn("FFmpeg", error_output.getvalue())

    def test_main_configures_reconfigurable_standard_streams_as_utf8(self):
        class ReconfigurableBuffer(io.StringIO):
            configuration = None

            def reconfigure(self, **configuration):
                self.configuration = configuration

        standard_output = ReconfigurableBuffer()
        error_output = ReconfigurableBuffer()
        with patch("audio_denoise.shutil.which", return_value=None):
            with redirect_stdout(standard_output), redirect_stderr(error_output):
                denoise.main(["-i", "input"])

        expected = {"encoding": "utf-8", "errors": "replace"}
        self.assertEqual(standard_output.configuration, expected)
        self.assertEqual(error_output.configuration, expected)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_run_continues_after_one_real_ffmpeg_failure(self):
        self.assertTrue(hasattr(denoise, "run"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_root = root / "input"
            output_root = root / "output"
            input_root.mkdir()
            (input_root / "a_invalid.mp3").write_bytes(b"not audio")
            with wave.open(str(input_root / "b_valid.wav"), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(8000)
                wav_file.writeframes(b"\x00\x00" * 800)

            standard_output = io.StringIO()
            with redirect_stdout(standard_output):
                exit_code = denoise.run(
                    input_root, output_root, False, shutil.which("ffmpeg")
                )

            self.assertEqual(exit_code, 1)
            self.assertFalse((output_root / "a_invalid_processed.mp3").exists())
            self.assertTrue((output_root / "b_valid_processed.wav").exists())
            self.assertIn("[FAIL]", standard_output.getvalue())
            self.assertIn("[OK]", standard_output.getvalue())
            self.assertIn("failed=1", standard_output.getvalue())


class DocumentationTests(unittest.TestCase):
    def test_readme_documents_command_formats_and_ffmpeg_requirement(self):
        readme = ROOT / "README.md"
        self.assertTrue(readme.exists())
        content = readme.read_text(encoding="utf-8")
        self.assertIn("python audio_denoise.py", content)
        self.assertIn("FFmpeg", content)
        self.assertIn("--keep-existing", content)
        self.assertIn("--output", content)
        self.assertIn("--help", content)
        self.assertNotIn("--overwrite", content)
        for suffix in (".mp3", ".wav", ".flac"):
            self.assertIn(suffix, content)


if __name__ == "__main__":
    unittest.main()
