# Procurement Agent — arquitetura viva

## Proposta de valor

Agente de compras para construtoras que transforma uma requisição informal em uma concorrência estruturada, conduz follow-ups e negociação com fornecedores, compara custo total, prazo e risco e entrega uma recomendação para aprovação humana.

O produto não é apenas um disparador de mensagens. Ele deve executar trabalho operacional verificável e manter uma trilha de auditoria.

## Princípios arquiteturais

- Um orquestrador controla uma máquina de estados persistente.
- LLM interpreta linguagem, extrai informações e explica decisões.
- Código determinístico valida regras, calcula valores e compara propostas.
- Nenhuma compra é confirmada sem aprovação humana explícita.
- O texto original nunca é sobrescrito pela versão normalizada.
- Informações confidenciais, como orçamento e preço máximo, não são enviadas ao fornecedor.
- Toda ação do agente gera um evento auditável.

## Fluxo planejado

1. Cadastro da empresa, usuários, obras e fornecedores.
2. Recebimento da lista de compras.
3. Normalização dos itens e esclarecimento de ambiguidades.
4. Seleção de fornecedores elegíveis.
5. Criação dos lotes e envio de RFQs.
6. Acompanhamento, cobrança e complementação das respostas.
7. Equalização das propostas e cálculo de cenários.
8. Negociação limitada por política.
9. Recomendação e aprovação humana.
10. Geração do pedido de compra e notificação do financeiro.
11. Registro da entrega e atualização da performance do fornecedor.

## Máquina de estados da requisição

```text
DRAFT
→ NORMALIZING
→ CLARIFYING
→ READY
→ QUOTING
→ COMPARING
→ NEGOTIATING
→ AWAITING_APPROVAL
→ APPROVED
→ ORDERED
→ CLOSED
```

Estados terminais alternativos: `CANCELLED`.

## Etapa 0 — cadastro

O cadastro é dividido em entidades independentes porque possuem ciclos de vida diferentes.

### Empresa

Contém:

- razão social, nome fantasia e CNPJ;
- endereço fiscal e contatos;
- moeda e condições comerciais padrão;
- política de negociação;
- exigência de nota fiscal;
- regras de aprovação e divisão de pedidos.

### Usuário

Contém:

- identidade e contatos;
- empresa;
- papéis e permissões;
- limite de aprovação;
- obras acessíveis;
- preferências de notificação.

Papéis mínimos do MVP:

- `BUYER`: cria requisição e conduz cotação;
- `APPROVER`: aprova um cenário.

### Obra

Contém:

- nome, código e centro de custo;
- endereço de entrega;
- contato local;
- horários e restrições de recebimento;
- datas do projeto;
- regras de substituição e marcas preferenciais.

### Fornecedor

Níveis de cadastro:

- `DISCOVERED`: encontrado, ainda não verificado;
- `CONTACTABLE`: contato e área de atuação confirmados;
- `VERIFIED`: dados fiscais e comerciais homologados.

Indicadores como taxa de resposta, tempo médio e pontualidade devem ser calculados pelo sistema, não preenchidos manualmente.

## Etapa 1 — lista de compras

### Decisão de contrato

O payload é um objeto com metadados gerais e um array `items`. Não deve ser apenas um array solto porque obra, solicitante, prazo, orçamento e restrições pertencem à requisição como um todo.

### Campos mínimos por item

- `clientItemId`;
- `rawDescription`;
- `quantity`;
- `unit`.

Campos opcionais:

- `referenceUnitPrice`: preço histórico ou estimado;
- `maximumUnitPrice`: limite confidencial;
- `notes`.

### Persistência

A API recebe um array, mas cada item é salvo em uma linha própria.

```text
purchase_requests 1 ─── N purchase_request_items
```

O modelo híbrido usa:

- colunas relacionais para dados universais e frequentemente consultados;
- `jsonb` para especificações que variam por categoria;
- `jsonb` para avisos e campos críticos ausentes.

### Transação de criação

1. Validar usuário, empresa e obra.
2. Inserir `purchase_requests`.
3. Inserir todos os `purchase_request_items`.
4. Registrar `PURCHASE_REQUEST_CREATED`.
5. Confirmar a transação.
6. Disparar a normalização.

Se qualquer item falhar, toda a transação deve ser revertida.

### Segurança comercial

Estes campos nunca devem entrar no contrato enviado ao fornecedor:

- orçamento total;
- preço de referência;
- preço máximo;
- propostas concorrentes;
- identidade de concorrentes.

## Escopo do MVP do hackathon

Demonstrar:

1. uma requisição com três itens;
2. detecção de uma especificação ambígua;
3. seleção de três fornecedores pré-cadastrados;
4. envio ou simulação de RFQs;
5. interpretação de respostas em linguagem natural;
6. follow-up automático de uma resposta incompleta;
7. uma rodada de negociação;
8. três cenários de compra;
9. aprovação humana;
10. geração do pedido de compra.

Não priorizar no MVP:

- descoberta ampla de fornecedores na internet;
- ERP completo;
- pagamento;
- logística pós-compra completa;
- negociação sem limites;
- muitas categorias de material.

## Etapa 2 — normalizar e esclarecer

### Objetivo

Transformar cada descrição livre em um item estruturado, comparável e seguro para cotação sem inventar especificações. A etapa preserva o texto original, extrai fatos, valida unidades e identifica perguntas realmente necessárias.

### Pipeline

1. Carregar a requisição e os itens em `PENDING`.
2. Fazer pré-processamento determinístico de números, unidades e abreviações.
3. Usar extração estruturada do LLM conforme o schema da categoria.
4. Resolver a categoria e consultar o catálogo de especificações obrigatórias.
5. Validar a saída em código.
6. Calcular confiança por campo, nunca apenas uma confiança global opaca.
7. Classificar cada ausência como crítica, recomendada ou opcional.
8. Agrupar perguntas para o comprador em uma única rodada curta.
9. Aplicar as respostas como uma nova versão do item.
10. Marcar o item como `READY` somente após as validações.

### Regras

- O LLM não pode completar marca, dimensão, norma, classe, embalagem ou aplicação sem evidência.
- Valores inferidos precisam conter `source: INFERRED` e não podem ser usados quando o campo for crítico sem confirmação.
- Quantidade, unidade e conversões são validadas em código.
- Perguntas devem apresentar opções quando o catálogo permitir, sem forçar uma opção.
- Perguntas críticas bloqueiam o item; perguntas recomendadas geram alerta; opcionais não bloqueiam.
- Cada resposta do usuário cria uma nova versão, preservando a anterior.
- Uma requisição só passa para `READY` quando todos os itens ativos estiverem `READY`.

### Classificação de criticidade

- `BLOCKING`: sem resposta, fornecedores podem cotar produtos não equivalentes.
- `WARNING`: a informação melhora a cotação, mas uma regra empresarial permite prosseguir.
- `OPTIONAL`: informação útil que não altera a comparabilidade básica.

### Fontes de cada campo

- `USER_EXPLICIT`: declarado diretamente pelo usuário.
- `CATALOG_MATCH`: correspondência exata com catálogo interno.
- `COMPANY_DEFAULT`: regra padrão cadastrada pela empresa ou obra.
- `INFERRED`: inferência do modelo; requer confirmação quando crítica.
- `USER_CONFIRMED`: resposta posterior do comprador.

### Estados do item

```text
PENDING → PROCESSING → NEEDS_CLARIFICATION → PROCESSING → READY
                         └──────────────────────────────→ REJECTED
```

### Critério de saída

Um item está pronto quando:

- possui categoria;
- quantidade e unidade são válidas;
- todos os campos `BLOCKING` do schema da categoria estão preenchidos;
- não existem contradições abertas;
- conversões foram registradas;
- as inferências críticas foram confirmadas;
- a descrição normalizada foi gerada a partir dos campos validados.

### Estratégia escolhida para o MVP: classificação livre controlada

No MVP, o catálogo técnico completo pode ser substituído por uma classificação livre do LLM. Temperatura baixa melhora a repetibilidade, mas não garante consistência semântica. Por isso, a classificação livre deve operar sob estes controles:

- saída estruturada obrigatória;
- categoria canônica em inglês e rótulo em português;
- distinção entre fatos explícitos e inferências;
- proibição de preencher especificações sem evidência;
- criticidade justificada para cada campo ausente;
- confirmação humana quando categoria ou campo crítico tiver baixa confiança;
- validações universais de quantidade, unidade, preço e contradições em código;
- reutilização de classificações previamente confirmadas como memória do sistema;
- versionamento do prompt, modelo e resultado.

