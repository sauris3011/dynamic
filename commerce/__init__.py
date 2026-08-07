"""Commerce Service — the enterprise system of record.

Represents the retailer's existing e-commerce backend (PRD 4.1, D13). Owns the
catalog, sales transactions, inventory, and the live price book.

This service knows nothing about AI. The pricing platform reads from it and
writes approved prices back to it over HTTP only — there is no shared database
and no cross-service import. A shortcut would be visible as an illegal import.
"""

__version__ = "1.1.0"
