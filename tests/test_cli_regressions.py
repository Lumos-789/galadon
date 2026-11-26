"""One regression per defect found in the 0.3.7 review, through the CLI."""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import commit_all, git, head, run  # noqa: E402

KEPT = {
    "README.md": "# MY README\n\nreal content\n",
    "AGENTS.md": "# MY AGENT RULES\n\n- never touch prod\n",
    ".gitignore": "secrets/\n",
    "spec/spec.md": (
        "# my real spec\n\n## Problem\n\nthe real problem\n\n"
        "## Acceptance\n\n- [x] done\n\n## Non-goals\n\nnone\n"
    ),
    "galadon.yaml": "project: mine\ntemplate: api\nrole: six-ring\n",
}


class InitIntoAnExistingProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-init-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "existing"
        for relative, text in KEPT.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def assert_kept(self) -> None:
        for relative, text in KEPT.items():
            now = (self.project / relative).read_text(encoding="utf-8")
            if relative == "AGENTS.md":
                # The loop reports the phase in a managed block it appends;
                # the rules that were there are untouched, byte for byte.
                self.assertTrue(now.startswith(text), relative)
                tail = now[len(text):]
                self.assertTrue(
                    tail == "" or re.fullmatch(r"\n*<!-- galadon:begin -->.*<!-- galadon:end -->\n", tail, re.DOTALL),
                    f"only the managed block may follow the rules, got {tail!r}",
                )
            else:
                self.assertEqual(now, text, relative)

    def test_without_force_nothing_is_added(self) -> None:
        code, _out, err = run("init", "--path", str(self.project))

        self.assertEqual(code, 2)
        self.assertIn("not empty", err)
        self.assertIn("the files already there are kept", err)
        self.assertFalse((self.project / "verify").exists())
        self.assert_kept()

    def test_force_adds_the_missing_files_and_keeps_the_rest(self) -> None:
        # --force used to overwrite every one of these with a template.
        code, out, err = run("init", "--path", str(self.project), "--force")

        self.assertEqual(code, 0, out + err)
        self.assert_kept()
        self.assertTrue((self.project / "verify" / "README.md").is_file())
        self.assertIn("kept 5 existing file(s), none overwritten:", out)
        self.assertIn("  spec/spec.md", out)
        # The existing configuration is the one that is honoured.
        self.assertIn("template: api", out)
        self.assertIn("role:     six-ring", out)

    def test_force_reports_what_it_kept_as_json(self) -> None:
        code, out, err = run("init", "--path", str(self.project), "--force", "--json")

        self.assertEqual(code, 0, out + err)
        result = json.loads(out)
        self.assertEqual(sorted(result["skipped"]), sorted(KEPT))
        self.assertNotIn("galadon.yaml", result["written"])
        self.assertIn("verify/README.md", result["written"])
        self.assert_kept()

    def test_a_layout_the_config_does_not_name_is_refused(self) -> None:
        code, _out, err = run("init", "--path", str(self.project), "--force", "--layout", "webapp")

        self.assertEqual(code, 2)
        self.assertIn("already has a galadon.yaml with layout none / none", err)
        self.assertFalse((self.project / "openspec").exists(), "nothing was added")

    def test_the_same_layout_may_be_re_run_to_restore_missing_files(self) -> None:
        (self.project / "galadon.yaml").write_text(
            "project: mine\ntemplate: api\nrole: solo\nlayout: webapp\nstack: python\n"
            'checks:\n  build: "true"\n  test: "true"\n',
            encoding="utf-8",
        )
        code, out, err = run("init", "--path", str(self.project), "--force", "--layout", "webapp", "--stack", "python")

        self.assertEqual(code, 0, out + err)
        self.assertTrue((self.project / "server" / "app.py").is_file())
        self.assertEqual((self.project / "README.md").read_text(encoding="utf-8"), KEPT["README.md"])


class FreshCloneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-clone-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def clone(self, layout: str) -> Path:
        source = self.tmp / f"src-{layout}"
        code, out, err = run("init", "--path", str(source), "--layout", layout, "--stack", "python", "--name", "demo")
        self.assertEqual(code, 0, out + err)
        git(source, "init")
        commit_all(source, "init")
        clone = self.tmp / f"clone-{layout}"
        git(self.tmp, "clone", "-q", str(source), str(clone))
        return clone

    def test_a_fresh_clone_is_green_for_every_layout(self) -> None:
        # `.agents/` is gitignored, so every clone starts without the slots the
        # layout contract asks for; `check` used to fail on all of them.
        for layout in ("webapp", "service", "plugin"):
            with self.subTest(layout=layout):
                clone = self.clone(layout)
                self.assertFalse((clone / ".agents").exists(), "the slots never reach a clone")

                code, out, err = run("check", "--path", str(clone), "--json")

                self.assertEqual(code, 0, out + err)
                self.assertTrue(json.loads(out)["ok"])
                for slot in ("plans", "evidence", "archive", "skills", "tmp", "wiki"):
                    self.assertTrue((clone / ".agents" / slot).is_dir(), slot)
                self.assertIn(
                    "recreated 6 missing local slot(s) under .agents/ "
                    "(plans, evidence, archive, skills, tmp, wiki)",
                    err,
                )

    def test_restoring_the_slots_adds_nothing_to_the_repository(self) -> None:
        clone = self.clone("webapp")
        run("next", "--path", str(clone))
        status = git(clone, "status", "--porcelain", "--untracked-files=all").stdout
        self.assertNotIn(".agents", status)


