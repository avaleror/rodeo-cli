"""Per-lab player for rodeo tracks on rodeo labs.

``runner`` runs a track's chapter scripts (setup, check, solve, cleanup) in
chapter order and renders the assignment text. ``agent`` is a small
``agent variable set/get`` command the track scripts call, put on PATH only
for those scripts.
"""
