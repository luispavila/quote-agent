import logging
import uuid

from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="quote-agent", version="0.1.0")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    model: str


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "ok": True,
        "env": settings.app_env,
        "model": settings.anthropic_model,
        "llm_configured": settings.anthropic_api_key is not None,
        "langfuse": settings.langfuse_enabled,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    settings = get_settings()
    if settings.anthropic_api_key is None:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY não configurada no servidor")

    from app.graph import build_graph
    from app.tracing import callbacks

    session_id = req.session_id or str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": session_id},
        "callbacks": callbacks(),
        "run_name": "quote-agent-chat",
        "metadata": {"session_id": session_id},
    }
    try:
        result = build_graph().invoke({"messages": [HumanMessage(content=req.message)]}, config)
    except Exception:
        logger.exception("Falha ao invocar o grafo")
        raise HTTPException(status_code=502, detail="Falha ao consultar o modelo")

    reply = result["messages"][-1].content
    if isinstance(reply, list):  # blocos de conteúdo → concatena só o texto
        reply = "".join(b.get("text", "") for b in reply if isinstance(b, dict))
    return ChatResponse(reply=reply, session_id=session_id, model=settings.anthropic_model)
