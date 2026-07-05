"""Service layer: request models → core library calls → response models.

These modules import core functions *by name* so tests can monkeypatch the
Earth Engine seam (composite builders, URL minting, byte fetching) while the
real request parsing, catalog resolution, and response shaping run unfaked.
"""
