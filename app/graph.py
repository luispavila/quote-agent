"""Grafo LangGraph mínimo: 1 nó que chama o LLM, com memória por thread.

Walking skeleton do Marco 0 — o agente de cotação real substitui este grafo
no dia do evento (tools de cotação, checkpointer Postgres, interrupt de aprovação).
"""

from functools import lru_cache

from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from app.llm import build_llm

SYSTEM_PROMPT = (
    "Você é um assistente de cotação de materiais de construção. "
    "Responda em português, de forma curta e objetiva."
)


def _agent_node(state: MessagesState) -> dict:
    llm = build_llm()
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = llm.invoke(messages)
    return {"messages": [response]}


@lru_cache
def build_graph():
    graph = StateGraph(MessagesState)
    graph.add_node("agent", _agent_node)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph.compile(checkpointer=MemorySaver())
