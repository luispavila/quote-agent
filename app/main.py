import hmac
import logging
import uuid
from collections import deque

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
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
        "primary": settings.primary_provider,
        "model": settings.primary_model,
        "fallback": settings.anthropic_api_key is not None and settings.primary_provider == "featherless",
        "llm_configured": settings.llm_configured,
        "langfuse": settings.langfuse_enabled,
        "whatsapp": settings.wa_configured,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    settings = get_settings()
    if not settings.llm_configured:
        raise HTTPException(
            status_code=503, detail="Nenhuma chave de LLM configurada (FEATHERLESS_API_KEY ou ANTHROPIC_API_KEY)"
        )

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
    return ChatResponse(reply=reply, session_id=session_id, model=settings.primary_model)


# ---------- WhatsApp (wa-service → cá) ----------

class WaWebhook(BaseModel):
    event: str
    messageId: str | None = None
    from_: str | None = Field(default=None, alias="from")
    fromJid: str | None = None
    isGroup: bool = False
    fromMe: bool = False
    pushName: str | None = None
    text: str | None = None
    status: str | None = None

    model_config = {"populate_by_name": True}


_seen_message_ids: deque[str] = deque(maxlen=500)


def _agent_reply(phone: str, text: str) -> None:
    from app.graph import build_graph
    from app.tracing import callbacks
    from app.wa import send_text

    config = {
        "configurable": {"thread_id": f"wa:{phone}"},
        "callbacks": callbacks(),
        "run_name": "quote-agent-whatsapp",
        "metadata": {"channel": "whatsapp", "phone": phone},
    }
    try:
        result = build_graph().invoke({"messages": [HumanMessage(content=text)]}, config)
        reply = result["messages"][-1].content
        if isinstance(reply, list):
            reply = "".join(b.get("text", "") for b in reply if isinstance(b, dict))
        send_text(phone, reply)
    except Exception:
        logger.exception("falha ao processar mensagem do WhatsApp")


@app.post("/webhooks/wa")
def wa_webhook(
    payload: WaWebhook,
    background: BackgroundTasks,
    x_wa_token: str = Header(default=""),
) -> dict:
    settings = get_settings()
    if not settings.wa_shared_token or not hmac.compare_digest(
        x_wa_token, settings.wa_shared_token.get_secret_value()
    ):
        raise HTTPException(status_code=401, detail="token inválido")

    if payload.event == "connection.update":
        logger.info("wa-service: conexão %s", payload.status)
        return {"ok": True}

    # Marco 1: responde só DMs de texto que não são nossas
    if (
        payload.event == "message.received"
        and payload.text
        and payload.from_
        and not payload.fromMe
        and not payload.isGroup
    ):
        if payload.messageId and payload.messageId in _seen_message_ids:
            return {"ok": True, "dedup": True}
        if payload.messageId:
            _seen_message_ids.append(payload.messageId)
        # 200 imediato; o LLM roda em background (webhook lento perde entrega)
        background.add_task(_agent_reply, payload.from_, payload.text)
    return {"ok": True}
