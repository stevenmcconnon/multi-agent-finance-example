"""L2 Data/Pipelines: SEC EDGAR -> Postgres (documents, document_acl, chunks, facts).

edgar.py (HTTP, fair-access) -> parse.py (pure) -> facts.py (pure) -> writer.py (one transaction per
document). jobs.py tracks attempts in ingest_jobs (the 5th failure = status 'dead', the dead-letter record);
workflow.py/worker.py make it durable on Temporal; cli.py drives all of it.
"""
