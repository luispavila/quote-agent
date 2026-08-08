# Procurement Agent — documentação

Esta pasta contém a documentação viva do projeto Hack2L.

## Arquivos

- `architecture.md`: visão do produto, fluxo e decisões arquiteturais.
- `contracts/onboarding.example.json`: contrato consolidado do cadastro inicial.
- `contracts/purchase-request.example.json`: contrato de entrada da lista de compras.
- `contracts/purchase-request-response.example.json`: resposta da criação da requisição.
- `database/schema.sql`: schema inicial para PostgreSQL/Supabase.
- A implementação executável está em `app/`; a interface web fica em `app/static/`.

## Regra de manutenção

Cada nova etapa definida deve atualizar:

1. o fluxo em `architecture.md`;
2. o contrato JSON correspondente em `contracts/`;
3. o schema, quando houver mudança de persistência.