class CheckOutputTests(unittest.TestCase):
    def test_a_failure_that_only_went_to_stderr_is_shown(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="galadon-check-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        project = tmp / "demo"
        code, out, err = run("init", "--path", str(project), "--layout", "webapp", "--stack", "python")
        self.assertEqual(code, 0, out + err)
        (project / "tests" / "test_red.py").write_text(
            "import unittest\n\n\nclass Red(unittest.TestCase):\n"
            "    def test_red(self):\n        self.fail('this exact reason')\n",
            encoding="utf-8",
        )

        code, out, _err = run("check", "--path", str(project))

        self.assertEqual(code, 1)
        self.assertIn("[1] test:", out)
        self.assertIn("this exact reason", out)
        self.assertIn("AssertionError", out)


class BadValuesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-bad-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "demo"
        code, out, err = run("init", "--path", str(self.project), "--layout", "webapp", "--stack", "python")
        self.assertEqual(code, 0, out + err)

    def edit(self, old: str, new: str) -> None:
        path = self.project / "galadon.yaml"
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def test_a_bad_timeout_is_reported_not_crashed(self) -> None:
        self.edit("timeout: 300", "timeout: abc")
        code, _out, err = run("next", "--path", str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("checks.timeout must be a positive whole number", err)
        self.assertNotIn("Traceback", err)

    def test_a_negative_timeout_is_refused(self) -> None:
        self.edit("timeout: 300", "timeout: -5")
        code, _out, err = run("check", "--path", str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("checks.timeout", err)

    def test_a_truncated_check_record_names_the_way_out(self) -> None:
        (self.project / ".galadon" / "checks.json").write_text("{ truncated", encoding="utf-8")
        code, _out, err = run("next", "--path", str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("checks.json is not valid JSON", err)
        self.assertIn("delete it and run the command again", err)
        self.assertNotIn("Traceback", err)

    def test_a_project_key_left_empty_is_required(self) -> None:
        self.edit("project: demo", "project:")
        code, _out, err = run("next", "--path", str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("project is required", err)

    def test_a_layout_key_left_empty_is_not_none(self) -> None:
        self.edit("layout: webapp", "layout:")
        code, _out, err = run("next", "--path", str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("layout must be none, webapp, service, plugin", err)


class VerificationBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-bind-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "demo"
        code, out, err = run("init", "--path", str(self.project), "--layout", "webapp", "--stack", "python")
        self.assertEqual(code, 0, out + err)
        git(self.project, "init")
        commit_all(self.project, "init")
        self.app = self.project / "src" / "demo" / "app.py"
        self.original = self.app.read_text(encoding="utf-8")

    def phase_and_next(self) -> tuple[str, str]:
        code, out, err = run("next", "--path", str(self.project), "--json")
        assert code == 0, err
        payload = json.loads(out)
        return payload["phase"], payload["next"]

    def test_a_green_check_goes_stale_when_the_code_changes(self) -> None:
        self.assertEqual(run("check", "--path", str(self.project))[0], 0)
        self.assertEqual(self.phase_and_next()[0], "ship")

        self.app.write_text(self.original + "# a change nobody checked\n", encoding="utf-8")

        phase, action = self.phase_and_next()
        self.assertEqual(phase, "verify")
        self.assertIn("changed after the last green check", action)

    def test_a_check_with_uncommitted_work_stays_current_while_it_is_unchanged(self) -> None:
        self.app.write_text(self.original + "# a change\n", encoding="utf-8")
        self.assertEqual(run("check", "--path", str(self.project))[0], 0)
        self.assertEqual(self.phase_and_next()[0], "ship")

    def test_committing_exactly_what_was_checked_does_not_make_it_stale(self) -> None:
        # "check, then commit" is the normal order; it must not send the agent
        # back to verify the same tree again.
        self.app.write_text(self.original + "# a change\n", encoding="utf-8")
        self.assertEqual(run("check", "--path", str(self.project))[0], 0)
        commit_all(self.project, "the checked change")
        self.assertEqual(self.phase_and_next()[0], "ship")

    def test_a_commit_of_something_else_does_make_it_stale(self) -> None:
        self.assertEqual(run("check", "--path", str(self.project))[0], 0)
        (self.project / "server" / "app.py").write_text("raise SystemExit('untested')\n", encoding="utf-8")
        commit_all(self.project, "untested change")
        self.assertEqual(self.phase_and_next()[0], "verify")

    def test_editing_an_uncommitted_file_again_is_detected(self) -> None:
        self.app.write_text(self.original + "# first edit\n", encoding="utf-8")
        self.assertEqual(run("check", "--path", str(self.project))[0], 0)
        self.app.write_text(self.original + "# second edit\n", encoding="utf-8")
        self.assertEqual(self.phase_and_next()[0], "verify")

    def test_deleting_a_checked_file_is_detected(self) -> None:
        extra = self.project / "src" / "demo" / "extra.py"
        extra.write_text("X = 1\n", encoding="utf-8")
        self.assertEqual(run("check", "--path", str(self.project))[0], 0)
        extra.unlink()
        self.assertEqual(self.phase_and_next()[0], "verify")

    def test_the_evidence_records_which_commit_was_checked(self) -> None:
        commit = head(self.project)
        run("check", "--path", str(self.project))
        self.assertIn(f"- commit: {commit}", (self.project / "verify" / "evidence.md").read_text(encoding="utf-8"))

    def test_without_git_there_is_nothing_to_bind_to(self) -> None:
        shutil.rmtree(self.project / ".git")
        self.assertEqual(run("check", "--path", str(self.project))[0], 0)
        self.app.write_text("BROKEN = (((\n", encoding="utf-8")
        self.assertEqual(self.phase_and_next()[0], "ship", "ship re-runs the checks either way")
        evidence = (self.project / "verify" / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("- commit: (project is not tracked by git)", evidence)


class QuietCommandsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-quiet-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "demo"
        code, out, err = run("init", "--path", str(self.project), "--layout", "webapp", "--stack", "python")
        self.assertEqual(code, 0, out + err)

    def test_next_does_not_rewrite_files_when_nothing_changed(self) -> None:
        agents = self.project / "AGENTS.md"
        run("next", "--path", str(self.project))
        before = agents.stat().st_mtime_ns
        time.sleep(0.05)

        run("next", "--path", str(self.project))
        run("next", "--path", str(self.project))

        self.assertEqual(agents.stat().st_mtime_ns, before)

    def test_the_managed_block_does_follow_the_phase(self) -> None:
        run("check", "--path", str(self.project), "--json")
        code, out, err = run("next", "--path", str(self.project))
        self.assertEqual(code, 0, err)
        self.assertIn("phase: ship", (self.project / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIn("demo: ship / agent", out)

    def test_no_state_file_is_written_for_nobody_to_read(self) -> None:
        run("next", "--path", str(self.project))
        self.assertFalse((self.project / ".galadon" / "state.json").exists())


class AcceptanceScopeTests(unittest.TestCase):
    def test_only_the_acceptance_section_counts(self) -> None:
        from galadon.config import load_config
        from galadon.loop import unchecked_acceptance

        tmp = Path(tempfile.mkdtemp(prefix="galadon-accept-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        project = tmp / "demo"
        (project / "spec").mkdir(parents=True)
        (project / "spec" / "spec.md").write_text(
            "# spec\n\n## Problem\n\np\n\n## Acceptance\n\n- [x] a\n- [X] b\n\n"
            "### Later\n- [x] c\n\n## Non-goals\n\nn\n\n"
            "## Open questions\n\n- [ ] something for another day\n",
            encoding="utf-8",
        )
        (project / "galadon.yaml").write_text("project: demo\ntemplate: web\n", encoding="utf-8")
        self.assertEqual(unchecked_acceptance(project, load_config(project)), 0)

        (project / "spec" / "spec.md").write_text(
            "# spec\n\n## Problem\n\np\n\n## Acceptance\n\n- [x] a\n- [ ] b\n",
            encoding="utf-8",
        )
        self.assertEqual(unchecked_acceptance(project, load_config(project)), 1)


class DoneActionTests(unittest.TestCase):
    def test_done_names_how_to_start_the_next_loop(self) -> None:
        from galadon.loop import ACTIONS

        self.assertEqual(ACTIONS["done"], "Loop complete. Update the spec for the next loop, then run `galadon new`.")


if __name__ == "__main__":
    unittest.main()