O LLM retorna uma proposta, não uma verdade definitiva. A categoria confirmada pode ser armazenada em `classification_memory`, criando progressivamente uma taxonomia própria sem exigir um catálogo inicial.

#### Limites recomendados

- categoria abaixo de `0.85`: pedir confirmação;
- campo crítico inferido: pedir confirmação independentemente da confiança;
- campo explícito abaixo de `0.80`: pedir confirmação;
- unidade incompatível ou contradição: bloquear;
- mais de três perguntas bloqueantes em um item: pedir ao usuário que reescreva ou detalhe o item.

Esses números são políticas iniciais do MVP, não probabilidades calibradas. Devem ser ajustados com casos reais.

#### Evolução pós-MVP

1. Classificação livre controlada.
2. Memória de classificações confirmadas.
3. Taxonomia emergente com aliases.
4. Schemas específicos para categorias frequentes ou críticas.
5. Catálogo externo apenas onde equivalência técnica ou compliance exigir.

### Exemplo

Entrada:

```text
100 sacos de cimento CP-II de 50 kg
```

O sistema extrai tipo e embalagem, mas não deve assumir a classe de resistência. Se o catálogo definir `strengthClass` como bloqueante, pergunta:

```text
Qual é a classe do cimento CP-II?
Opções: 25, 32, 40 ou “aceito qualquer classe”.
```

Depois da resposta `32`, a descrição normalizada pode se tornar:

```text
Cimento Portland CP-II, classe 32, saco de 50 kg
```

Os contratos de exemplo estão em:

- `contracts/normalization-result.example.json`;
- `contracts/clarification-request.example.json`;
- `contracts/clarification-response.example.json`.

## Etapa 3 — selecionar fornecedores

### Objetivo

Selecionar um conjunto pequeno de fornecedores elegíveis para cada grupo de itens, maximizando cobertura, competição e confiabilidade. O resultado é uma shortlist explicável; não é ainda uma decisão de compra.

### Pipeline

1. Carregar somente itens normalizados em `READY`.
2. Agrupar itens por afinidade de categoria e possibilidade de cotação conjunta.
3. Gerar candidatos por categoria, região de atendimento e canal de contato.
4. Aplicar filtros eliminatórios.
5. Calcular compatibilidade semântica de categoria quando necessário.
6. Calcular score determinístico dos candidatos elegíveis.
7. Aplicar regras de diversidade e cobertura.
8. Selecionar de três a cinco fornecedores por lote.
9. Persistir candidatos, motivos, score e versão da política.
10. Produzir a shortlist para a criação da RFQ.

### Filtros eliminatórios

Um fornecedor é inelegível quando:

- está `INACTIVE` ou `BLOCKED`;
- não atende a região da obra;
- não possui contato utilizável no canal escolhido;
- não atende nenhuma categoria do lote;
- não entrega dentro do prazo máximo conhecido;
- não satisfaz exigências obrigatórias de compliance;
- o pedido não atinge o mínimo comercial, quando não houver possibilidade de agrupamento;
- está explicitamente bloqueado pela empresa ou obra.

### Compatibilidade de categoria

Como o MVP usa classificação livre, a compatibilidade pode ser:

- `EXACT`: categoria canônica igual;
- `MEMORY_MATCH`: correspondência confirmada anteriormente;
- `SEMANTIC`: LLM ou embeddings indicam compatibilidade;
- `NONE`: incompatível.

Correspondência `SEMANTIC` abaixo do limiar da política não elimina nem aprova silenciosamente: ela exige revisão ou é usada apenas como fornecedor reserva.

### Ranking

Score inicial recomendado:

```text
30% cobertura dos itens do lote
20% taxa de entrega no prazo
15% taxa de resposta a cotações
10% qualidade/acurácia do pedido
10% velocidade de resposta
10% competitividade histórica de preço
 5% proximidade ou facilidade logística
```

Os pesos são versionados na política. Para fornecedores sem histórico, usar score neutro com penalidade de incerteza pequena, não zero. Isso permite entrada de novos fornecedores sem colocá-los automaticamente em primeiro lugar.

### Diversidade da shortlist

- selecionar de três a cinco fornecedores por lote;
- evitar shortlist dominada por um único grupo econômico;
- incluir no máximo um fornecedor de alto risco quando houver alternativas;
- incluir pelo menos dois fornecedores com alta cobertura;
- permitir um fornecedor novo como exploração controlada;
- registrar por que cada candidato foi incluído ou excluído.

