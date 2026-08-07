"""Third-party market data feeds.

The problem statement asks for clear separation between the AI solution layer,
enterprise transactional systems, and third-party feeds. This package is that
third tier: competitor prices are *external market observations*, not the
retailer's own records, so they deliberately do not live in the Commerce Service.
"""
