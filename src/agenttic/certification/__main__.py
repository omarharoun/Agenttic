"""``python -m agenttic.certification gen-key`` — unchanged from the flat module."""

import json
import sys

from .safety_cert import generate_signing_key

if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) >= 2 and sys.argv[1] == "gen-key":
        priv_b64, entry = generate_signing_key()
        print("# Keep this SECRET. Set it as AGENTTIC_CERT_SIGNING_KEY:")
        print(priv_b64)
        print("\n# Public key (published; safe to share):")
        print(json.dumps(entry, indent=2))
    else:
        print("usage: python -m agenttic.certification gen-key")
