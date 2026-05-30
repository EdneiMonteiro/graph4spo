"""
search_query.py

RAG interativo: busca híbrida no Azure AI Search + resposta com GPT-4o Mini.

Fluxo por pergunta:
  1. Gera embedding da pergunta (text-embedding-3-small)
  2. Busca híbrida (keyword + vector) no Azure AI Search → top-5 chunks
  3. Monta contexto com chunks recuperados e suas fontes
  4. Envia para GPT-4o Mini com prompt de RAG em português
  5. Exibe resposta + fontes

Pré-requisitos:
  - index_documents.py executado com sucesso
  - az login executado na sessão atual
"""

import logging
import os
import sys

from dotenv import load_dotenv
from langchain_community.vectorstores import AzureSearch
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

sys.path.insert(0, os.path.dirname(__file__))
from keyvault_client import build_kv_client

logging.basicConfig(level=logging.WARNING)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
TOP_K = 10  # número de chunks recuperados por pergunta

RAG_SYSTEM_PROMPT = """Você é um especialista em literatura brasileira do século XIX.

REGRAS OBRIGATÓRIAS:
1. Responda APENAS com base nos trechos fornecidos no contexto.
2. Se um personagem, fato ou evento NÃO aparecer nos trechos, diga claramente:
   "[Nome] não aparece nos trechos indexados." — e pare por aí. NÃO invente nem complemente com conhecimento geral.
3. Você pode usar conhecimento geral SOMENTE para explicar ou contextualizar algo que JÁ aparece nos trechos — nunca para introduzir informações novas. Sinalize com "(conhecimento geral)".
4. Informe ao usuário quando a limitação se deve ao escopo indexado (ex: apenas caps. 1-6).
5. Cite sempre a fonte no formato (fonte: nome_do_arquivo)."""

RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", RAG_SYSTEM_PROMPT),
    ("human", "ESCOPO INDEXADO: {scope}\n\nTrechos recuperados:\n\n{context}\n\n---\n\nPergunta: {question}"),
])

# Escopo de cada obra (capítulos disponíveis no índice)
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


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def format_docs(docs: list[Document]) -> str:
    """Formata os chunks recuperados como contexto legível para o LLM."""
    parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "desconhecido")
        parts.append(f"[Trecho {i}] (fonte: {source})\n{doc.page_content}")
    return "\n\n" + ("─" * 60) + "\n\n".join(parts)


# Mapa de palavras-chave → arquivos de cada obra
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


def docs_da_obra(question: str, vector_store, k: int = 6, filters: str | None = None) -> list[Document]:
    """Se a pergunta menciona uma obra específica, busca chunks apenas dela."""
    q_lower = question.lower()
    for keyword, prefixes in _OBRA_FILTROS.items():
        if keyword in q_lower:
            results = []
            for prefix in prefixes:
                # Busca semântica geral e filtra por prefixo do arquivo
                hits = vector_store.similarity_search(question, k=k, filters=filters)
                results += [d for d in hits if d.metadata.get("source", "").startswith(prefix)]
                # Complementa com busca direta por texto do arquivo via hybrid
                more = vector_store.hybrid_search(prefix.replace("_", " "), k=k, filters=filters)
                results += [d for d in more if d.metadata.get("source", "").startswith(prefix)]
            # Remove duplicatas preservando ordem
            seen, deduped = set(), []
            for d in results:
                key = (d.metadata.get("source"), d.page_content[:80])
                if key not in seen:
                    seen.add(key)
                    deduped.append(d)
            if deduped:
                return deduped
    return []


