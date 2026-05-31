-- ============================================================
-- Gov Contract Intel Platform — Supabase Schema
-- Paste this entire file into Supabase SQL Editor and run it
-- ============================================================

-- Enable UUID generation
create extension if not exists "uuid-ossp";

-- ============================================================
-- CONTRACTS
-- Raw scraped contract data from all sources
-- ============================================================
create table if not exists contracts (
  id            uuid primary key default uuid_generate_v4(),
  source        text not null,           -- 'txsmartbuy' | 'sam_gov' | 'houston' | 'dallas'
  source_id     text not null,           -- original ID from source system
  title         text not null,
  agency        text,
  naics         text,
  value         numeric,
  due_date      date,
  set_aside     text,
  url           text,
  raw_html      text,
  scraped_at    timestamptz default now(),

  -- prevent duplicates on re-run
  unique (source, source_id)
);

create index if not exists idx_contracts_source on contracts(source);
create index if not exists idx_contracts_due_date on contracts(due_date);
create index if not exists idx_contracts_scraped_at on contracts(scraped_at);

-- ============================================================
-- ENRICHED CONTRACTS
-- AI-processed summaries and scores
-- ============================================================
create table if not exists enriched_contracts (
  id                uuid primary key default uuid_generate_v4(),
  contract_id       uuid not null references contracts(id) on delete cascade,
  summary           text,
  category          text,               -- IT | construction | staffing | healthcare | professional_services | other
  complexity_score  int check (complexity_score between 1 and 10),
  first_time_friendly text,             -- yes | no | maybe
  first_time_reasoning text,
  match_tags        text[],
  content_hash      text,               -- hash of raw_html to detect changes
  processed_at      timestamptz default now(),

  unique (contract_id)
);

create index if not exists idx_enriched_category on enriched_contracts(category);
create index if not exists idx_enriched_processed_at on enriched_contracts(processed_at);

-- ============================================================
-- CLIENTS
-- Subscribers to the digest
-- ============================================================
create table if not exists clients (
  id          uuid primary key default uuid_generate_v4(),
  email       text not null unique,
  company     text,
  niche       text,                     -- IT | construction | staffing | all
  plan        text default 'trial',     -- trial | basic | pro
  active      boolean default true,
  created_at  timestamptz default now()
);

create index if not exists idx_clients_niche on clients(niche);
create index if not exists idx_clients_active on clients(active);

-- ============================================================
-- DELIVERIES
-- Track what was sent to whom
-- ============================================================
create table if not exists deliveries (
  id            uuid primary key default uuid_generate_v4(),
  client_id     uuid not null references clients(id) on delete cascade,
  contract_ids  uuid[],
  sent_at       timestamptz default now(),
  email_id      text                    -- Resend message ID for tracking
);

create index if not exists idx_deliveries_client_id on deliveries(client_id);
create index if not exists idx_deliveries_sent_at on deliveries(sent_at);

-- ============================================================
-- SCRAPER LOGS
-- Track every scraper run — gov sites break often
-- ============================================================
create table if not exists scraper_logs (
  id            uuid primary key default uuid_generate_v4(),
  source        text not null,
  status        text not null,          -- success | error | partial
  contracts_found int default 0,
  contracts_new   int default 0,
  error_message text,
  duration_ms   int,
  ran_at        timestamptz default now()
);

create index if not exists idx_scraper_logs_source on scraper_logs(source);
create index if not exists idx_scraper_logs_ran_at on scraper_logs(ran_at);

-- ============================================================
-- DONE — Schema created successfully
-- ============================================================