### Critério de saída

Cada lote possui fornecedores suficientes para gerar competição ou uma exceção explícita `INSUFFICIENT_SUPPLIERS`. A seleção contém snapshot dos dados, score, fatores e versão da política.

Contratos:

- `contracts/supplier-selection-request.example.json`;
- `contracts/supplier-selection-result.example.json`.

## Etapa 4 — WhatsApp e conversas de RFQ

### Decisão de integração

Produção usa a WhatsApp Business Platform Cloud API oficial. O backend nunca expõe o token da Meta ao frontend. Um adaptador `MessagingProvider` isola a aplicação da API externa e permite trocar a Cloud API por um simulador na demo.

### Pré-requisitos de produção

- Meta business portfolio;
- WhatsApp Business Account (WABA);
- número empresarial registrado;
- permissão `whatsapp_business_messaging`;
- app inscrito nos webhooks do WABA;
- endpoint HTTPS público para webhook;
- consentimento registrável do contato;
- templates aprovados para mensagens iniciadas pela empresa.

### Regra de consentimento

O telefone cadastrado não implica consentimento. Antes do primeiro contato, o fornecedor precisa possuir registro de opt-in contendo finalidade, origem e momento. Opt-out, bloqueio ou ausência de consentimento impede novo envio automatizado.

### Pipeline de saída

1. Criar RFQ e conversa interna.
2. Validar consentimento, contato, template e política de envio.
3. Registrar mensagem como `QUEUED` com chave de idempotência.
4. Um worker envia para `/{PHONE_NUMBER_ID}/messages`.
5. Persistir o identificador retornado pela Meta (`wamid`).
6. Atualizar estados por webhooks: `SENT`, `DELIVERED`, `READ` ou `FAILED`.
7. Agendar lembrete apenas quando permitido pela política e pelo canal.

### Pipeline de entrada

1. Meta envia evento ao webhook.
2. Validar assinatura antes de processar.
3. Persistir o payload bruto e devolver `200` rapidamente.
4. Deduplicar pelo identificador da mensagem.
5. Resolver fornecedor pelo identificador/telefone do remetente.
6. Correlacionar com uma única conversa aberta.
7. Persistir a mensagem recebida.
8. Enfileirar parsing da cotação e avanço do orquestrador.

Se houver zero ou mais de uma conversa compatível, a mensagem vai para `NEEDS_HUMAN_ROUTING`; o sistema não deve adivinhar.

### Máquina de estados da mensagem

```text
QUEUED → SUBMITTED → SENT → DELIVERED → READ
   └──────────────→ FAILED
```

Estado da conversa:

```text
DRAFT → WAITING_SEND → WAITING_RESPONSE → RESPONSE_RECEIVED
                                  └────→ FOLLOW_UP_DUE
          RESPONSE_RECEIVED → PARSING → INCOMPLETE_RESPONSE | VALID_QUOTE
```

### Templates

A primeira mensagem iniciada pela empresa usa template aprovado e variáveis limitadas. Informações detalhadas podem ser apresentadas de forma compacta ou em documento/link seguro conforme a política aprovada. Mensagens livres e follow-ups dependem da janela de atendimento vigente; o adaptador decide entre `TEMPLATE`, `SESSION_TEXT` ou bloqueio.

### Segurança e confiabilidade

- token e app secret ficam em secret manager;
- verificar assinatura do webhook;
- responder `200` antes de processamento pesado;
- deduplicar webhooks e mensagens;
- usar retry com backoff para erros transitórios;
- não repetir automaticamente erros permanentes;
- mascarar telefone e conteúdo sensível nos logs;
- persistir payload bruto com retenção controlada;
- mensagens externas são conteúdo não confiável e nunca executam ações diretamente;
- confirmação de compra continua exigindo gate humano.

### Estratégia para a demo

O mesmo contrato `MessagingProvider` possui dois adapters:

- `MetaWhatsAppProvider`: integração real;
- `DemoMessagingProvider`: simula envio, entrega e respostas.

Se credenciais, templates ou webhook público não estiverem prontos, a demo usa o simulador com payloads idênticos aos reais. Isso preserva a arquitetura e evita depender da aprovação da Meta durante o hackathon.

Contratos:

- `contracts/whatsapp-template-send.example.json`;
- `contracts/whatsapp-inbound-webhook.example.json`;
- `contracts/conversation-message.example.json`.
