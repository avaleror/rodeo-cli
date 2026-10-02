"""Student claim portal service (F5.1): runs on the workshop's portal VM.

Standard library only and relative imports only: ``rodeo fleet portal up`` copies
this package verbatim to the portal VM (as ``rodeo_portal``) and runs it with the
system ``python3``, so the portal never needs rodeo's own dependencies, cloud
credentials or SSH keys. ``tests/test_portal_*.py`` pins that rule.

Design: docs/claim-portal.md (sections 4-6).
"""
