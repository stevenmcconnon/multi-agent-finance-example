# Diagrams

Two kinds live in this repository.

**Mermaid, inline in Markdown** (README: sequence, state, flowchart). GitHub renders them; no build step.
Edit the fenced block, preview on GitHub or with `npx @mermaid-js/mermaid-cli -i README.md` for a local
render. The state diagram must match `services/harness_rag/agent/graph.py::_graph()`: when a node or edge
changes there, change the diagram in the same commit.

**Hand-written Scalable Vector Graphics (SVG)** (`stack.svg`, `aws.svg`). Plain text, a `viewBox` of 1100 or 1200 wide, `system-ui`
font, no gradients, no external assets, colours chosen so white text on mid-dark fills reads on both the
light and the dark GitHub theme. Edit them in a text editor; the layout is a grid of `<rect>` and `<text>`
with absolute coordinates, and a `<marker>` for arrowheads. Check a change by opening the file in a
browser at 100 % zoom: every label must be readable without zooming.

`stack.svg` is seven rows (L7 down to L1), each with the layer name, its folder, and two cells: "Local demo
(what `make up` runs)" and "Cloud mode (Terraform, not applied)", at most four technology names per cell.
To regenerate it: edit the text, keeping only technologies that `grep` finds in `pyproject.toml`,
`apps/web/package.json`, `docker-compose.yml` or `infra/terraform` (no library that is merely planned), then
keep it in step with the README's "The stack" table. Row bands are mid-dark fills with white text; cells are
`#f8fafc` with `#111827` text, so both read on either GitHub theme. `aws.svg` names the Terraform modules' resources; keep it in step with
`infra/terraform/modules`. Neither carries a number that was not measured here.
