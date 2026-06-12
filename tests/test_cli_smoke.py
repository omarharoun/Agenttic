"""CLI smoke test: app imports and registers the full command surface."""
from ascore.cli import app

def test_cli_commands_registered():
    names = {c.callback.__name__ for c in app.registered_commands}
    assert {"generate", "approve", "run", "calibrate",
            "regress", "report", "monitor"} <= names
