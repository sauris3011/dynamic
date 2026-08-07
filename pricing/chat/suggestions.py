"""Questions to offer the analyst, chosen from the state the system is in.

An empty chat box is a bad brief. Generic prompts ("try asking about pricing!")
are a worse one, because they teach nothing about what this system can actually
answer. Every suggestion here names something real — a SKU that exists, a run
that ran, a queue that has items in it — so the first click demonstrates a
capability rather than producing an apology.

The list therefore changes with the system: before any run it points at the
catalog and the method; after one it points at what that run decided and what is
waiting on a human.
"""

from __future__ import annotations

from pricing.chat.catalog import Catalog, snapshot
from pricing.db.app_db import session
from pricing.llm.schemas import ChatRoute


def _pick_sku(catalog: Catalog) -> str:
    if not catalog.available:
        return ""
    ranked = sorted(
        catalog.products, key=lambda p: catalog.velocity(p["sku"]), reverse=True
    )
    return ranked[0]["sku"] if ranked else ""


def _state() -> dict:
    """What exists to be asked about."""
    with session() as conn:
        runs = conn.execute(
            "SELECT run_id, scope_kind, scope_value, objective FROM runs"
            " WHERE status = 'completed' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        pending = conn.execute(
            "SELECT band, COUNT(*) AS n FROM recommendations WHERE status = 'pending'"
            " GROUP BY band"
        ).fetchall()
        escalated = conn.execute(
            "SELECT sku FROM recommendations WHERE band = 'escalate'"
            " ORDER BY ABS(expected_revenue_delta) DESC LIMIT 1"
        ).fetchone()
        outcomes = conn.execute("SELECT COUNT(*) AS n FROM outcomes").fetchone()["n"]
    return {
        "run": dict(runs) if runs else None,
        "pending": {r["band"]: r["n"] for r in pending},
        "escalated_sku": escalated["sku"] if escalated else None,
        "outcomes": outcomes,
    }


def starters(limit: int = 6) -> list[dict]:
    """Opening questions for a chat with no history yet."""
    catalog = snapshot()
    state = _state()
    sku = state["escalated_sku"] or _pick_sku(catalog)
    category = catalog.categories[0] if catalog.categories else ""
    out: list[dict] = []

    def add(label: str, question: str, kind: str) -> None:
        out.append({"label": label, "question": question, "kind": kind})

    run = state["run"]
    if run:
        scope = run["scope_value"] or "the full catalog"
        add("Summarise the last run",
            f"Summarise run {run['run_id']} over {scope} — what did it decide and "
            "what needs my attention?", "analysis")
        waiting = state["pending"].get("review", 0) + state["pending"].get("escalate", 0)
        if waiting:
            add(f"My queue ({waiting})",
                "What is waiting in my review queue right now, and which items are "
                "worth the most?", "analysis")
        if state["escalated_sku"]:
            add(f"Why {state['escalated_sku']} escalated",
                f"Why did {state['escalated_sku']} land in the escalate band rather "
                "than auto-approve?", "analysis")
    else:
        add("What a run does",
            "What happens when I start a pricing run, and what will I get at the "
            "end of it?", "platform")

    if sku:
        add(f"Price {sku} today", f"How is {sku} positioned today — price, cost, "
            "margin, stock and competitors?", "product")
        add(f"What if {sku} +5%",
            f"What if we raised {sku} by 5% — what happens to revenue and margin "
            "over the next 28 days?", "what_if")
    if category:
        add(f"{category} last 90 days",
            f"How has {category} traded over the last 90 days?", "history")
    if state["outcomes"]:
        add("Forecast accuracy",
            "How accurate have our price forecasts turned out to be against what "
            "actually sold?", "analysis")
    add("How you decide", "How does this platform decide a price, and what stops it "
        "from pushing one I would not agree with?", "platform")

    return out[:limit]


def follow_ups(route: ChatRoute, catalog: Catalog, data: dict | None = None) -> list[str]:
    """Where an analyst usually goes next, given what they just asked."""
    data = data or {}
    sku = ""
    if route.skus:
        sku = route.skus[0]
    elif data.get("skus"):
        sku = data["skus"][0]
    elif data.get("busiest"):
        sku = data["busiest"][0]
    category = route.category or (catalog.categories[0] if catalog.categories else "")

    if route.intent == "product" and sku:
        return [
            f"How has {sku} traded over the last 90 days?",
            f"What if we cut {sku} by 5%?",
            f"What did the last run recommend for {sku}, and why?",
        ]
    if route.intent == "history":
        return [
            f"What if we raised {sku or 'the busiest SKU'} by 5%?",
            f"How is {sku or category} positioned against competitors today?",
            "Has the platform priced any of this yet?",
        ]
    if route.intent == "what_if":
        return [
            f"Stress test that move for {sku} — demand collapse, competitor undercut, "
            "cost spike.",
            f"What would the margin objective recommend for {sku} instead?",
            f"How has {sku} actually traded over the last 90 days?",
        ]
    if route.intent == "analysis":
        return [
            "What is waiting in my review queue right now?",
            "How accurate have our forecasts been against realized sales?",
            f"Why did {sku or 'the largest mover'} land in the band it did?",
        ]
    return [
        "What do the three autonomy bands mean in practice?",
        "Which documents are you grounded on right now?",
        f"How has {category or 'the catalog'} traded over the last 90 days?",
    ]
