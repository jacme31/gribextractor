from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


class FakeFlask:
    def __init__(self, name):
        pass

    def route(self, *args, **kwargs):
        return lambda function: function


flask = types.ModuleType("flask")
flask.Flask = FakeFlask
flask.jsonify = lambda value: value
flask.render_template = lambda *args, **kwargs: None
herbie = types.ModuleType("herbie")
herbie.HerbieWait = mock.Mock()
pygrib = types.ModuleType("pygrib")

with mock.patch.dict(sys.modules, {"flask": flask, "herbie": herbie, "pygrib": pygrib}):
    spec = importlib.util.spec_from_file_location(
        "gribextractor_tested", Path(__file__).with_name("gribextractor.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


class GribExtractorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.original = module.GRIBD
        self.addCleanup(setattr, module, "GRIBD", self.original)
        module.GRIBD = module.gribd(step=3)
        module.GRIBD.pathconfig = self.tempdir.name
        module.DELAI_NOUVEL_ESSAI = 0
        self.addCleanup(setattr, module, "DELAI_NOUVEL_ESSAI", 60)
        self.run = datetime(2024, 1, 1)

    def write_legacy(self, tm):
        path = Path(self.tempdir.name) / "config.txt"
        path.write_text(
            "[CURRENT]\ndaterun = 2024/01/01\nnumrun = 0\nTM = %s\n" % tm,
            encoding="utf-8")

    def run_one_checkpoint(self):
        original_write = module.config_write

        def write_and_stop(*args):
            original_write(*args)
            module.GRIBD.stop_event.set()

        with mock.patch.object(module, "config_write", side_effect=write_and_stop):
            module.boucle_grib()

    def test_status_and_existing_failure_are_localized(self):
        module.GRIBD.dernier_echec = "Daterun=2026/11/15 numéro de run=12Z TM=+9"
        module.config_write(self.run, 0, 0)
        module.GRIBD.dernier_echec = ""
        module.config_read()
        with mock.patch.object(module, "UI_LANGUAGE", "en"):
            status = module.statut()
            self.assertEqual(status["dernier_echec"], "Daterun=2026/11/15 run=12Z TM=+9")
            self.assertEqual(status["dernier_fichier"], "No files downloaded")
            self.assertEqual(status["status"], "GRIB downloads stopped")
            self.assertEqual(module.reinitialiser_echec()["dernier_echec"],
                             "No abandoned forecast hours")
        with mock.patch.object(module, "UI_LANGUAGE", "fr"):
            self.assertEqual(module.statut()["dernier_echec"], "Aucune échéance abandonnée")

    def test_reset_failure_error_is_localized_and_logs_in_english(self):
        with mock.patch.object(module, "UI_LANGUAGE", "en"), \
                self.assertLogs(module.logger, level="ERROR") as logs:
            response, status = module.reinitialiser_echec()
        self.assertEqual(status, 500)
        self.assertIn("Could not save", response["erreur"])
        self.assertIn("Could not clear the last abandoned forecast hour", logs.output[0])

    def test_language_environment_and_template_context(self):
        with mock.patch.dict(os.environ, {"UI_LANGUAGE": "en"}), \
                mock.patch.object(module, "UI_LANGUAGE", "en"), \
                mock.patch.object(module, "render_template") as render:
            module.index()
        self.assertEqual(render.call_args.kwargs["language"], "en")
        self.assertEqual(render.call_args.kwargs["text"]["start"], "Start downloads")

    def test_both_languages_render_supervision_page(self):
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                          autoescape=select_autoescape(["html"]))
        template = env.get_template("index.html")
        for language, title, button in (
                ("en", "NOAA GFS monitor", "Start downloads"),
                ("fr", "Surveillance du programme GFS NOAA", "Démarrer la boucle")):
            with self.subTest(language=language):
                rendered = template.render(text=module.TEXT[language], language=language)
                self.assertIn(f'<html lang="{language}">', rendered)
                self.assertIn(f"<h1>{title}</h1>", rendered)
                self.assertIn(f">{button}</button>", rendered)
                self.assertIn('var text = {', rendered)

    def test_invalid_language_fails_at_startup(self):
        spec = importlib.util.spec_from_file_location(
            "gribextractor_language_tested", Path(__file__).with_name("gribextractor.py"))
        loaded = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"UI_LANGUAGE": "de"}), \
                mock.patch.dict(sys.modules, {"flask": flask, "herbie": herbie, "pygrib": pygrib}):
            with self.assertRaisesRegex(ValueError, "UI_LANGUAGE"):
                spec.loader.exec_module(loaded)

    def test_minimum_messages_environment(self):
        for value, expected in ((None, 1), ("3", 3), ("0", None),
                                ("-1", None), ("", None), ("abc", None)):
            with self.subTest(value=value):
                spec = importlib.util.spec_from_file_location(
                    "gribextractor_min_messages_tested", Path(__file__).with_name("gribextractor.py"))
                loaded = importlib.util.module_from_spec(spec)
                environment = {"UI_LANGUAGE": "fr", "GRIB_MIN_MESSAGES": value}
                if value is None:
                    environment.pop("GRIB_MIN_MESSAGES")
                with mock.patch.dict(os.environ, environment, clear=True), \
                        mock.patch.dict(sys.modules, {"flask": flask, "herbie": herbie, "pygrib": pygrib}):
                    if expected is None:
                        with self.assertRaisesRegex(ValueError, "GRIB_MIN_MESSAGES"):
                            spec.loader.exec_module(loaded)
                    else:
                        spec.loader.exec_module(loaded)
                        self.assertEqual(loaded.GRIB_MIN_MESSAGES, expected)

    def test_grib_file_message_count(self):
        path = Path(self.tempdir.name) / "download.grib"
        with mock.patch.object(module.pygrib, "open", create=True) as open_grib:
            self.assertFalse(module.chkgribfile(path))
            path.touch()
            self.assertFalse(module.chkgribfile(path))
            open_grib.assert_not_called()
            path.write_bytes(b"GRIB")

            for minimum, count, valid in ((1, 0, False), (1, 1, True),
                                           (3, 2, False), (3, 3, True),
                                           (3, 4, True)):
                with self.subTest(minimum=minimum, count=count):
                    gribs = mock.Mock(messages=count)
                    open_grib.return_value = gribs
                    with mock.patch.object(module, "GRIB_MIN_MESSAGES", minimum):
                        self.assertEqual(module.chkgribfile(path), valid)
                    gribs.close.assert_called_once()

            open_grib.side_effect = OSError("unreadable GRIB")
            self.assertFalse(module.chkgribfile(path))

    def test_statistics_count_success_by_attempt_and_abandons(self):
        self.write_legacy(0)
        download = mock.Mock()
        download.download.return_value = "valid.grib"
        with mock.patch.object(module, "chkgribfile", return_value=True):
            for failures in range(4):
                module.GRIBD.stop_event.clear()
                attempts = [OSError("failure")] * failures + [TimeoutError("not ready"), download]
                with mock.patch.object(module, "HerbieWait", side_effect=attempts):
                    self.run_one_checkpoint()

            module.GRIBD.stop_event.clear()
            with mock.patch.object(module, "HerbieWait", side_effect=OSError("failure")):
                self.run_one_checkpoint()

        stats = module.statut()["statistiques"]
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["traites"], 5)
        self.assertEqual([entry["nombre"] for entry in stats["tentatives"]], [1, 1, 1, 1])
        self.assertEqual([entry["pourcentage"] for entry in stats["tentatives"]], [20.0] * 4)
        self.assertEqual(stats["abandonnes"], {"nombre": 1, "pourcentage": 20.0})

    def test_statistics_reset_on_new_instance_and_have_zero_percent_when_empty(self):
        stats = module.statut()["statistiques"]
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["traites"], 0)
        self.assertEqual(stats["abandonnes"], {"nombre": 0, "pourcentage": 0.0})
        self.assertEqual([entry["pourcentage"] for entry in stats["tentatives"]], [0.0] * 4)
        self.assertTrue(datetime.fromisoformat(stats["depuis"]).utcoffset().total_seconds() == 0)
        module.GRIBD.compteurs_tentatives[0] = 4
        module.GRIBD = module.gribd(step=3)
        self.assertEqual(module.statut()["statistiques"]["total"], 0)

    def test_statistics_survive_worker_stop_and_restart(self):
        module.GRIBD.compteurs_tentatives[:] = [3, 1, 0, 0]
        with mock.patch.object(module, "boucle_grib",
                               side_effect=lambda: module.GRIBD.stop_event.wait(5)):
            for _ in range(2):
                self.assertTrue(module.demarrer_boucle())
                module.arreter()
                module.GRIBD.thread_boucle.join(5)
                self.assertFalse(module.GRIBD.thread_boucle.is_alive())
        stats = module.statut()["statistiques"]
        self.assertEqual(stats["total"], 4)
        self.assertEqual([entry["pourcentage"] for entry in stats["tentatives"]],
                         [75.0, 25.0, 0.0, 0.0])

    def test_abandoned_count_only_after_checkpoint_and_not_reset_by_clear_button(self):
        self.write_legacy(0)
        with mock.patch.object(module, "HerbieWait", side_effect=OSError("failure")), \
                mock.patch.object(module, "config_write", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                module.boucle_grib()
        self.assertEqual(module.statut()["statistiques"]["abandonnes"]["nombre"], 0)
        with mock.patch.object(module, "HerbieWait", side_effect=OSError("failure")):
            self.run_one_checkpoint()
        self.assertEqual(module.statut()["statistiques"]["abandonnes"],
                         {"nombre": 1, "pourcentage": 100.0})
        module.reinitialiser_echec()
        self.assertEqual(module.statut()["statistiques"]["abandonnes"]["nombre"], 1)

    def test_priority_parsing_preserves_order_and_rejects_empty_entries(self):
        self.assertEqual(module.parse_priority(" google, AWS , azure "),
                         ["google", "aws", "azure"])
        for value in ("", " ", "aws,", ",aws", "aws,,azure", "aws,gooogle"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.parse_priority(value)

    def test_priority_environment_is_read_at_startup(self):
        def load(env):
            spec = importlib.util.spec_from_file_location(
                "gribextractor_priority_tested", Path(__file__).with_name("gribextractor.py"))
            loaded = importlib.util.module_from_spec(spec)
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.dict(sys.modules, {"flask": flask, "herbie": herbie, "pygrib": pygrib}):
                spec.loader.exec_module(loaded)
            return loaded.PRIORITY

        self.assertEqual(load({}), ["aws", "google", "azure"])
        self.assertEqual(load({"PRIORITY": "nomads, aws"}), ["nomads", "aws"])
        with self.assertRaises(ValueError):
            load({"PRIORITY": ""})

    def test_configured_priority_is_passed_to_herbie(self):
        self.write_legacy(0)
        download = mock.Mock()
        download.download.return_value = "valid.grib"
        with mock.patch.object(module, "PRIORITY", ["google", "aws"]), \
                mock.patch.object(module, "HerbieWait", return_value=download) as wait, \
                mock.patch.object(module, "chkgribfile", return_value=True):
            self.run_one_checkpoint()
        self.assertEqual(wait.call_args.kwargs["priority"], ["google", "aws"])

    def test_signal_diagnostic_registers_all_threads(self):
        with mock.patch.object(module.signal, "SIGUSR1", 10, create=True), \
                mock.patch.object(module.faulthandler, "register", create=True) as register:
            module.activer_diagnostic_signal()
        register.assert_called_once_with(10, all_threads=True)

    @unittest.skipUnless(hasattr(signal, "SIGUSR1"), "SIGUSR1 requires Unix")
    def test_signal_dumps_threads_without_stopping_process(self):
        code = (
            "import threading, sys\n"
            "from test_gribextractor import module\n"
            "module.activer_diagnostic_signal()\n"
            "threading.Thread(target=threading.Event().wait, daemon=True).start()\n"
            "print('ready', flush=True)\n"
            "sys.stdin.readline()\n"
            "print('alive', flush=True)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-B", "-c", code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            os.kill(proc.pid, signal.SIGUSR1)
            stdout, stderr = proc.communicate(input="stop\n", timeout=10)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("alive", stdout)
            self.assertIn("Thread", stderr)
            self.assertIn("threading.py", stderr)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_three_different_errors_skip_and_persist_next_hour(self):
        self.write_legacy(0)
        bad_download = mock.Mock()
        bad_download.download.side_effect = OSError("download failed")
        invalid_download = mock.Mock()
        invalid_download.download.return_value = None
        with mock.patch.object(module, "HerbieWait", side_effect=[
                TimeoutError("not ready"), OSError("availability failed"),
                bad_download, invalid_download]) as wait:
            self.run_one_checkpoint()
        self.assertEqual(wait.call_count, 4)
        self.assertEqual(module.config_read(), (self.run, 0, 1))
        self.assertIn("TM=+0", module.GRIBD.dernier_echec)
        module.GRIBD.dernier_echec = ""
        module.config_read()
        self.assertIn("TM=+0", module.GRIBD.dernier_echec)

    def test_availability_timeouts_do_not_consume_failure_budget(self):
        self.write_legacy(0)
        download = mock.Mock()
        download.download.return_value = "valid.grib"
        with mock.patch.object(module, "HerbieWait", side_effect=[
                TimeoutError("not ready")] * 4 + [download]) as wait, \
                mock.patch.object(module, "chkgribfile", return_value=True):
            self.run_one_checkpoint()
        self.assertEqual(wait.call_count, 5)
        self.assertTrue(all(call.kwargs["fxx"] == 0 for call in wait.call_args_list))
        download.download.assert_called_once()
        self.assertEqual(module.config_read(), (self.run, 0, 1))
        self.assertEqual(module.GRIBD.dernier_echec, "Aucune échéance abandonnée")

    def test_download_timeout_counts_as_failure(self):
        self.write_legacy(0)
        download = mock.Mock()
        download.download.side_effect = TimeoutError("download timed out")
        with mock.patch.object(module, "HerbieWait", return_value=download):
            self.run_one_checkpoint()
        self.assertEqual(download.download.call_count, 3)
        self.assertEqual(module.config_read(), (self.run, 0, 1))
        self.assertIn("TM=+0", module.GRIBD.dernier_echec)

    def test_availability_timeouts_preserve_previous_failure_count(self):
        self.write_legacy(0)
        download = mock.Mock()
        download.download.side_effect = OSError("download failed")
        with mock.patch.object(module, "HerbieWait", side_effect=[
                download, TimeoutError("not ready"), TimeoutError("not ready"),
                download, download]) as wait:
            self.run_one_checkpoint()
        self.assertEqual(wait.call_count, 5)
        self.assertEqual(download.download.call_count, 3)
        self.assertEqual(module.config_read(), (self.run, 0, 1))
        self.assertIn("TM=+0", module.GRIBD.dernier_echec)

    def test_missing_index_waits_without_consuming_failure_budget(self):
        self.write_legacy(0)
        missing = [mock.Mock(idx=None, grib_source="nomads") for _ in range(4)]
        ready = mock.Mock(idx="https://example.test/grib.idx", grib_source="aws")
        ready.download.return_value = "valid.grib"
        with mock.patch.object(module, "HerbieWait", side_effect=missing + [ready]) as wait, \
                mock.patch.object(module, "chkgribfile", return_value=True), \
                mock.patch.object(module, "DELAI_NOUVEL_ESSAI", 60), \
                mock.patch.object(module.GRIBD.stop_event, "wait", return_value=False) as pause:
            self.run_one_checkpoint()
        self.assertEqual(wait.call_count, 5)
        self.assertTrue(all(call.kwargs["fxx"] == 0 for call in wait.call_args_list))
        self.assertEqual(pause.call_count, 4)
        pause.assert_has_calls([mock.call(60)] * 4)
        for item in missing:
            item.download.assert_not_called()
        ready.download.assert_called_once()
        self.assertEqual(module.config_read(), (self.run, 0, 1))
        self.assertEqual(module.GRIBD.dernier_echec, "Aucune échéance abandonnée")

    def test_missing_index_preserves_other_failures(self):
        self.write_legacy(0)
        failed = mock.Mock(idx="index", grib_source="nomads")
        failed.download.side_effect = OSError("download failed")
        missing = mock.Mock(idx=None, grib_source="nomads")
        with mock.patch.object(module, "HerbieWait", side_effect=[
                failed, missing, missing, failed, failed]) as wait:
            self.run_one_checkpoint()
        self.assertEqual(wait.call_count, 5)
        self.assertEqual(failed.download.call_count, 3)
        missing.download.assert_not_called()
        self.assertEqual(module.config_read(), (self.run, 0, 1))
        self.assertIn("TM=+0", module.GRIBD.dernier_echec)

    def test_stop_interrupts_missing_index_wait(self):
        self.write_legacy(0)
        missing = mock.Mock(idx=None, grib_source="nomads")
        waiting = threading.Event()
        original_wait = module.GRIBD.stop_event.wait

        def wait_for_stop(delay):
            waiting.set()
            return original_wait(delay)

        with mock.patch.object(module, "DELAI_NOUVEL_ESSAI", 60), \
                mock.patch.object(module, "HerbieWait", return_value=missing) as wait, \
                mock.patch.object(module.GRIBD.stop_event, "wait", side_effect=wait_for_stop):
            self.assertTrue(module.demarrer_boucle())
            try:
                self.assertTrue(waiting.wait(5))
            finally:
                module.arreter()
                module.GRIBD.thread_boucle.join(5)
            self.assertFalse(module.GRIBD.thread_boucle.is_alive())
            wait.assert_called_once()
        missing.download.assert_not_called()
        self.assertEqual(module.config_read(), (self.run, 0, 0))
        self.assertEqual(module.GRIBD.dernier_echec, "Aucune échéance abandonnée")

    def test_local_grib_without_index_can_generate_one(self):
        self.write_legacy(0)
        local = mock.Mock(idx=None, grib_source="local")
        local.download.return_value = "valid.grib"
        with mock.patch.object(module, "HerbieWait", return_value=local), \
                mock.patch.object(module, "chkgribfile", return_value=True):
            self.run_one_checkpoint()
        local.download.assert_called_once()
        self.assertEqual(module.config_read(), (self.run, 0, 1))

    def test_stop_after_availability_timeout_does_not_skip(self):
        self.write_legacy(0)

        def stop_and_timeout(*args, **kwargs):
            module.GRIBD.stop_event.set()
            raise TimeoutError("not ready")

        with mock.patch.object(module, "HerbieWait", side_effect=stop_and_timeout) as wait:
            module.boucle_grib()
        wait.assert_called_once()
        self.assertEqual(module.config_read(), (self.run, 0, 0))
        self.assertEqual(module.GRIBD.dernier_echec, "Aucune échéance abandonnée")

    def test_success_checkpoint_resumes_next_hour(self):
        self.write_legacy(0)
        download = mock.Mock()
        download.download.return_value = "valid.grib"
        with mock.patch.object(module, "HerbieWait", return_value=download), \
                mock.patch.object(module, "chkgribfile", return_value=True):
            self.run_one_checkpoint()
        self.assertEqual(module.config_read(), (self.run, 0, 1))
        self.assertIn("TM=+0", module.GRIBD.dernier_fichier)

        module.GRIBD.stop_event.clear()
        with mock.patch.object(module, "HerbieWait", return_value=download) as wait, \
                mock.patch.object(module, "chkgribfile", return_value=True):
            self.run_one_checkpoint()
        self.assertEqual(wait.call_args.kwargs["fxx"], 3)
        self.assertEqual(module.config_read(), (self.run, 0, 2))

    def test_invalid_files_are_removed_on_each_of_three_attempts(self):
        self.write_legacy(0)
        path = Path(self.tempdir.name) / "invalid.grib"
        download = mock.Mock()

        def make_invalid(*args, **kwargs):
            path.write_bytes(b"invalid")
            return path

        download.download.side_effect = make_invalid
        with mock.patch.object(module, "HerbieWait", return_value=download), \
                mock.patch.object(module, "chkgribfile", return_value=False):
            self.run_one_checkpoint()
        self.assertEqual(download.download.call_count, 3)
        self.assertFalse(path.exists())
        self.assertEqual(module.config_read(), (self.run, 0, 1))

    def test_last_hour_advances_to_next_day_and_run(self):
        self.assertEqual(module.prochaine_echeance(
            datetime(2024, 1, 1, 18), module.GRIBD.lfxx - 1),
            (datetime(2024, 1, 2), 0))

    def test_legacy_checkpoint_is_replayed_and_step_change_aligns_forward(self):
        self.write_legacy(5)
        self.assertEqual(module.config_read(), (self.run, 0, 1))
        module.config_write(self.run, 0, 1)
        module.GRIBD = module.gribd(step=2)
        module.GRIBD.pathconfig = self.tempdir.name
        self.assertEqual(module.config_read(), (self.run, 0, 2))

    def test_failed_atomic_replace_preserves_previous_checkpoint(self):
        module.config_write(self.run, 0, 0)
        with mock.patch.object(module.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                module.config_write(self.run, 0, 1)
        self.assertEqual(module.config_read(), (self.run, 0, 0))
        self.assertEqual(os.listdir(self.tempdir.name), ["config.txt"])

    def test_reset_failure_is_persisted_without_changing_checkpoint(self):
        module.GRIBD.dernier_echec = "Daterun=2024/01/01 numéro de run=0Z TM=+0"
        module.config_write(self.run, 0, 2)
        result = module.reinitialiser_echec()
        self.assertEqual(result["dernier_echec"], "Aucune échéance abandonnée")
        module.GRIBD.dernier_echec = "incorrect"
        self.assertEqual(module.config_read(), (self.run, 0, 2))
        self.assertEqual(module.GRIBD.dernier_echec, "Aucune échéance abandonnée")
        module.config_write(self.run, 0, 3)
        self.assertEqual(module.config_read(), (self.run, 0, 3))
        self.assertEqual(module.GRIBD.dernier_echec, "Aucune échéance abandonnée")

    def test_reset_failure_reports_write_error_without_losing_state(self):
        module.GRIBD.dernier_echec = "TM=+0"
        module.config_write(self.run, 0, 2)
        with mock.patch.object(module.os, "replace", side_effect=OSError("disk")):
            response, status = module.reinitialiser_echec()
        self.assertEqual(status, 500)
        self.assertIn("erreur", response)
        self.assertEqual(module.GRIBD.dernier_echec, "TM=+0")
        self.assertEqual(module.config_read(), (self.run, 0, 2))
        self.assertEqual(module.GRIBD.dernier_echec, "TM=+0")

    def test_reset_failure_reports_missing_configuration(self):
        response, status = module.reinitialiser_echec()
        self.assertEqual(status, 500)
        self.assertIn("erreur", response)
        self.assertFalse(Path(self.tempdir.name, "config.txt").exists())

    def test_reset_and_progress_write_do_not_overwrite_each_other(self):
        module.GRIBD.dernier_echec = "TM=+0"
        module.config_write(self.run, 0, 1)
        saving = threading.Event()
        release = threading.Event()
        original_save = module.config_save

        def delayed_save(config):
            if not saving.is_set():
                saving.set()
                if not release.wait(5):
                    raise TimeoutError("test timed out")
            original_save(config)

        with mock.patch.object(module, "config_save", side_effect=delayed_save), \
                ThreadPoolExecutor(max_workers=2) as pool:
            reset = pool.submit(module.reinitialiser_echec)
            try:
                self.assertTrue(saving.wait(5))
                update = pool.submit(module.config_write, self.run, 0, 2)
            finally:
                release.set()
            reset.result(timeout=5)
            update.result(timeout=5)
        self.assertEqual(module.config_read(), (self.run, 0, 2))
        self.assertEqual(module.GRIBD.dernier_echec, "Aucune échéance abandonnée")

    def test_stop_during_check_returns_without_skipping(self):
        self.write_legacy(0)
        entered = threading.Event()
        release = threading.Event()

        def waiting(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test timed out")
            return mock.Mock()

        with mock.patch.object(module, "HerbieWait", side_effect=waiting):
            self.assertTrue(module.demarrer_boucle())
            try:
                self.assertTrue(entered.wait(5))
                module.arreter()
                status = module.statut()
                self.assertTrue(status["en_arret"])
                self.assertFalse(status["boucle_active"])
                self.assertFalse(module.demarrer_boucle())
            finally:
                release.set()
                module.GRIBD.thread_boucle.join(5)
            self.assertFalse(module.GRIBD.thread_boucle.is_alive())
        self.assertEqual(module.config_read(), (self.run, 0, 0))

    def test_stop_during_successful_download_saves_completion(self):
        self.write_legacy(0)
        entered = threading.Event()
        release = threading.Event()
        download = mock.Mock()

        def slow_download(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test timed out")
            return "valid.grib"

        download.download.side_effect = slow_download
        with mock.patch.object(module, "HerbieWait", return_value=download), \
                mock.patch.object(module, "chkgribfile", return_value=True):
            self.assertTrue(module.demarrer_boucle())
            try:
                self.assertTrue(entered.wait(5))
                module.arreter()
            finally:
                release.set()
                module.GRIBD.thread_boucle.join(5)
        self.assertEqual(module.config_read(), (self.run, 0, 1))

    def test_stopping_run_wait_does_not_consume_attempt(self):
        future = datetime(2099, 1, 1)
        module.config_write(future, 0, 0)
        with mock.patch.object(module, "HerbieWait") as wait:
            self.assertTrue(module.demarrer_boucle())
            module.arreter()
            module.GRIBD.thread_boucle.join(5)
            self.assertFalse(module.GRIBD.thread_boucle.is_alive())
            wait.assert_not_called()
        self.assertEqual(module.config_read(), (future, 0, 0))

    def test_supervisor_restarts_and_stop_interrupts_backoff(self):
        attempts = []

        def fail_then_stop():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("temporary error")
            module.GRIBD.stop_event.set()

        with mock.patch.object(module, "boucle_grib", side_effect=fail_then_stop), \
                mock.patch.object(module, "DELAI_SUPERVISION", 0):
            module.superviser_boucle()
        self.assertEqual(len(attempts), 2)

        module.GRIBD.stop_event.clear()
        waiting = threading.Event()

        def fail():
            waiting.set()
            raise OSError("persistent error")

        with mock.patch.object(module, "boucle_grib", side_effect=fail), \
                mock.patch.object(module, "DELAI_SUPERVISION", 60):
            self.assertTrue(module.demarrer_boucle())
            self.assertTrue(waiting.wait(5))
            module.arreter()
            module.GRIBD.thread_boucle.join(5)
            self.assertFalse(module.GRIBD.thread_boucle.is_alive())


if __name__ == "__main__":
    unittest.main()
