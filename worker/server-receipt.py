#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


def latest_log(instance: Path) -> Path:
    logs = list(instance.glob("SpaceEngineersDedicated_*.log"))
    if not logs:
        raise SystemExit(f"no dedicated-server log found in {instance}")
    return max(logs, key=lambda path: path.stat().st_mtime_ns)


def first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else None


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: server-receipt.py INSTANCE_DIRECTORY")
    instance = Path(sys.argv[1]).resolve()
    log = latest_log(instance)
    raw = log.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    fatal_markers = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"(?:Exception while loading world|Fatal error|MOD_ERROR)", line)
    ]
    receipt = {
        "schema": "vragecage.server-receipt.v1",
        "log": str(log),
        "log_bytes": len(raw),
        "log_sha256": hashlib.sha256(raw).hexdigest(),
        "app_version": first_match(r"App Version:\s*(.+)$", text),
        "bind": first_match(r"Bind IP\s*:\s*(.+)$", text),
        "session_loaded": "Session loaded" in text,
        "game_ready": "Game ready..." in text,
        "clean_shutdown": all(
            marker in text for marker in ("Exiting..", "Saving world - END", "Steam closed", "Log Closed")
        ),
        "fatal_markers": fatal_markers,
    }
    receipt["passed"] = bool(
        receipt["session_loaded"]
        and receipt["game_ready"]
        and not receipt["fatal_markers"]
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
