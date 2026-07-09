# Project Rules

This is the primary development environment.

## Project

- Canonical repository: `/Projects/telegram-report-dev`
- Ignore `/telegram-report` legacy directory unless explicitly asked.

## Runtime

- Main container: `telegram-report-allinone-dev`
- AI container: `telegram-report-ai-dev`

## Docker Access

The following read-only commands may be used without asking every time:

- `sudo docker ps`
- `sudo docker inspect telegram-report-allinone-dev`
- `sudo docker logs --tail=200 telegram-report-allinone-dev`
- `sudo docker exec telegram-report-allinone-dev cat /app/start.sh`

## Before Making Changes

1. Run `git status`.
2. Verify runtime if needed.
3. Explain the implementation plan.
4. Wait for approval before editing.

## Never

- Change matching logic without approval.
- Change AI recovery thresholds without approval.
- Edit the legacy `/telegram-report` directory.
- Stop, restart, rebuild, or remove Docker containers unless explicitly requested.

If this repository contains duplicate implementations, always identify the active runtime before proposing changes.
