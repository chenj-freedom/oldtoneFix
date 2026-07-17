import math
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import oldtonefix_web as web


def default_payload():
    return {
        "input": "C:/audio/source",
        "output": "C:/audio/output",
        "keep_existing": False,
        "highpass_hz": 28,
        "rnnoise_mix": 0.78,
        "afftdn_nr": 14,
        "afftdn_nf": -50,
        "afftdn_nt": "white",
        "afftdn_tn": True,
        "treble_gain": -2.5,
        "treble_hz": 7000,
        "treble_width": 0.6,
    }


class WebBoundaryTests(unittest.TestCase):
    def test_build_command_maps_every_tuning_value(self):
        payload = default_payload()
        command = web.build_denoise_command(payload, script=Path("C:/repo/scripts/oldtonefix.py"))

        self.assertEqual(
            command[:5],
            [sys.executable, "-u", str(Path("C:/repo/scripts/oldtonefix.py")), "--input", payload["input"]],
        )
        self.assertIn("--progress-json", command)
        expected_pairs = {
            "--output": "C:/audio/output",
            "--highpass-hz": "28",
            "--rnnoise-mix": "0.78",
            "--afftdn-nr": "14",
            "--afftdn-nf": "-50",
            "--afftdn-nt": "white",
            "--treble-gain": "-2.5",
            "--treble-hz": "7000",
            "--treble-width": "0.6",
        }
        for option, value in expected_pairs.items():
            position = command.index(option)
            self.assertEqual(command[position + 1], value)
        self.assertIn("--afftdn-tn", command)
        self.assertNotIn("--keep-existing", command)

    def test_build_command_maps_boolean_switches(self):
        payload = default_payload()
        payload["keep_existing"] = True
        payload["afftdn_tn"] = False

        command = web.build_denoise_command(payload)

        self.assertIn("--keep-existing", command)
        self.assertIn("--no-afftdn-tn", command)
        self.assertNotIn("--afftdn-tn", command)

    def test_validation_rejects_missing_input_invalid_enum_and_boolean(self):
        cases = []
        for key, value in (("input", ""), ("afftdn_nt", "tape"), ("afftdn_tn", "yes")):
            payload = default_payload()
            payload[key] = value
            cases.append(payload)

        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                web.validate_job_payload(payload)

    def test_validation_rejects_out_of_range_and_non_finite_numbers(self):
        for key, value in (
            ("highpass_hz", 0),
            ("rnnoise_mix", 1.01),
            ("afftdn_nr", 100),
            ("afftdn_nf", -81),
            ("treble_gain", math.inf),
            ("treble_hz", 999),
            ("treble_width", math.nan),
        ):
            payload = default_payload()
            payload[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                web.validate_job_payload(payload)

    def test_validation_uses_defaults_for_omitted_tuning_values(self):
        validated = web.validate_job_payload({"input": "recording.mp3"})

        self.assertEqual(validated["highpass_hz"], 28)
        self.assertEqual(validated["rnnoise_mix"], 0.78)
        self.assertEqual(validated["afftdn_nt"], "white")
        self.assertTrue(validated["afftdn_tn"])
        self.assertFalse(validated["keep_existing"])

    def test_detect_tools_reports_ffmpeg_and_model(self):
        with patch.object(web.shutil, "which", return_value="C:/ffmpeg/bin/ffmpeg.exe"), patch.object(
            web, "DEFAULT_RNN_MODEL", Path(__file__)
        ):
            result = web.detect_tools()

        self.assertTrue(result["ok"])
        self.assertTrue(result["ffmpeg"])
        self.assertTrue(result["model"])

    def test_static_path_stays_inside_web_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            index = root / "index.html"
            index.write_text("ok", encoding="utf-8")

            self.assertEqual(web.resolve_static_path("/", root), index)
            self.assertIsNone(web.resolve_static_path("/../scripts/oldtonefix.py", root))


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


class WebJobTests(unittest.TestCase):
    def tearDown(self):
        with web.JOBS_LOCK:
            web.JOBS.clear()

    def test_create_job_has_safe_snapshot_and_validated_command(self):
        job_id = web.create_job(default_payload(), start_thread=False)

        snapshot = web.snapshot_job(job_id)

        self.assertEqual(snapshot["id"], job_id)
        self.assertEqual(snapshot["status"], "running")
        self.assertNotIn("process", snapshot)
        self.assertIn("--rnnoise-mix", snapshot["command"])
        self.assertEqual(snapshot["progress"]["completed"], 0)
        self.assertEqual(snapshot["progress"]["total"], 0)

    def test_run_job_streams_logs_and_progress_before_process_exit(self):
        job_id = web.create_job(default_payload(), start_thread=False)
        event = json.dumps({"completed": 7, "total": 12, "current": "seven.mp3", "status": "processed"})
        script = (
            "import time; "
            "print('first file finished', flush=True); "
            f"print({(web.PROGRESS_PREFIX + event)!r}, flush=True); "
            "time.sleep(1)"
        )
        thread = threading.Thread(
            target=web.run_job,
            args=(job_id, [sys.executable, "-u", "-c", script]),
        )
        thread.start()
        deadline = time.monotonic() + 0.8
        snapshot = web.snapshot_job(job_id)
        while time.monotonic() < deadline and snapshot["progress"]["completed"] != 7:
            time.sleep(0.02)
            snapshot = web.snapshot_job(job_id)

        self.assertTrue(thread.is_alive(), "progress must arrive before the process exits")
        self.assertIn("first file finished", snapshot["logs"])
        self.assertFalse(any(line.startswith(web.PROGRESS_PREFIX) for line in snapshot["logs"]))
        self.assertEqual(snapshot["progress"]["completed"], 7)
        self.assertEqual(snapshot["progress"]["total"], 12)
        self.assertEqual(snapshot["progress"]["percent"], 58)
        thread.join(timeout=2)

    def test_run_job_captures_utf8_output_and_completion(self):
        job_id = web.create_job(default_payload(), start_thread=False)
        command = [sys.executable, "-c", "print('处理完成')"]

        web.run_job(job_id, command)
        snapshot = web.snapshot_job(job_id)

        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["return_code"], 0)
        self.assertIn("处理完成", snapshot["logs"])

    def test_run_job_records_process_start_failure(self):
        job_id = web.create_job(default_payload(), start_thread=False)

        with patch.object(web.subprocess, "Popen", side_effect=OSError("cannot start")):
            web.run_job(job_id, ["missing-program"])

        snapshot = web.snapshot_job(job_id)
        self.assertEqual(snapshot["status"], "failed")
        self.assertIn("cannot start", snapshot["error"])

    def test_stop_job_terminates_active_process(self):
        job_id = web.create_job(default_payload(), start_thread=False)
        process = FakeProcess()
        with web.JOBS_LOCK:
            web.JOBS[job_id]["process"] = process

        web.stop_job(job_id)

        self.assertTrue(process.terminated)
        self.assertEqual(web.snapshot_job(job_id)["status"], "stopped")

    def test_health_endpoint_returns_json(self):
        server = web.create_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            with urlopen(f"http://{host}:{port}/api/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertIn("ok", payload)
            self.assertIn("ffmpeg", payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_parser_defaults_open_browser_on_preferred_port(self):
        arguments = web.create_parser().parse_args([])

        self.assertEqual(arguments.port, web.DEFAULT_PORT)
        self.assertFalse(arguments.no_open)


class InputCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(dict(attrs))


class TuningPageCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pages = []
        self.depth = 0
        self.card_count = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if tag == "div" and "tuning-page" in classes:
            self.depth = 1
            self.card_count = 0
        elif self.depth and tag == "div":
            self.depth += 1
        elif self.depth and tag == "article" and "slider-card" in classes:
            self.card_count += 1

    def handle_endtag(self, tag):
        if self.depth and tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.pages.append(self.card_count)


class FrontendContractTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.styles = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        translations_path = ROOT / "web" / "i18n.js"
        self.translations = translations_path.read_text(encoding="utf-8") if translations_path.exists() else ""

    def test_language_switch_loads_translations_before_the_application(self):
        self.assertIn('id="language-switch"', self.html)
        self.assertEqual(self.html.count('data-language="zh"'), 1)
        self.assertEqual(self.html.count('data-language="en"'), 1)
        self.assertLess(self.html.index('src="/i18n.js"'), self.html.index('src="/app.js"'))
        self.assertIn(".language-switch", self.styles)
        self.assertIn('.language-option[aria-pressed="true"]', self.styles)

    def test_every_marked_translation_key_exists_in_both_languages(self):
        keys = re.findall(r'data-i18n(?:-placeholder|-aria-label)?="([^"]+)"', self.html)

        self.assertGreaterEqual(len(keys), 45)
        self.assertIn("window.OLDTONEFIX_TRANSLATIONS", self.translations)
        for key in set(keys):
            with self.subTest(key=key):
                self.assertGreaterEqual(self.translations.count(f'"{key}"'), 2)

    def test_language_switch_is_instant_persistent_and_translates_runtime_state(self):
        for fragment in (
            "function setLanguage",
            'localStorage.getItem("oldtonefix-language")',
            'localStorage.setItem("oldtonefix-language"',
            "document.documentElement.lang",
            't("status.running")',
            't("progress.count"',
            "renderHealth",
        ):
            self.assertIn(fragment, self.script)
        for phrase in (
            "Select audio",
            "Tune restoration",
            "Start denoising",
            "Processing status",
            "Purpose:",
            "Lower",
            "Higher",
            "Processing progress",
        ):
            self.assertIn(phrase, self.translations)

    def test_all_numeric_tuning_values_are_correctly_initialized_sliders(self):
        parser = InputCollector()
        parser.feed(self.html)
        sliders = {item["id"]: item for item in parser.inputs if item.get("type") == "range"}
        expected = {
            "highpass-hz": ("1", "500", "1", "28"),
            "rnnoise-mix": ("-1", "1", "0.01", "0.78"),
            "afftdn-nr": ("0.01", "97", "0.01", "14"),
            "afftdn-nf": ("-80", "-20", "1", "-50"),
            "treble-gain": ("-20", "20", "0.1", "-2.5"),
            "treble-hz": ("1000", "16000", "100", "7000"),
            "treble-width": ("0.01", "5", "0.01", "0.6"),
        }

        self.assertEqual(set(sliders), set(expected))
        for slider_id, values in expected.items():
            slider = sliders[slider_id]
            self.assertEqual(
                (slider["min"], slider["max"], slider["step"], slider["value"]),
                values,
            )
            self.assertEqual(slider["data-default"], values[3])

    def test_every_slider_explains_purpose_and_both_directions(self):
        for slider_id in (
            "highpass-hz",
            "rnnoise-mix",
            "afftdn-nr",
            "afftdn-nf",
            "treble-gain",
            "treble-hz",
            "treble-width",
        ):
            self.assertIn(f'data-slider="{slider_id}"', self.html)
        for phrase in (
            "用途",
            "越小",
            "越大",
            "保留更多低频",
            "降噪更强",
            "更保守",
            "更明亮",
            "过渡更宽",
        ):
            self.assertIn(phrase, self.html)

    def test_discrete_controls_and_client_api_are_present(self):
        for value in ("white", "vinyl", "shellac"):
            self.assertIn(f'value="{value}"', self.html)
        self.assertIn('id="afftdn-tn"', self.html)
        self.assertIn('id="keep-existing"', self.html)
        self.assertIn('id="reset-tuning"', self.html)
        self.assertIn('fetch("/api/health")', self.script)
        self.assertIn('fetch("/api/jobs"', self.script)
        self.assertIn("/stop`,", self.script)
        self.assertIn("setTimeout", self.script)

    def test_health_status_only_mentions_the_bundled_model_when_it_is_missing(self):
        self.assertIn('key: "health.ready"', self.script)
        self.assertIn('"health.ready": "FFmpeg 已就绪"', self.translations)
        self.assertNotIn("FFmpeg 与 RNNoise 模型已就绪", self.script)
        self.assertIn('missing: { ffmpeg: !data.ffmpeg, model: !data.model }', self.script)
        self.assertIn('if (healthView.missing.model) missing.push(t("health.model"))', self.script)

    def test_layout_has_branded_slider_and_narrow_screen_styles(self):
        self.assertIn("--brand-teal", self.styles)
        self.assertIn('input[type="range"]', self.styles)
        self.assertIn(".default-marker", self.styles)
        self.assertIn("@media (max-width: 820px)", self.styles)

    def test_action_bar_contains_a_job_progress_bar(self):
        for element_id in ("job-progress", "progress-fill", "progress-label", "progress-text"):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("function renderProgress", self.script)
        self.assertIn("renderProgress(job.progress)", self.script)
        self.assertIn('progressFill.style.width = `${percent}%`', self.script)
        self.assertIn(".job-progress", self.styles)
        self.assertIn(".progress-fill", self.styles)

    def test_action_bar_sits_between_source_and_tuning_panels(self):
        source_position = self.html.index('class="panel source-panel"')
        action_position = self.html.index('class="action-bar"')
        tuning_position = self.html.index('class="panel tuning-panel"')

        self.assertLess(source_position, action_position)
        self.assertLess(action_position, tuning_position)

    def test_tuning_controls_are_split_into_four_two_card_pages(self):
        parser = TuningPageCollector()
        parser.feed(self.html)

        self.assertEqual(parser.pages, [2, 2, 2, 2])
        self.assertEqual(self.html.count('class="tuning-page slider-grid" data-page="0"'), 1)
        for page_number in range(1, 4):
            self.assertIn(
                f'class="tuning-page slider-grid" data-page="{page_number}" hidden',
                self.html,
            )

    def test_tuning_pagination_has_accessible_client_controls(self):
        self.assertIn('id="page-prev"', self.html)
        self.assertIn('id="page-next"', self.html)
        self.assertIn('id="page-status"', self.html)
        self.assertEqual(self.html.count('class="page-button"'), 4)
        self.assertIn("const tuningPages", self.script)
        self.assertIn("function showTuningPage", self.script)
        self.assertIn("page.hidden = pageIndex !== currentTuningPage", self.script)
        self.assertIn(".tuning-page[hidden]", self.styles)
        self.assertIn(".tuning-pagination", self.styles)

    def test_default_marker_uses_the_range_thumb_center_coordinates(self):
        self.assertIn("const rangeThumbSize = 19", self.script)
        self.assertIn("slider.clientWidth - rangeThumbSize", self.script)
        self.assertIn("rangeThumbSize / 2 + defaultRatio * usableTrackWidth", self.script)
        self.assertIn('`${defaultPosition}px`', self.script)
        self.assertIn('window.addEventListener("resize", updateAllSliders)', self.script)
        self.assertIn("box-sizing: border-box", self.styles)

    def test_desktop_result_panel_fills_the_remaining_viewport_without_page_scroll(self):
        self.assertIn("@media (min-width: 821px)", self.styles)
        self.assertIn("overflow-y: hidden", self.styles)
        self.assertIn("height: 100vh", self.styles)
        self.assertIn(".result-panel { flex: 1;", self.styles)
        self.assertIn("#log { flex: 1;", self.styles)


class DocumentationTests(unittest.TestCase):
    def test_readmes_document_browser_ui_launch(self):
        chinese = (ROOT / "README.md").read_text(encoding="utf-8")
        english = (ROOT / "README.en.md").read_text(encoding="utf-8")

        for content in (chinese, english):
            self.assertIn("scripts\\oldtonefix_web.py", content)
            self.assertIn("FFmpeg", content)
            self.assertIn("models/cb.rnnn", content)
        self.assertIn("自动打开浏览器", chinese)
        self.assertIn("opens the browser automatically", english)
        self.assertIn("实时进度", chinese)
        self.assertIn("real-time progress", english)
        self.assertIn("保留原文件名", chinese)
        self.assertIn("keeps the original filename", english)
        self.assertIn("不同目录时保留原文件名", (ROOT / "web" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("中文 / EN", chinese)
        self.assertIn("中文 / EN", english)
        self.assertIn("不会中断当前任务", chinese)
        self.assertIn("never interrupts the current task", english)


class ProjectLayoutTests(unittest.TestCase):
    def test_scripts_use_project_specific_names_under_scripts_directory(self):
        self.assertTrue((ROOT / "scripts" / "oldtonefix.py").is_file())
        self.assertTrue((ROOT / "scripts" / "oldtonefix_web.py").is_file())
        self.assertFalse((ROOT / "audio_denoise.py").exists())
        self.assertFalse((ROOT / "audio_denoise_web.py").exists())

    def test_script_entry_points_support_help_when_called_by_path(self):
        for script in ("scripts/oldtonefix.py", "scripts/oldtonefix_web.py"):
            with self.subTest(script=script):
                result = __import__("subprocess").run(
                    [sys.executable, script, "--help"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)

    def test_model_and_web_resources_resolve_from_repository_root(self):
        self.assertEqual(web.REPO_ROOT, ROOT)
        self.assertEqual(web.DENOISE_SCRIPT, ROOT / "scripts" / "oldtonefix.py")
        self.assertEqual(web.WEB_ROOT, ROOT / "web")
        self.assertEqual(web.DEFAULT_RNN_MODEL, ROOT / "models" / "cb.rnnn")

    def test_cross_platform_launchers_match_reference_conventions(self):
        windows = (ROOT / "start_web.bat").read_text(encoding="utf-8")
        macos = (ROOT / "start_web.command").read_text(encoding="utf-8")

        self.assertIn('cd /d "%~dp0"', windows)
        self.assertIn("where python", windows)
        self.assertIn("where py", windows)
        self.assertIn("scripts\\oldtonefix_web.py", windows)
        self.assertIn('cd "$(dirname "$0")"', macos)
        self.assertIn("command -v python3", macos)
        self.assertIn("command -v python", macos)
        self.assertIn("scripts/oldtonefix_web.py", macos)
        for launcher in (windows, macos):
            self.assertIn("PYTHONUTF8", launcher)
            self.assertIn("PYTHONIOENCODING", launcher)


if __name__ == "__main__":
    unittest.main()
