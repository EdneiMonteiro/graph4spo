"""
chat.py

Chat multi-turn com memória de conversa + RAG sobre Machado de Assis.

Diferencial em relação ao search_query.py:
  - Mantém histórico completo de mensagens (human/assistant)
  - Cada resposta considera o contexto das perguntas anteriores
  - Streaming: resposta aparece palavra por palavra
  - Busca RAG é feita na pergunta atual + reescrita contextual quando necessário

Uso:
  python src/chat.py
"""

import logging
import os
import sys

from dotenv import load_dotenv
from langchain_community.vectorstores import AzureSearch
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

sys.path.insert(0, os.path.dirname(__file__))
from keyvault_client import build_kv_client

logging.basicConfig(level=logging.WARNING)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
TOP_K = 10          # chunks recuperados por turno
MAX_HISTORY = 10    # turnos de histórico mantidos (human+assistant = 1 turno)

SYSTEM_PROMPT = """\
Você é um especialista em literatura brasileira, especializado na obra de Machado de Assis.

REGRAS OBRIGATÓRIAS:
1. Responda APENAS com base nos trechos fornecidos a cada mensagem.
2. Se um personagem, fato ou evento NÃO aparecer nos trechos, diga claramente:
   "[Nome] não aparece nos trechos indexados." NÃO invente nem complemente com conhecimento geral.
3. Você pode usar conhecimento geral SOMENTE para explicar algo que JÁ aparece nos trechos. Sinalize com "(conhecimento geral)".
4. Informe ao usuário quando a limitação se deve ao escopo indexado (ex: apenas caps. 1-6 de Dom Casmurro).
5. Mantenha o contexto da conversa para perguntas de follow-up ("e ele?", "o que acontece depois?").
6. Cite sempre a fonte no formato (fonte: nome_do_arquivo). Responda sempre em português.
"""

CONTEXT_TEMPLATE = """\
ESCOPO INDEXADO: {scope}

Trechos recuperados para esta pergunta:

{context}

---
Responda à pergunta levando em conta o histórico da conversa e os trechos acima.
Pergunta atual: {question}"""


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

_OBRA_FILTROS = {
    "dom casmurro": ["machado_01_", "machado_02_"],
    "memórias póstumas": ["machado_03_", "machado_04_"],
    "memorias postumas": ["machado_03_", "machado_04_"],
    "quincas borba": ["machado_05_"],
    "alienista": ["machado_06_"],
    "cartomante": ["machado_07_"],
    "missa do galo": ["machado_08_"],
    "espelho": ["machado_09_"],
    "causa secreta": ["machado_10_"],
}


_ESCOPO_OBRAS = {
    "machado_01_": "Dom Casmurro cap.1-3",
    "machado_02_": "Dom Casmurro cap.4-6",
    "machado_03_": "Memórias Póstumas de Brás Cubas cap.1-3",
    "machado_04_": "Memórias Póstumas de Brás Cubas cap.4-6",
    "machado_05_": "Quincas Borba (trecho)",
    "machado_06_": "O Alienista (trecho)",
    "machado_07_": "A Cartomante (trecho)",
    "machado_08_": "Missa do Galo (trecho)",
    "machado_09_": "O Espelho (trecho)",
    "machado_10_": "A Causa Secreta (trecho)",
}


def scope_from_docs(docs: list) -> str:
    prefixes = {src[:12] for d in docs if (src := d.metadata.get("source", ""))}
    labels = [_ESCOPO_OBRAS.get(p, p) for p in sorted(prefixes)]
    return ", ".join(labels) if labels else "trechos variados"


def docs_da_obra(question: str, vector_store, k: int = 6, filters: str | None = None) -> list[Document]:
    """Busca chunks focados na obra mencionada na pergunta."""
    q_lower = question.lower()
    for keyword, prefixes in _OBRA_FILTROS.items():
        if keyword in q_lower:
            results = []
            for prefix in prefixes:
                hits = vector_store.similarity_search(question, k=k, filters=filters)
                results += [d for d in hits if d.metadata.get("source", "").startswith(prefix)]
                more = vector_store.hybrid_search(prefix.replace("_", " "), k=k, filters=filters)
                results += [d for d in more if d.metadata.get("source", "").startswith(prefix)]
            seen, deduped = set(), []
            for d in results:
                key = (d.metadata.get("source"), d.page_content[:80])
                if key not in seen:
                    seen.add(key)
                    deduped.append(d)
            if deduped:
                return deduped
    return []


