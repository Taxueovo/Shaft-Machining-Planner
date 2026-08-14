# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a Vulnerability

Please report security issues privately through
[GitHub Security Advisories](https://github.com/Taxueovo/Shaft-Machining-Planner/security/advisories/new)
("Report a vulnerability") rather than opening a public issue.

When reporting, include:

- The affected version or commit hash
- A description of the vulnerability and its impact
- Steps to reproduce, if possible

We will acknowledge your report within a few business days and keep you updated
as the issue is investigated. Please do not disclose the issue publicly until we
have had a chance to address it.

## Scope

This project is a local development tool that may load configuration (including
API keys) from environment files. Never commit credentials, tokens, or internal
infrastructure details to the repository; `.env` files are excluded via
`.gitignore`.

The application is intentionally local-only. Both launchers reject non-loopback
listen addresses. Do not place the frontend or backend behind a public proxy.
User-entered cases, process-card outputs, RAG source documents, vector indexes,
and exported-card RAG records are ignored by Git and must be treated as private.
