# graph4spo — RAG sobre SharePoint Online

## Visão Geral

Este repositório contém código de exemplo / prova de conceito (PoC) que demonstra
um pipeline de **RAG (Retrieval-Augmented Generation)** sobre documentos do
**SharePoint Online**, usando **Microsoft Graph** para ingestão, **Azure AI Search**
para indexação híbrida (vetorial + textual) e **Azure OpenAI** para embeddings e
geração, orquestrado com **LangChain** e segredos no **Azure Key Vault**.

Os documentos de exemplo (`docs/`) são textos de domínio público de Machado de Assis.

Este projeto foi criado para fins de aprendizado, avaliação e experimentação.

## Aviso Importante

Este repositório contém **código de exemplo e não é destinado para uso em produção**.

Antes de utilizar qualquer parte deste projeto em um ambiente produtivo ou crítico,
é essencial revisar, validar, proteger e adaptar o código conforme os requisitos da
sua organização, incluindo:

- Segurança
- Escalabilidade
- Confiabilidade
- Monitoramento
- Observabilidade
- Custos
- Conformidade

Leia também:

- [DISCLAIMER.md](./DISCLAIMER.md)
- [SUPPORT.md](./SUPPORT.md)

## O que este exemplo demonstra

- Autenticação a Microsoft Graph via **Service Principal** com `CertificateCredential`
- Ingestão de arquivos de uma biblioteca do SharePoint Online
- Chunking com `RecursiveCharacterTextSplitter` (800 tokens, overlap 100)
- Embeddings com **Azure OpenAI** (`text-embedding-3-small`, 1536 dims)
- Índice híbrido no **Azure AI Search** via LangChain
- Consulta simples (`search_query.py`) e **chat multi-turn** com memória (`chat.py`)
- Segredos e certificado armazenados no **Azure Key Vault**

## Pré-requisitos

- Python 3.10+
- Azure CLI autenticado (`az login`)
- Recursos Azure: OpenAI, AI Search, Key Vault
- Service Principal com permissão de leitura no SharePoint (Sites/Files via Graph)

## Como iniciar

1. Provisione a infraestrutura (scripts em `infra/`):
   ```powershell
   ./infra/01_azure_resources.ps1
   ./infra/02_create_cert.ps1
   ./infra/03_register_sp.ps1
   ./infra/04_grant_sharepoint.ps1
   ```
2. Instale as dependências:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env
   ```
3. Indexe os documentos do SharePoint:
   ```bash
   python src/index_documents.py
   ```
4. Consulte o índice:
   ```bash
   python src/search_query.py      # consulta única
   python src/chat.py              # chat multi-turn com memória
   ```

## Estrutura

```
src/
  index_documents.py   # pipeline SharePoint -> AI Search
  search_query.py      # consulta RAG simples
  chat.py              # chat multi-turn com memória
  indexer_api.py       # API de indexação
  keyvault_client.py   # acesso a segredos/certificados
infra/                 # scripts de provisionamento (PowerShell)
docs/                  # documentos de exemplo (domínio público)
slides/                # material de apresentação
```

## Suporte

Este projeto **não possui SLA nem suporte oficial**.

Veja [SUPPORT.md](./SUPPORT.md) para detalhes.

## Aviso Legal

O uso deste projeto está sujeito aos termos descritos em [DISCLAIMER.md](./DISCLAIMER.md).

## Contribuições

Contribuições podem ser aceitas a critério do mantenedor.

## Licença

Distribuído sob a licença [MIT](LICENSE).

## Marcas Registradas (Trademarks)

Os nomes e serviços da Microsoft são utilizados apenas para fins descritivos.

Este projeto **não é afiliado, endossado ou suportado oficialmente pela Microsoft**.

O uso de marcas da Microsoft não deve sugerir qualquer tipo de parceria ou suporte oficial.
