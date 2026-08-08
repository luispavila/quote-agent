# quote-agent

Fundação genérica de um agente de IA conversacional em produção: **FastAPI + LangGraph +
Claude (Anthropic) + Langfuse**, deploy contínuo no **Render** (deploy-on-push via Dockerfile).

> Este repositório é a fundação open source usada como ponto de partida (declarado) no
> **Hack2L — AI Agents Hackathon (08/08/2026)**. A lógica de negócio da demo nasce no dia do evento.

## Arquitetura (Marco 0)

```
cliente ──HTTP──> Caddy (80/443) ──> FastAPI /chat ──> LangGraph (1 nó) ──> Claude Sonnet
                                                          │                    └─ fallback Featherless (opcional)
                                                          └──> Langfuse cloud (trace por sessão; no-op sem chaves)
Postgres 16 no compose (checkpointer do agente nos próximos marcos)
```

## Rodar local (3 comandos)

```bash
cp .env.example .env          # preencha ANTHROPIC_API_KEY (e LANGFUSE_* se quiser traces)
docker compose up --build -d
curl -s localhost/health && curl -s localhost/chat -X POST -H 'content-type: application/json' -d '{"message":"quanto custa um saco de cimento?"}'
```

Testes: `uv run pytest` (ou `docker run --rm -v $PWD:/w -w /w ghcr.io/astral-sh/uv:python3.13-bookworm uv run pytest`).

## Deploy em produção (Render, ~15 min)

1. Crie a conta em [render.com](https://render.com) (login com GitHub) e autorize o repo.
2. **New + → Blueprint** → selecione `quote-agent` — o [render.yaml](render.yaml) define tudo
   (Docker, região Virginia, plano Starter, health check em `/health`).
3. Preencha as env vars marcadas no painel: `ANTHROPIC_API_KEY` (obrigatória),
   `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` (traces) e `FEATHERLESS_API_KEY` (opcional).
4. Deploy. A partir daí, **todo `git push` na `main` redeploya sozinho** — sem Action, sem secrets no GitHub.

Prova de produção:

```bash
curl -s https://<app>.onrender.com/health
curl -s https://<app>.onrender.com/chat -X POST -H 'content-type: application/json' -d '{"message":"oi"}'
```

→ resposta do Claude + trace visível no projeto do [Langfuse](https://cloud.langfuse.com).

> Plano **Starter** de propósito: o free tier hiberna com inatividade e o cold start (~1 min)
> mataria a demo ao vivo.

### Plano B: AWS EC2 (scriptado, não usado por padrão)

`infra/aws/` guarda o caminho completo para EC2 t3.small + docker compose: `aws-bootstrap.sh`
(ECR, OIDC role, EC2, Elastic IP) e `github-deploy.yml` (mover para `.github/workflows/` para
ativar o deploy OIDC → ECR → SSM). Útil se quisermos citar a infra da patrocinadora no pitch.

## Endpoints

| Método | Rota      | Descrição |
|--------|-----------|-----------|
| GET    | `/health` | Status, modelo configurado, flags de LLM/Langfuse |
| POST   | `/chat`   | `{message, session_id?}` → resposta do agente (memória por `session_id`) |

## Variáveis de ambiente

Ver [.env.example](.env.example). Segredos nunca vão para o git; em produção vivem em
`/opt/quote-agent/.env` na EC2. Sem `LANGFUSE_*` a app roda sem tracing (no-op silencioso).

## Checklist do dia do evento

- [ ] Tornar este repo **público** antes de começar (`gh repo edit --visibility public --accept-visibility-change-consequences`) e declará-lo como ponto de partida.
- [ ] Chaves no lugar: `ANTHROPIC_API_KEY`, `LANGFUSE_*`, perk Featherless (painel do Render).
- [ ] `curl https://<app>.onrender.com/health` verde no telão. 😄
- [ ] Commits da lógica de negócio começam no repositório do evento a partir da manhã.

## Roadmap (pós-Marco 0)

1. **wa-service** — serviço WhatsApp próprio (Baileys, TypeScript estrito, instância única).
2. **Agente de cotação** — tools `parse_pedido`/`cotar`/`consolidar`/`aplicar_markup`/…,
   checkpointer Postgres, aprovação via `interrupt()`.
3. **Dashboard** — tabela comparativa e feed de eventos.
4. **Integração real** — pareamento do número, webhook público, E2E.
