"""Fill an .env file's placeholder secrets with generated values.

Run once per deployment (``make setup``). Existing non-placeholder values are
left alone so re-running cannot invalidate a live database password.
"""
from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

PLACEHOLDERS = {
    "POSTGRES_PASSWORD": "change-me-in-env",
    "INTERNAL_JWT_SECRET": "change-me-too",
}


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "docker/.env")
    text = path.read_text()

    generated: dict[str, str] = {}
    for key, placeholder in PLACEHOLDERS.items():
        current = re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
        if current is None or current.group(1).strip() not in ("", placeholder):
            continue
        value = secrets.token_urlsafe(32 if key != "POSTGRES_PASSWORD" else 18)
        generated[key] = value
        text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, count=1, flags=re.MULTILINE)

    path.write_text(text)
    if generated:
        print(f"generated: {', '.join(sorted(generated))}")
    else:
        print("no placeholder secrets found; nothing changed")


if __name__ == "__main__":
    main()
