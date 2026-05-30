"""
indexer_api.py

FastAPI app que expõe o pipeline de indexação como endpoint HTTP.
Projetado para rodar em Azure Container Apps com User-Assigned Managed Identity.

Endpoints:
  POST /index   — dispara indexação em background (202 Accepted imediato)
  GET  /status  — estado atual e histórico da última execução
  GET  /health  — health check para o Container App liveness probe

Autenticação (transparente):
  - MI_CLIENT_ID set  → ManagedIdentityCredential (container em produção)
  - MI_CLIENT_ID vazio → DefaultAzureCredential → az login (dev local)

Uso local (dev):
  uvicorn src.indexer_api:app --reload --port 8000

Trigger de indexação:
  curl -X POST http://localhost:8000/index
  curl http://localhost:8000/status
"""

import logging
import os
import sys
import threading
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException

# garante que src/ está no path quando rodado via uvicorn src.indexer_api:app
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI(
    title="graph4spo Indexer API",
    version="2.0.0",
    description="Pipeline SharePoint → Azure AI Search via Container App + Managed Identity",
)

# ---------------------------------------------------------------------------
# Estado global da indexação (thread-safe com Lock)
# ---------------------------------------------------------------------------
_state: dict = {
    "status":      "idle",   # idle | running | done | error
    "started_at":  None,
    "finished_at": None,
    "error":       None,
}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_indexer() -> None:
    """Executa o pipeline de indexação em background thread."""
    with _lock:
        _state.update(status="running", started_at=_now(), finished_at=None, error=None)
    try:
        log.info("Iniciando pipeline de indexação...")
        from index_documents import main as index_main  # import tardio evita efeitos em import
        index_main()
        with _lock:
            _state.update(status="done", finished_at=_now())
        log.info("Indexação concluída com sucesso.")
    except Exception as exc:
        log.exception("Falha no pipeline de indexação")
        with _lock:
            _state.update(status="error", finished_at=_now(), error=str(exc))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/index", status_code=202)
def trigger_index(background_tasks: BackgroundTasks):
    """
    Dispara a indexação em background e retorna 202 imediatamente.
    Retorna 409 Conflict se já houver uma indexação em andamento.
    Consulte GET /status para acompanhar o progresso.
    """
    with _lock:
        if _state["status"] == "running":
            raise HTTPException(
                status_code=409,
                detail="Indexação já em andamento. Consulte GET /status.",
            )
    background_tasks.add_task(_run_indexer)
    return {"status": "accepted", "message": "Indexação iniciada.", "status_url": "/status"}


@app.get("/status")
def get_status():
    """Retorna o estado atual e o histórico da última execução."""
    with _lock:
        return dict(_state)


@app.get("/health")
def health():
    """Health check — usado pelo Azure Container Apps liveness/readiness probe."""
    return {"status": "ok", "service": "graph4spo-indexer", "version": "2.0.0"}