def format_docs(docs: list[Document]) -> str:
    parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "desconhecido")
        parts.append(f"[Trecho {i}] (fonte: {source})\n{doc.page_content}")
    return ("\n" + "─" * 50 + "\n").join(parts)


def get_sources(docs: list[Document]) -> str:
    sources = sorted({doc.metadata.get("source", "?") for doc in docs})
    return ", ".join(sources)


def trim_history(history: list, max_turns: int) -> list:
    """Mantém apenas os últimos max_turns turnos (preservando o SystemMessage inicial)."""
    system = [m for m in history if isinstance(m, SystemMessage)]
    turns  = [m for m in history if not isinstance(m, SystemMessage)]
    # Cada turno = 1 HumanMessage + 1 AIMessage = 2 itens
    max_msgs = max_turns * 2
    return system + turns[-max_msgs:]


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    index_name   = os.environ["AZURE_SEARCH_INDEX_NAME"]
    oai_api_ver  = os.environ["AZURE_OPENAI_API_VERSION"]
    chat_deploy  = os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"]
    embed_deploy = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"]
    kv_cert_name = os.environ["KV_CERT_NAME"]

    print("Conectando ao Azure Key Vault...")
    kv = build_kv_client()

    oai_endpoint  = kv.get_secret("openai-endpoint")
    oai_key       = kv.get_secret("openai-api-key")
    srch_endpoint = kv.get_secret("search-endpoint")
    srch_key      = kv.get_secret("search-api-key")
    _ = kv.get_sp_cert_bytes(kv_cert_name)  # valida configuração

    print("Inicializando modelos...")

    embeddings = AzureOpenAIEmbeddings(
        azure_endpoint=oai_endpoint,
        api_key=oai_key,
        azure_deployment=embed_deploy,
        openai_api_version=oai_api_ver,
    )

    llm = AzureChatOpenAI(
        azure_endpoint=oai_endpoint,
        api_key=oai_key,
        azure_deployment=chat_deploy,
        openai_api_version=oai_api_ver,
        temperature=0.3,
        max_tokens=1800,
        streaming=True,
    )

    vector_store = AzureSearch(
        azure_search_endpoint=srch_endpoint,
        azure_search_key=srch_key,
        index_name=index_name,
        embedding_function=embeddings,
        search_type="hybrid",
    )

    retriever = vector_store.as_retriever(k=TOP_K)

    # Histórico de mensagens (inclui SystemMessage inicial)
    history: list = [SystemMessage(content=SYSTEM_PROMPT)]
    _ultima_obra_prefixes: set[str] = set()  # prefixos da última obra mencionada
    _ultima_obra_keyword: str = ""            # keyword usada para buscar (ex: "cartomante")

    # ---------------------------------------------------------------------------
    # UI
    # ---------------------------------------------------------------------------
    clear_screen()
    print("╔" + "═" * 63 + "╗")
    print("║   Chat — Machado de Assis · RAG + GPT-4o Mini" + " " * 17 + "║")
    print("║   Azure AI Search (busca híbrida) · histórico de conversa" + " " * 4 + "║")
    print("╚" + "═" * 63 + "╝")
    print()
    print("  Digite sua pergunta e pressione Enter.")
    print("  Comandos: 'sair' para encerrar · 'limpar' para nova conversa")
    print()

    obras = [
        "Dom Casmurro (cap. 1–6)",
        "Memórias Póstumas de Brás Cubas (cap. 1–6)",
        "Quincas Borba",
        "O Alienista",
        "A Cartomante",
        "Missa do Galo",
        "O Espelho",
        "A Causa Secreta",
    ]
    print("  Obras indexadas:")
    for obra in obras:
        print(f"    · {obra}")
    print()
    print("─" * 65)

    # Filtro por departamento
    print()
    print("  Filtro por departamento (deixe em branco para ver todos):")
    print("  Opções: CSU | STU | ATU")
    depto_input = input("  Depto: ").strip().upper()
    _valid_deptos = {"CSU", "STU", "ATU"}
    if depto_input in _valid_deptos:
        depto_filter_str: str | None = f"depto eq '{depto_input}'"
        print(f"  → Filtrando por departamento: {depto_input}")
    else:
        depto_filter_str = None
        if depto_input:
            print("  → Valor inválido, sem filtro de departamento.")
    print("─" * 65)

    turn = 0

    while True:
        # Prompt de entrada
        try:
            print()
            user_input = input("  Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  Até logo!\n")
            break

        if not user_input:
            continue

        cmd = user_input.lower()

        if cmd in ("sair", "exit", "quit", "q"):
            print("\n  Até logo!\n")
            break

        if cmd == "limpar":
            history = [SystemMessage(content=SYSTEM_PROMPT)]
            _ultima_obra_prefixes.clear()
            _ultima_obra_keyword = ""
            turn = 0
            clear_screen()
            print("  Conversa reiniciada.\n" + "─" * 65)
            continue

        if cmd.startswith("depto"):
            parts = user_input.split(None, 1)
            new_depto = parts[1].upper() if len(parts) > 1 else ""
            if new_depto in {"CSU", "STU", "ATU"}:
                depto_filter_str = f"depto eq '{new_depto}'"
                print(f"\n  Filtro atualizado para: {new_depto}")
            else:
                depto_filter_str = None
                print("\n  Filtro de departamento removido.")
            print("─" * 65)
            continue

        turn += 1

        # --- Recupera chunks (focado na obra mencionada ou follow-up da última) ---
        obra_docs = docs_da_obra(user_input, vector_store, filters=depto_filter_str)
        if obra_docs:
            # Nova obra detectada: atualiza memória
            _ultima_obra_prefixes.clear()
            _ultima_obra_prefixes.update(d.metadata.get("source", "")[:12] for d in obra_docs)
            # Guarda o keyword que ativou a detecção (para uso em follow-ups)
            q_lower = user_input.lower()
            _ultima_obra_keyword = next(
                (kw for kw in _OBRA_FILTROS if kw in q_lower), user_input
            )
            docs = obra_docs
        elif _ultima_obra_prefixes:
            # Follow-up: primeiro semântica com a pergunta real, depois keyword fallback
            hits = vector_store.similarity_search(user_input, k=TOP_K, filters=depto_filter_str)
            filtered = [d for d in hits if d.metadata.get("source", "")[:12] in _ultima_obra_prefixes]
            if not filtered:
                hits = vector_store.hybrid_search(_ultima_obra_keyword, k=TOP_K, filters=depto_filter_str)
                filtered = [d for d in hits if d.metadata.get("source", "")[:12] in _ultima_obra_prefixes]
            docs = filtered if filtered else vector_store.similarity_search(user_input, k=TOP_K, filters=depto_filter_str)
        else:
            docs = vector_store.similarity_search(user_input, k=TOP_K, filters=depto_filter_str)

        context = format_docs(docs)
        sources = get_sources(docs)
        scope = scope_from_docs(docs)

        # Monta mensagem humana com contexto RAG + escopo embutido
        user_msg_content = CONTEXT_TEMPLATE.format(
            context=context,
            scope=scope,
            question=user_input,
        )
        history.append(HumanMessage(content=user_msg_content))

        # Garante que o histórico não cresça indefinidamente
        history = trim_history(history, MAX_HISTORY)

        # --- Resposta com streaming ---
        print(f"\n  Machado·AI: ", end="", flush=True)

        full_response = ""
        try:
            for chunk in llm.stream(history):
                token = chunk.content
                print(token, end="", flush=True)
                full_response += token
        except Exception as exc:
            print(f"\n  [Erro ao gerar resposta: {exc}]")
            history.pop()  # remove a mensagem que causou o erro
            continue

        # Adiciona resposta ao histórico (sem o contexto RAG — só a pergunta limpa)
        # Substitui a última HumanMessage pela versão enxuta (sem os trechos)
        history[-1] = HumanMessage(content=user_input)
        history.append(AIMessage(content=full_response))

        # Exibe fontes
        print(f"\n\n  ╰─ fontes: {sources}")
        print("─" * 65)


if __name__ == "__main__":
    main()
