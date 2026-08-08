# Nexo Compras — Procurement Agent

Aplicação full-stack de procurement para construção civil. Recebe listas de materiais,
normaliza especificações, pausa para esclarecimento humano e seleciona fornecedores por
categoria, região, risco e histórico.

Stack: FastAPI, SQLAlchemy, PostgreSQL, LangGraph, Featherless e frontend web responsivo.
A integração de WhatsApp permanece fora do escopo.

## Fluxo

```text
Cadastro → lista de compras → normalização com Featherless
→ esclarecimento humano → retomada do LangGraph
→ seleção auditável de fornecedores
```

O `thread_id` do LangGraph é o ID da solicitação. O nó de esclarecimento usa `interrupt()`
e a API retoma o mesmo fluxo com `Command(resume=answers)`. Sem uma chave Featherless ou
em caso de indisponibilidade da API, a demo usa o normalizador determinístico de segurança.

## Rodar localmente no Windows

Crie uma chave em `https://featherless.ai/account/api-keys`. Em seguida:

```powershell
Set-Location "C:\Users\Amanda\Documents\ChatGPT\HACK2L v2"
Copy-Item .env.example .env
notepad .env
```

Preencha somente esta linha no `.env`:

```text
FEATHERLESS_API_KEY=sua_chave_aqui
```

Instale as dependências, caso ainda não tenha feito, e inicie a aplicação:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --reload-dir app
```

Se a `.venv` já existe, execute apenas o último comando. Abra `http://127.0.0.1:8000/`.
O uso é local, mas as inferências são remotas: o backend chama a API Featherless pela
internet. Não é necessário instalar Ollama nem baixar modelos.

Verifique a configuração:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

O resultado deve conter `llm_provider: featherless` e `llm_configured: true`.

## Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Abra `http://localhost/`. O compose executa FastAPI, PostgreSQL e Caddy; a inferência
continua sendo feita pela Featherless.

## Deploy

O `render.yaml` já está configurado para Featherless. No painel do Render, cadastre
`FEATHERLESS_API_KEY` como secret e `DATABASE_URL` com a conexão PostgreSQL. O mesmo modelo
e os mesmos contratos são usados localmente e online.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | Configuração e status da aplicação |
| GET | `/` | Interface web |
| POST | `/api/demo/seed` | Cria os dados-base da demo |
| GET | `/api/bootstrap` | Empresa, obra e usuário disponíveis |
| GET | `/api/dashboard` | Métricas operacionais |
| GET/POST | `/api/purchase-requests` | Lista ou cria solicitações |
| GET | `/api/purchase-requests/{id}` | Detalhe e trilha de auditoria |
| POST | `/api/purchase-requests/{id}/process` | Inicia o LangGraph |
| POST | `/api/purchase-requests/{id}/clarifications` | Retoma o LangGraph |

Os contratos e decisões de produto ficam em [`docs/`](docs/README.md).
