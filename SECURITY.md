# Security policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.6.x (current) | Yes |
| < 0.6 | No |

## Reporting a vulnerability

Do not open a public GitHub issue for security vulnerabilities.

Send a description of the issue to **anvarui@gmail.com** with:

- A description of the vulnerability and its potential impact
- Steps to reproduce
- Any suggested fix if you have one

We will acknowledge receipt within 72 hours and aim to release a fix within 14 days for confirmed vulnerabilities.

## Scope

rodeo-cli is designed for **isolated training labs**, not production systems. The security model reflects that:

- TLS verification is disabled (self-signed certificates on lab VMs)
- Lab networking uses predictable CIDR ranges (`192.168.122.0/24`)
- Host firewall exposes UI ports by design
- SSH uses `StrictHostKeyChecking=no` for ephemeral VMs

Findings that only affect the lab environment itself (e.g. an attacker already on the lab VM can access other lab VMs) are out of scope. In-scope issues include:

- Credential exposure (secrets written to unprotected paths, leaked in logs)
- Host privilege escalation beyond what `rodeo install-deps` / `rodeo up` explicitly request
- Supply chain issues (malicious content injected via bundled Ansible roles or downloaded artifacts)
- Vulnerabilities in the `sudo` self-escalation path in `rodeo/privilege.py`
