# services/

The Python backend: one package, `finance_agents`, plus the Docker image that runs it. It spans layers L2 to L6 (data, models, retrieval, harness, orchestration) and the two L7 entry points (the API and the Model Context Protocol (MCP) server).

## Files

| File | What it owns |
|---|---|
| `Dockerfile` | Builds the `api` image. Build context is the repo root so `db/migrations` ships next to the package. |
| `finance_agents/` | The package. See its [README](finance_agents/README.md). |

## How it is used

`docker compose up` (via `make up`) builds this image and starts `uvicorn finance_agents.api.app:app`. The same image also runs the ingest CLI (`python -m finance_agents.ingest.cli`) and the Temporal worker.

## Rules that hold here

- Dependencies are pinned by `uv.lock` at the repo root; the image installs with `uv sync --frozen`.
- Tests live in [`../tests/`](../tests/README.md), not beside the code.

## Production vs this demo

In production: the Open Neural Network Exchange (ONNX) embedding model is baked into the image or mounted from Elastic File System (EFS), so a task is ready at start.
In this demo: the model downloads on first embed, because baking about 130 MB into every build slows the sample.

## Read next

- [`finance_agents/README.md`](finance_agents/README.md), the package map.
- [`../docs/TOUR.md`](../docs/TOUR.md), the request-order code walk.
