"""Per-lab player for Instruqt-style tracks on rodeo labs.

``runner`` runs a track's chapter scripts (setup, check, solve, cleanup) in
the order Instruqt does and renders the assignment text. ``agent`` is the
stand-in for Instruqt's in-sandbox ``agent`` binary, put on PATH only for
those scripts.
"""
