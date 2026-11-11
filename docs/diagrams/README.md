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

`stack.svg` names, per layer, the folder and the libraries; keep it in step with the README's "Repository
layout" table. `aws.svg` names the Terraform modules' resources; keep it in step with
`infra/terraform/modules`. Neither carries a number that was not measured here.
