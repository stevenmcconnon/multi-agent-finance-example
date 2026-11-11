# Diagrams

Three kinds live in this repository.

**Mermaid, inline in Markdown** (README: sequence, state, flowchart). GitHub renders them; no build step.
Edit the fenced block, preview on GitHub or with `npx @mermaid-js/mermaid-cli -i README.md` for a local
render. The state diagram must match `services/finance_agents/agent/graph.py::_graph()`: when a node or edge
changes there, change the diagram in the same commit.

**Hand-written Scalable Vector Graphics (SVG)** (`stack.svg`, `aws.svg`,
`multi-agent-rag-architecture.svg`). They are plain text with a fixed `viewBox`, `system-ui` fonts, no
external assets, and colours chosen to read on both the light and dark GitHub themes. Edit them in a text
editor; the layout uses `<rect>`, `<text>`, paths, and arrowhead markers. Check a change by opening the file
in a browser at 100 % zoom: every label must be readable without zooming.

**Annotated product image** (`multi-agent-rag-turn-annotated.png`). This is a guided derivative of
`../screenshots/12-agent-team.png`. It identifies the question, authorization gate, authorized retrieval,
and write/verify stages for a first-time README reader. If the product timeline changes, regenerate the
annotation from a current screenshot rather than editing the old interface into a new state.

`stack.svg` is seven rows (L7 down to L1), each with the layer name, its folder, and two cells: "Local demo
(what `make up` runs)" and "Cloud mode (Terraform, not applied)", at most four technology names per cell.
To regenerate it: edit the text, keeping only technologies that `grep` finds in `pyproject.toml`,
`apps/web/package.json`, `docker-compose.yml` or `infra/terraform` (no library that is merely planned), then
keep it in step with the README's "The stack" table. Row bands are mid-dark fills with white text; cells are
`#f8fafc` with `#111827` text, so both read on either GitHub theme. `aws.svg` names the Terraform modules' resources; keep it in step with
`infra/terraform/modules`. Neither carries a number that was not measured here.

`multi-agent-rag-architecture.svg` is the portfolio-level system view. It must keep three visual classes
distinct: teal for deterministic controls, purple for model reasoning, and amber for a human decision. Its
agent names and sequence must match `services/finance_agents/agent/graph.py` and `agent/team.py`; its
control plane must stay aligned with `docs/GUARDRAILS.md`.
