"""Round-11 hooks: the per-edit gate and the installer.

The aislop loop parity: an agent hook that gates the file just edited
with the same engines and the same registry the CI scan uses, and an
installer that writes the hook configs without ever silently rewriting
a consumer's existing settings.
"""

import json
import sys
from pathlib import Path

import hook_install
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

# The templates the isolated hook root needs: hook_gate imports the
# slop_* engines from the same scripts/ directory it lives in.
ENGINE_FILES = sorted(SCRIPTS.glob("slop_*.py")) + [
    SCRIPTS / "hook_gate.py",
    SCRIPTS / "ai_slop_phrases.json",
]


@pytest.fixture()
def hook_root(tmp_path):
    """A minimal checkout with the gate + engines, like a consumer copy."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for source in ENGINE_FILES:
        (scripts / source.name).write_text(source.read_text(encoding="utf-8"))
    return tmp_path


def _run_gate(root: Path, *argv: str):
    import subprocess

    return subprocess.run(
        [sys.executable, str(root / "scripts" / "hook_gate.py"), *argv],
        capture_output=True,
        text=True,
        cwd=str(root),
    )


# ---------------------------------------------------------------------------
# hook_gate: the per-edit decision
# ---------------------------------------------------------------------------

SLOP = "def run():\n    try:\n        work()\n    except Exception:\n        pass\n"
CLEAN = "def ok():\n    return 1\n"


def test_gate_blocks_a_blocking_finding(hook_root):
    (hook_root / "svc.py").write_text(SLOP)
    proc = _run_gate(hook_root, "svc.py")
    assert proc.returncode == 1
    assert "BLOCKING" in proc.stdout and "swallowed-exception" in proc.stdout


def test_gate_passes_clean_and_advisory_files(hook_root):
    (hook_root / "fine.py").write_text(CLEAN)
    # mutable-default is a per-file advisory check, exactly the grade the
    # gate prints without failing on (repo-level advisory views belong to
    # the CI scan by design).
    (hook_root / "advisory.py").write_text("def collect(items=[]):\n    return items\n")
    proc = _run_gate(hook_root, "fine.py", "advisory.py")
    assert proc.returncode == 0
    assert "advisory" in proc.stdout


def test_gate_reads_a_claude_payload(hook_root):
    (hook_root / "svc.py").write_text(SLOP)
    import subprocess

    payload = json.dumps({"tool_input": {"file_path": str(hook_root / "svc.py")}})
    proc = subprocess.run(
        [sys.executable, str(hook_root / "scripts" / "hook_gate.py"), "--stdin-claude"],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(hook_root),
    )
    assert proc.returncode == 1


def test_gate_without_files_fails_closed(hook_root):
    proc = _run_gate(hook_root)
    assert proc.returncode == 2
    assert "no files" in proc.stderr


def test_gate_skips_a_deleted_file(hook_root):
    proc = _run_gate(hook_root, "gone.py")
    assert proc.returncode == 0


# ---------------------------------------------------------------------------
# hook_install: the generators
# ---------------------------------------------------------------------------


def test_install_claude_creates_settings(hook_root):
    proc = hook_install.main(["--agent", "claude", "--root", str(hook_root)])
    assert proc == 0
    settings = json.loads((hook_root / ".claude" / "settings.json").read_text())
    hook = settings["hooks"]["PostToolUse"][0]
    assert hook["matcher"] == "Edit|Write|MultiEdit"
    assert "--stdin-claude" in hook["hooks"][0]["command"]


def test_install_claude_never_rewrites_existing_settings(hook_root):
    claude = hook_root / ".claude"
    claude.mkdir()
    existing = {"hooks": {"PostToolUse": []}}
    (claude / "settings.json").write_text(json.dumps(existing))
    proc = hook_install.main(["--agent", "claude", "--root", str(hook_root)])
    assert proc == 0
    # The consumer file is byte-identical; the block ships beside it.
    assert json.loads((claude / "settings.json").read_text()) == existing
    snippet = json.loads((claude / "master-ci-hook.json").read_text())
    assert snippet["hooks"]["PostToolUse"][0]["matcher"] == "Edit|Write|MultiEdit"


def test_install_pre_commit_writes_and_refuses(hook_root):
    import subprocess

    subprocess.run(["git", "init", "-q", str(hook_root)], check=True)
    proc = hook_install.main(["--agent", "pre-commit", "--root", str(hook_root)])
    assert proc == 0
    script = (hook_root / ".git" / "hooks" / "pre-commit").read_text()
    assert "git diff --cached" in script and "hook_gate.py" in script
    # A second run refuses; --force replaces.
    refused = hook_install.main(["--agent", "pre-commit", "--root", str(hook_root)])
    assert refused == 2
    forced = hook_install.main(["--agent", "pre-commit", "--root", str(hook_root), "--force"])
    assert forced == 0


def test_install_pre_commit_without_git_fails_closed(hook_root):
    proc = hook_install.main(["--agent", "pre-commit", "--root", str(hook_root)])
    assert proc == 2


def test_install_recipe_prints_and_writes_nothing(hook_root):
    proc = hook_install.main(["--agent", "recipe", "--root", str(hook_root)])
    assert proc == 0
    assert not (hook_root / ".claude").exists()


def test_install_outside_the_template_fails_closed(tmp_path):
    proc = hook_install.main(["--agent", "claude", "--root", str(tmp_path)])
    assert proc == 2
