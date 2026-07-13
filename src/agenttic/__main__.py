"""Run the CLI with ``python -m agenttic`` (equivalent to the ``agenttic``
console script; ``ascore`` remains as a deprecated alias)."""
from agenttic.cli import app

if __name__ == "__main__":
    app()
