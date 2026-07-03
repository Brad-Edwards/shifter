"""Shifter ACES contract surface.

This package holds Shifter's ACES (Adversarial Cyber Exercise System) contract
artifacts. The first artifact (issue #1261) is the ``provisioning-only`` backend
manifest published in :mod:`shared.aces.manifest`.

Nothing is imported eagerly here on purpose. The manifest builder imports the
``aces-sdl`` tooling, which is a dev/test-scoped dependency for this
publication-only slice; keeping ``import shared.aces`` inert ensures the ACES
tooling never enters Shifter's production runtime import graph until a later
slice (#1262) promotes it to a runtime dependency.
"""
