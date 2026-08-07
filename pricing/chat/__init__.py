"""The analyst assistant (W9).

A chat surface over evidence the platform already holds: the catalog and sales
history in the Commerce Service, the run record in app.db, and the same Monte
Carlo engine the pipeline prices with.

The division of labour is the same one the rest of the system runs on. Every
number an answer contains is computed here, deterministically, before a model is
consulted; the model classifies the question and phrases the reply. With the
gateway down the assistant still answers — in plainer prose, from the same
figures.
"""

from pricing.chat.service import ask, suggestions  # noqa: F401
