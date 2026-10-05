"""Rodeo Builder: a web UI that composes a new rodeo from a lab engine and story chapters.

``discovery`` reads the data from the rodeo-cli tree, ``api`` answers the page's
requests, and ``htdocs/`` is the page itself. ``scripts/build-builder-static.py``
embeds every answer into one static HTML page (published with the docs).
"""
