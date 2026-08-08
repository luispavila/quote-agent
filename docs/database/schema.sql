create extension if not exists pgcrypto;

create table companies (
    id uuid primary key default gen_random_uuid(),
    legal_name text not null,
    trade_name text not null,
    tax_id_country char(2) not null default 'BR',
    tax_id_type text not null default 'CNPJ',
    tax_id_value text not null unique,
    status text not null default 'ACTIVE'
        check (status in ('ACTIVE', 'INACTIVE')),
    procurement_settings jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table users (
    id uuid primary key default gen_random_uuid(),
    company_id uuid not null references companies(id),
    name text not null,
    email text not null,
    phone text,
    job_title text,
    status text not null default 'ACTIVE'
        check (status in ('ACTIVE', 'INACTIVE')),
    roles jsonb not null default '[]'::jsonb,
    permissions jsonb not null default '[]'::jsonb,
    approval_limit numeric(14, 2),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (company_id, email)
);

create table construction_sites (
    id uuid primary key default gen_random_uuid(),
    company_id uuid not null references companies(id),
    code text not null,
    name text not null,
    status text not null default 'ACTIVE'
        check (status in ('PLANNED', 'ACTIVE', 'PAUSED', 'COMPLETED')),
    cost_center text,
    delivery_address jsonb not null,
    site_contact jsonb not null default '{}'::jsonb,
    receiving_rules jsonb not null default '{}'::jsonb,
    procurement_preferences jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (company_id, code)
);

create table product_categories (
    id uuid primary key default gen_random_uuid(),
    code text not null unique,
    name text not null,
    specification_schema jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table suppliers (
    id uuid primary key default gen_random_uuid(),
    legal_name text,
    trade_name text not null,
    tax_id_value text unique,
    status text not null default 'ACTIVE'
        check (status in ('ACTIVE', 'INACTIVE', 'BLOCKED')),
    verification_level text not null default 'DISCOVERED'
        check (verification_level in ('DISCOVERED', 'CONTACTABLE', 'VERIFIED')),
    address jsonb not null default '{}'::jsonb,
    service_coverage jsonb not null default '{}'::jsonb,
    commercial_terms jsonb not null default '{}'::jsonb,
    operational_capabilities jsonb not null default '{}'::jsonb,
    compliance jsonb not null default '{}'::jsonb,
    performance jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table supplier_contacts (
    id uuid primary key default gen_random_uuid(),
    supplier_id uuid not null references suppliers(id) on delete cascade,
    name text not null,
    role text,
    phone text not null,
    email text,
    whatsapp_enabled boolean not null default false,
    preferred_channel text,
    status text not null default 'ACTIVE'
        check (status in ('ACTIVE', 'INACTIVE')),
    created_at timestamptz not null default now()
);

create table supplier_categories (
    supplier_id uuid not null references suppliers(id) on delete cascade,
    category_id uuid not null references product_categories(id) on delete cascade,
    brands jsonb not null default '[]'::jsonb,
    primary key (supplier_id, category_id)
);

create table purchase_requests (
    id uuid primary key default gen_random_uuid(),
    company_id uuid not null references companies(id),
    construction_site_id uuid not null references construction_sites(id),
    requested_by uuid not null references users(id),
    title text not null,
    status text not null default 'DRAFT'
        check (status in (
            'DRAFT', 'NORMALIZING', 'CLARIFYING', 'READY', 'QUOTING',
            'COMPARING', 'NEGOTIATING', 'AWAITING_APPROVAL', 'APPROVED',
            'ORDERED', 'CANCELLED', 'CLOSED'
        )),
    priority text not null default 'NORMAL'
        check (priority in ('LOW', 'NORMAL', 'HIGH', 'URGENT')),
    required_at timestamptz not null,
    currency char(3) not null default 'BRL',
    maximum_budget numeric(14, 2),
    payment_terms text,
    tax_invoice_required boolean not null default true,
    substitutions_allowed boolean not null default false,
    split_order_allowed boolean not null default true,
    partial_delivery_allowed boolean not null default false,
    notes text,
    current_version integer not null default 1,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table purchase_request_items (
    id uuid primary key default gen_random_uuid(),
    purchase_request_id uuid not null
        references purchase_requests(id) on delete cascade,
    client_item_id text,
    raw_description text not null,
    normalized_description text,
    category_id uuid references product_categories(id),
    quantity numeric(14, 3) not null check (quantity > 0),
    unit text not null,
    reference_unit_price numeric(14, 4)
        check (reference_unit_price is null or reference_unit_price >= 0),
    maximum_unit_price numeric(14, 4)
        check (maximum_unit_price is null or maximum_unit_price >= 0),
    specifications jsonb not null default '{}'::jsonb,
    agent_confidence numeric(4, 3)
        check (agent_confidence is null or agent_confidence between 0 and 1),
    normalization_status text not null default 'PENDING'
        check (normalization_status in (
            'PENDING', 'PROCESSING', 'NEEDS_CLARIFICATION', 'READY', 'REJECTED'
        )),
    missing_critical_fields jsonb not null default '[]'::jsonb,
    warnings jsonb not null default '[]'::jsonb,
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (purchase_request_id, client_item_id)
);

create table agent_events (
    id uuid primary key default gen_random_uuid(),
    purchase_request_id uuid references purchase_requests(id) on delete cascade,
    event_type text not null,
    actor_type text not null check (actor_type in ('USER', 'AGENT', 'SYSTEM')),
    actor_id text,
    reason text,
    input_payload jsonb,
    output_payload jsonb,
    created_at timestamptz not null default now()
);

create table normalization_runs (
    id uuid primary key default gen_random_uuid(),
    purchase_request_id uuid not null
        references purchase_requests(id) on delete cascade,
    model_version text not null,
    catalog_version text not null,
    status text not null
        check (status in ('PROCESSING', 'NEEDS_CLARIFICATION', 'COMPLETED', 'FAILED')),
    input_payload jsonb not null,
    output_payload jsonb,
    error_payload jsonb,
    created_at timestamptz not null default now(),
    completed_at timestamptz
);

create table clarification_requests (
    id uuid primary key default gen_random_uuid(),
    purchase_request_id uuid not null
        references purchase_requests(id) on delete cascade,
    request_version integer not null,
    status text not null default 'WAITING_USER'
        check (status in ('WAITING_USER', 'ANSWERED', 'CANCELLED', 'EXPIRED')),
    created_at timestamptz not null default now(),
    answered_at timestamptz
);

create table clarification_questions (
    id uuid primary key default gen_random_uuid(),
    clarification_request_id uuid not null
        references clarification_requests(id) on delete cascade,
    purchase_request_item_id uuid not null
        references purchase_request_items(id) on delete cascade,
    field_path text not null,
    severity text not null
        check (severity in ('BLOCKING', 'WARNING', 'OPTIONAL')),
    reason_code text not null,
    question text not null,
    help_text text,
    answer_type text not null
        check (answer_type in ('TEXT', 'NUMBER', 'SINGLE_CHOICE', 'MULTIPLE_CHOICE', 'BOOLEAN')),
    options jsonb not null default '[]'::jsonb,
    allow_free_text boolean not null default false,
    answer_payload jsonb,
    answered_by uuid references users(id),
    answered_at timestamptz,
    created_at timestamptz not null default now()
);

create table purchase_request_item_versions (
    id uuid primary key default gen_random_uuid(),
    purchase_request_item_id uuid not null
        references purchase_request_items(id) on delete cascade,
    version integer not null,
    snapshot jsonb not null,
    changed_by_type text not null
        check (changed_by_type in ('USER', 'AGENT', 'SYSTEM')),
    changed_by_id text,
    change_reason text not null,
    created_at timestamptz not null default now(),
    unique (purchase_request_item_id, version)
);

create table classification_memory (
    id uuid primary key default gen_random_uuid(),
    company_id uuid references companies(id) on delete cascade,
    normalized_input text not null,
    canonical_category text not null,
    category_label text not null,
    subcategory text,
    confirmed_attributes jsonb not null default '[]'::jsonb,
    confirmation_count integer not null default 1,
    last_confirmed_by uuid references users(id),
    last_confirmed_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    unique (company_id, normalized_input)
);

create table supplier_selection_runs (
    id uuid primary key default gen_random_uuid(),
    purchase_request_id uuid not null
        references purchase_requests(id) on delete cascade,
    policy_version text not null,
    status text not null
        check (status in ('PROCESSING', 'COMPLETED', 'INSUFFICIENT_SUPPLIERS', 'FAILED')),
    input_snapshot jsonb not null,
    output_snapshot jsonb,
    created_at timestamptz not null default now(),
    completed_at timestamptz
);

create table sourcing_lots (
    id uuid primary key default gen_random_uuid(),
    purchase_request_id uuid not null
        references purchase_requests(id) on delete cascade,
    selection_run_id uuid references supplier_selection_runs(id) on delete set null,
    status text not null default 'DRAFT'
        check (status in ('DRAFT', 'READY', 'RFQ_CREATED', 'CANCELLED')),
    required_categories jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table sourcing_lot_items (
    sourcing_lot_id uuid not null references sourcing_lots(id) on delete cascade,
    purchase_request_item_id uuid not null
        references purchase_request_items(id) on delete cascade,
    primary key (sourcing_lot_id, purchase_request_item_id)
);

create table supplier_selection_candidates (
    id uuid primary key default gen_random_uuid(),
    selection_run_id uuid not null
        references supplier_selection_runs(id) on delete cascade,
    sourcing_lot_id uuid not null references sourcing_lots(id) on delete cascade,
    supplier_id uuid not null references suppliers(id),
    eligible boolean not null,
    selected boolean not null default false,
    rank integer,
    score numeric(5, 4),
    category_match_type text
        check (category_match_type is null or category_match_type in ('EXACT', 'MEMORY_MATCH', 'SEMANTIC', 'NONE')),
    category_match_score numeric(5, 4),
    item_coverage numeric(5, 4),
    risk_level text
        check (risk_level is null or risk_level in ('LOW', 'MEDIUM', 'HIGH')),
    factor_snapshot jsonb not null default '{}'::jsonb,
    reasons jsonb not null default '[]'::jsonb,
    exclusion_reason_code text,
    created_at timestamptz not null default now(),
    unique (selection_run_id, sourcing_lot_id, supplier_id)
);

create table supplier_contact_consents (
    id uuid primary key default gen_random_uuid(),
    supplier_contact_id uuid not null
        references supplier_contacts(id) on delete cascade,
    channel text not null check (channel in ('WHATSAPP')),
    status text not null
        check (status in ('OPTED_IN', 'OPTED_OUT', 'REVOKED', 'UNKNOWN')),
    purpose text not null,
    source text not null,
    evidence jsonb not null default '{}'::jsonb,
    granted_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz not null default now()
);

create table rfqs (
    id uuid primary key default gen_random_uuid(),
    purchase_request_id uuid not null
        references purchase_requests(id) on delete cascade,
    sourcing_lot_id uuid not null references sourcing_lots(id),
    supplier_id uuid not null references suppliers(id),
    status text not null default 'DRAFT'
        check (status in ('DRAFT', 'READY_TO_SEND', 'SENT', 'RESPONDED', 'DECLINED', 'EXPIRED', 'CANCELLED')),
    response_deadline timestamptz not null,
    request_snapshot jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (sourcing_lot_id, supplier_id)
);

create table supplier_conversations (
    id uuid primary key default gen_random_uuid(),
    rfq_id uuid not null references rfqs(id) on delete cascade,
    supplier_id uuid not null references suppliers(id),
    supplier_contact_id uuid not null references supplier_contacts(id),
    channel text not null check (channel in ('WHATSAPP', 'DEMO')),
    provider text not null check (provider in ('META_CLOUD_API', 'DEMO_PROVIDER')),
    status text not null default 'DRAFT'
        check (status in (
            'DRAFT', 'WAITING_SEND', 'WAITING_RESPONSE', 'FOLLOW_UP_DUE',
            'RESPONSE_RECEIVED', 'PARSING', 'INCOMPLETE_RESPONSE',
            'VALID_QUOTE', 'NEEDS_HUMAN_ROUTING', 'CLOSED'
        )),
    last_inbound_at timestamptz,
    last_outbound_at timestamptz,
    service_window_expires_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table conversation_messages (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid references supplier_conversations(id) on delete cascade,
    direction text not null check (direction in ('INBOUND', 'OUTBOUND')),
    channel text not null check (channel in ('WHATSAPP', 'DEMO')),
    provider text not null check (provider in ('META_CLOUD_API', 'DEMO_PROVIDER')),
    provider_message_id text,
    message_type text not null
        check (message_type in ('TEXT', 'TEMPLATE', 'INTERACTIVE', 'DOCUMENT', 'IMAGE', 'AUDIO', 'UNKNOWN')),
    status text not null
        check (status in ('QUEUED', 'SUBMITTED', 'SENT', 'DELIVERED', 'READ', 'RECEIVED', 'FAILED')),
    idempotency_key text,
    content jsonb not null,
    raw_provider_payload jsonb,
    failure_code text,
    failure_detail text,
    occurred_at timestamptz not null,
    created_at timestamptz not null default now(),
    unique (provider, provider_message_id),
    unique (idempotency_key)
);

create table messaging_webhook_events (
    id uuid primary key default gen_random_uuid(),
    provider text not null,
    provider_event_key text not null,
    signature_valid boolean not null,
    payload jsonb not null,
    processing_status text not null default 'RECEIVED'
        check (processing_status in ('RECEIVED', 'PROCESSED', 'IGNORED', 'FAILED')),
    error_detail text,
    received_at timestamptz not null default now(),
    processed_at timestamptz,
    unique (provider, provider_event_key)
);

create index idx_purchase_requests_company_status
    on purchase_requests (company_id, status);

create index idx_purchase_request_items_request
    on purchase_request_items (purchase_request_id);

create index idx_agent_events_request_created
    on agent_events (purchase_request_id, created_at);

create index idx_normalization_runs_request
    on normalization_runs (purchase_request_id, created_at);

create index idx_clarification_requests_request_status
    on clarification_requests (purchase_request_id, status);

create index idx_classification_memory_company_category
    on classification_memory (company_id, canonical_category);

create index idx_supplier_selection_runs_request
    on supplier_selection_runs (purchase_request_id, created_at);

create index idx_supplier_selection_candidates_lot_selected
    on supplier_selection_candidates (sourcing_lot_id, selected, rank);

create index idx_supplier_conversations_contact_status
    on supplier_conversations (supplier_contact_id, status);

create index idx_conversation_messages_conversation_occurred
    on conversation_messages (conversation_id, occurred_at);

create index idx_messaging_webhook_events_status
    on messaging_webhook_events (processing_status, received_at);
