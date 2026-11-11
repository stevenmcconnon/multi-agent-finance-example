# docs/

The written explanation of the system. Start with the tour, then the architecture.

## Files

| Document | What it is |
|---|---|
| [`TOUR.md`](TOUR.md) | Code walk in request order, from bearer token to stored checkpoint, with file and function per stop. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | The seven layers, ports and adapters, stores by purpose, and the harness turn before, during and after. |
| [`CONTRACTS.md`](CONTRACTS.md) | Cross-layer contracts pinned before the build: entry points, routes, JWT claims, auth flow, the agent team. |
| [`SECURITY.md`](SECURITY.md) | Trust boundaries, each threat and its control, availability, and what the demo does not do. |
| [`DEMO.md`](DEMO.md) | The five-step demo: what to click, what to say, what the screen proves. |
| [`AUDIT_2026-11-10.md`](AUDIT_2026-11-10.md) | A dated review of the repo: what it does, checks run, and findings to fix. |
| [`QA_2026-11-10.md`](QA_2026-11-10.md) | A dated browser quality-assurance run of the web app with recorded facts. |
| [`adr/`](adr/README.md) | Architecture decision records, one per decision. |
| [`diagrams/`](diagrams/README.md) | How the diagrams are made, plus `stack.svg` and `aws.svg`. |
| `screenshots/` | Twelve PNGs from the QA run, used by the README and `DEMO.md`. |

## How it is used

Read in this order: root README, `TOUR.md`, `ARCHITECTURE.md`. Use `CONTRACTS.md` when changing a boundary and `SECURITY.md` when adding a control.

## Rules that hold here

- Docs state only what was measured in this repo; no invented load or latency figures (`AGENTS.md`).
- Where the demo takes a cheaper path, write the `In production:` and `In this demo:` pair.
- Expand every acronym on first use. Dated files (`AUDIT`, `QA`) are records; do not rewrite them.
- When the turn graph changes, update the state diagram in the same commit ([`diagrams/README.md`](diagrams/README.md)).

## Read next

- [`TOUR.md`](TOUR.md).
- [`adr/README.md`](adr/README.md).
