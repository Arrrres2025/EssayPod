"""Isolated HTTP worker used to make provider deadlines cancellable.

The request description is accepted only over stdin so API credentials never
appear in the process arguments. The parent can terminate this process on an
absolute timeout, which is not possible with a blocked Python thread.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any


def main() -> int:
    try:
        spec = json.loads(sys.stdin.read())
        parent_pid = int(spec.get("parent_pid") or os.getppid())

        def stop_when_parent_exits() -> None:
            while True:
                time.sleep(0.5)
                if os.getppid() != parent_pid:
                    os._exit(143)

        threading.Thread(
            target=stop_when_parent_exits,
            name="llm-parent-watchdog",
            daemon=True,
        ).start()
        request = urllib.request.Request(
            str(spec["url"]),
            data=json.dumps(
                spec["payload"], ensure_ascii=False
            ).encode("utf-8"),
            headers=dict(spec.get("headers") or {}),
            method="POST",
        )
        with urllib.request.urlopen(
            request, timeout=float(spec.get("socket_timeout") or 90)
        ) as response:
            data: Any = json.loads(response.read().decode("utf-8"))
        result = {"ok": True, "data": data}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
            detail = (
                body.get("error", {}).get("message", "")
                if isinstance(body.get("error"), dict)
                else body.get("message", "")
            )
        except (json.JSONDecodeError, AttributeError):
            pass
        result = {
            "ok": False,
            "kind": "http",
            "status": exc.code,
            "detail": str(detail or ""),
        }
    except urllib.error.URLError as exc:
        result = {"ok": False, "kind": "network", "detail": str(exc)}
    except Exception as exc:
        result = {"ok": False, "kind": "invalid", "detail": str(exc)}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