def print_sources(docs: list[Document]) -> None:
    """Exibe as fontes dos chunks utilizados na resposta."""
    sources = sorted({doc.metadata.get("source", "?") for doc in docs})
    print(f"\n  Fontes consultadas: {', '.join(sources)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    tenant_id    = os.environ["AZURE_TENANT_ID"]
    client_id    = os.environ["AZURE_CLIENT_ID"]
    kv_cert_name = os.environ["KV_CERT_NAME"]
    index_name   = os.environ["AZURE_SEARCH_INDEX_NAME"]
    oai_api_ver  = os.environ["AZURE_OPENAI_API_VERSION"]
    chat_deploy  = os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"]
    embed_deploy = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"]

    print("Conectando ao Azure Key Vault...")
    kv = build_kv_client()

    oai_endpoint  = kv.get_secret("openai-endpoint")
    oai_key       = kv.get_secret("openai-api-key")
    srch_endpoint = kv.get_secret("search-endpoint")
    srch_key      = kv.get_secret("search-api-key")

    # O certificado do SP é recuperado aqui para validar a configuração,
    # mas a autenticação do Graph API não é necessária durante as queries —
    # apenas as API keys do OpenAI e Search (armazenadas no KV) são usadas.
    _ = kv.get_sp_cert_bytes(kv_cert_name)

    print("Inicializando modelos e índice de busca...")

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
        temperature=0.2,
        max_tokens=1500,
    )

    vector_store = AzureSearch(
        azure_search_endpoint=srch_endpoint,
        azure_search_key=srch_key,
        index_name=index_name,
        embedding_function=embeddings,
        search_type="hybrid",
    )

    retriever = vector_store.as_retriever(k=TOP_K)

    # Chain sem retriever embutido — recebe context, scope e question já prontos
    answer_chain = RAG_PROMPT | llm | StrOutputParser()

    _ultima_obra_prefixes: set[str] = set()
    _ultima_obra_keyword: str = ""

    # ---------------------------------------------------------------------------
    # Loop interativo
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 65)
    print("  RAG Demo — Machado de Assis + Azure AI Search")
    print(f"  LLM: {chat_deploy} | Embedding: {embed_deploy}")
    print(f"  Índice: {index_name} | Busca: Híbrida (keyword + vector)")
    print("=" * 65)
    print("Faça perguntas sobre as obras indexadas. Digite 'sair' para encerrar.\n")
    print("Exemplos de perguntas:")
    print("  - Quem é Capitu e como seus olhos são descritos?")
    print("  - Qual é a filosofia do Humanitismo de Quincas Borba?")
    print("  - O que acontece no final de A Cartomante?")
    print("  - Como o doutor Bacamarte é descrito em O Alienista?")
    print("  - O que Jacobina descobre sobre a alma no conto O Espelho?\n")

    # Filtro por departamento
    print("Filtro por departamento (deixe em branco para ver todos):")
    print("  Opções: CSU | STU | ATU")
    depto_input = input("  Depto: ").strip().upper()
    _valid_deptos = {"CSU", "STU", "ATU"}
    if depto_input in _valid_deptos:
        depto_filter_str: str | None = f"depto eq '{depto_input}'"
        print(f"  → Filtrando por departamento: {depto_input}\n")
    else:
        depto_filter_str = None
        if depto_input:
            print("  → Valor inválido, sem filtro de departamento.\n")
        else:
            print("  → Sem filtro de departamento.\n")

    while True:
        try:
            question = input("Pergunta: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrando.")
            break

        if question.lower() in ("sair", "exit", "quit", "q"):
            print("Encerrando.")
            break

        if not question:
            continue

        if question.lower().startswith("depto"):
            parts = question.split(None, 1)
            new_depto = parts[1].upper() if len(parts) > 1 else ""
            if new_depto in {"CSU", "STU", "ATU"}:
                depto_filter_str = f"depto eq '{new_depto}'"
                print(f"  Filtro atualizado para: {new_depto}\n")
            else:
                depto_filter_str = None
                print("  Filtro de departamento removido.\n")
            continue

        print("\n⏳ Buscando e gerando resposta...")

        # Detecta obra na pergunta atual; se não achar, re-usa a última obra (follow-up)
        obra_docs = docs_da_obra(question, vector_store, filters=depto_filter_str)
        if obra_docs:
            _ultima_obra_prefixes.clear()
            _ultima_obra_prefixes.update(d.metadata.get("source", "")[:12] for d in obra_docs)
            _ultima_obra_keyword = next(
                (kw for kw in _OBRA_FILTROS if kw in question.lower()), question
            )
            retrieved_docs = obra_docs
        elif _ultima_obra_prefixes:
            # 1º: busca semântica com a pergunta real, filtrada pela obra
            hits = vector_store.similarity_search(question, k=TOP_K, filters=depto_filter_str)
            filtered = [d for d in hits if d.metadata.get("source", "")[:12] in _ultima_obra_prefixes]
            if not filtered:
                # 2º fallback: query pelo nome da obra (perguntas vagas tipo "Resuma")
                hits = vector_store.hybrid_search(_ultima_obra_keyword, k=TOP_K, filters=depto_filter_str)
                filtered = [d for d in hits if d.metadata.get("source", "")[:12] in _ultima_obra_prefixes]
            retrieved_docs = filtered if filtered else vector_store.similarity_search(question, k=TOP_K, filters=depto_filter_str)
        else:
            retrieved_docs = vector_store.similarity_search(question, k=TOP_K, filters=depto_filter_str)

        scope = scope_from_docs(retrieved_docs)
        answer = answer_chain.invoke({
            "context":  format_docs(retrieved_docs),
            "scope":    scope,
            "question": question,
        })

        print(f"\n{'─' * 65}")
        print(f"Resposta:\n\n{answer}")
        print_sources(retrieved_docs)
        print(f"{'─' * 65}\n")


if __name__ == "__main__":
    main()
