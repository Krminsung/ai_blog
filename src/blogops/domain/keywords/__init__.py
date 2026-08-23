"""Keyword intelligence domain.

The package deliberately separates licensed provider collection from deterministic
normalisation, scoring and clustering.  No provider adapter may silently substitute
synthetic metrics when an official or licensed source is unavailable.
"""
