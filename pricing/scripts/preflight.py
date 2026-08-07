"""Pre-flight validation (FR-074, FR-075, FR-076, NFR-022).

Fails fast with a specific, actionable message. A vague "startup failed" costs
more time than the check saves, so every failure names the thing to fix.

Exit codes:  0 = all good   1 = blocking failure   0 (with warnings) = degraded
"""

from __future__ import annotations

import argparse
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

GREEN, YELLOW, RED, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[0m"


@dataclass
class Check:
    name: str
    ok: bool
    blocking: bool
    detail: str
    fix: str = ""


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) != 0


def _port_serving(port: int, host: str = "127.0.0.1") -> bool:
    return not _port_free(port, host)


def run_checks(expect_running: bool = False) -> list[Check]:
    checks: list[Check] = []

    # --- Python version ------------------------------------------------
    v = sys.version_info
    checks.append(
        Check(
            "Python version", v >= (3, 10), True,
            f"Python {v.major}.{v.minor}.{v.micro}",
            "Python 3.10+ is required (3.12 recommended).",
        )
    )

    # --- Virtual environment -------------------------------------------
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    checks.append(
        Check(
            "Virtual environment", in_venv, False,
            f"prefix={sys.prefix}" if in_venv else "Not running inside a venv",
            "Activate with .venv\\Scripts\\activate (Windows) or "
            "source .venv/bin/activate (POSIX).",
        )
    )

    # --- Required packages ---------------------------------------------
    required = {
        "fastapi": "web framework", "uvicorn": "ASGI server",
        "pydantic": "validation", "httpx": "HTTP client",
        "numpy": "analytics", "pandas": "analytics", "scipy": "statistics",
        "structlog": "logging", "tenacity": "retries",
    }
    optional = {
        "langchain": "LLM routing", "langgraph": "agent orchestration",
        "chromadb": "vector store", "langfuse": "telemetry",
    }
    missing, missing_opt = [], []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    for pkg in optional:
        try:
            __import__(pkg)
        except ImportError:
            missing_opt.append(pkg)

    checks.append(
        Check(
            "Core packages", not missing, True,
            "all present" if not missing else f"missing: {', '.join(missing)}",
            "pip install -r requirements.txt",
        )
    )
    checks.append(
        Check(
            "AI packages", not missing_opt, False,
            "all present" if not missing_opt else f"missing: {', '.join(missing_opt)}",
            "pip install -r requirements.txt  (agents and RAG stay disabled until "
            "these are installed; deterministic pricing still works)",
        )
    )

    # --- Configuration --------------------------------------------------
    try:
        from pricing.config import get_settings

        settings = get_settings()
        checks.append(Check("Configuration", True, True, "validated", ""))
    except Exception as exc:
        checks.append(
            Check("Configuration", False, True, f"{type(exc).__name__}: {exc}",
                  "Copy .env.example to .env and correct the reported field.")
        )
        return checks  # everything below depends on settings

    # --- Data directory writability -------------------------------------
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.data_dir / ".preflight"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
        detail = str(settings.data_dir.resolve())
    except Exception as exc:
        writable, detail = False, f"{settings.data_dir}: {exc}"
    checks.append(
        Check("Data directory", writable, True, detail,
              "Ensure the DATA_DIR path exists and is writable by this user.")
    )

    # --- Ports ------------------------------------------------------------
    for label, port in (
        ("Commerce", settings.commerce_port),
        ("Pricing", settings.pricing_port),
        ("UI", settings.ui_port),
    ):
        if expect_running:
            serving = _port_serving(port)
            checks.append(
                Check(f"{label} port {port}", serving, label == "Commerce",
                      "serving" if serving else "nothing listening",
                      f"Start the {label.lower()} service.")
            )
        else:
            free = _port_free(port)
            checks.append(
                Check(f"{label} port {port}", free, False,
                      "free" if free else "already in use",
                      f"Stop whatever is bound to {port}, or change the port in .env.")
            )

    # --- Commerce reachability (FR-076) ----------------------------------
    try:
        from pricing.clients.commerce import CommerceClient

        with CommerceClient() as client:
            health = client.health()
        ok = health.get("status") == "ok"
        checks.append(
            Check(
                "Commerce Service", ok, expect_running,
                f"status={health.get('status')} products={health.get('product_count')}",
                "Run: python -m commerce.seed   (then restart the commerce service)",
            )
        )
    except Exception as exc:
        checks.append(
            Check("Commerce Service", False, expect_running,
                  f"unreachable at {settings.commerce_base_url}: "
                  f"{type(exc).__name__}",
                  "Start it with: python -m uvicorn commerce.main:app --port 8001")
        )

    # --- LLM gateway ------------------------------------------------------
    try:
        import httpx

        from pricing.core.tls import verify_option

        with httpx.Client(timeout=6.0, verify=verify_option(settings)) as client:
            resp = client.get(f"{settings.llm_gateway_url.rstrip('/')}/health")
            reachable = resp.status_code < 500
        detail = f"{settings.llm_gateway_url} responded {resp.status_code}"
    except Exception as exc:
        reachable = False
        detail = f"{settings.llm_gateway_url} unreachable ({type(exc).__name__})"
    checks.append(
        Check(
            "LLM gateway", reachable, False, detail,
            "Agent narration will be unavailable; deterministic pricing is "
            "unaffected. Check LLM_GATEWAY_URL in .env.",
        )
    )

    # --- TLS posture (NFR-019) --------------------------------------------
    from pricing.core.tls import tls_status

    status = tls_status(settings)
    checks.append(
        Check(
            "TLS posture", bool(status["secure"]), False, str(status["detail"]),
            "Set CA_BUNDLE_PATH to the corporate root CA to restore verification.",
        )
    )

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-flight validation")
    parser.add_argument(
        "--expect-running", action="store_true",
        help="Assert services are already up (post-start check) rather than "
             "asserting their ports are free (pre-start check).",
    )
    args = parser.parse_args()

    print("\n  Dynamic Pricing Engine — pre-flight\n" + "  " + "-" * 62)
    checks = run_checks(expect_running=args.expect_running)

    blocking_failures = 0
    warnings = 0
    for c in checks:
        if c.ok:
            mark, colour = "OK  ", GREEN
        elif c.blocking:
            mark, colour = "FAIL", RED
            blocking_failures += 1
        else:
            mark, colour = "WARN", YELLOW
            warnings += 1
        print(f"  [{colour}{mark}{RESET}] {c.name:<24} {c.detail}")
        if not c.ok and c.fix:
            print(f"         -> {c.fix}")

    print("  " + "-" * 62)
    if blocking_failures:
        print(f"  {RED}{blocking_failures} blocking failure(s). Cannot start.{RESET}\n")
        return 1
    if warnings:
        print(f"  {YELLOW}Ready with {warnings} warning(s) — degraded features "
              f"noted above.{RESET}\n")
        return 0
    print(f"  {GREEN}All checks passed.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
