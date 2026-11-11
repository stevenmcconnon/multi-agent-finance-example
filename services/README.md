# services/

The Python backend: one package, `harness_rag`, plus the Docker image that runs it. It spans layers L2 to L6 (data, models, retrieval, harness, orchestration) and the API edge for L7.

## Files

| File | What it owns |
|---|---|
| `Dockerfile` | Builds the `api` image. Build context is the repo root so `db/migrations` ships next to the package. |
| `harness_rag/` | The package. See its [README](harness_rag/README.md). |

## How it is used

`docker compose up` (via `make up`) builds this image and starts `uvicorn harness_rag.api.app:app`. The same image also runs the ingest CLI (`python -m harness_rag.ingest.cli`) and the Temporal worker.

## Rules that hold here

- Dependencies are pinned by `uv.lock` at the repo root; the image installs with `uv sync --frozen`.
- Tests live in [`../tests/`](../tests/README.md), not beside the code.

## Production vs this demo

In production: the Open Neural Network Exchange (ONNX) embedding model is baked into the image or mounted from Elastic File System (EFS), so a task is ready at start.
In this demo: the model downloads on first embed, because baking about 130 MB into every build slows the sample.

## Read next

- [`harness_rag/README.md`](harness_rag/README.md), the package map.
- [`../docs/TOUR.md`](../docs/TOUR.md), the request-order code walk.
