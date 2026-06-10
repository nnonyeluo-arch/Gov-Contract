-- TX Contract Intel — Webhook Migration
-- Run this in the Supabase SQL Editor before deploying the stripe-webhook function

-- ── subscribers table (create if it doesn't exist yet) ──────────────────────
create table if not exists subscribers (
  id                      bigint generated always as identity primary key,
  email                   text unique not null,
  name                    text,
  stripe_customer_id      text,
  stripe_subscription_id  text,
  status                  text default 'active',
  source                  text default 'manual',
  created_at              timestamptz default now()
);

-- Add any columns that may be missing if the table already existed
alter table subscribers
  add column if not exists name                   text,
  add column if not exists stripe_customer_id     text,
  add column if not exists stripe_subscription_id text,
  add column if not exists status                 text default 'active',
  add column if not exists source                 text default 'manual';

create index if not exists idx_subscribers_stripe_customer
  on subscribers (stripe_customer_id);

-- ── idempotency table ────────────────────────────────────────────────────────
-- Prevents duplicate processing when Stripe retries webhook delivery
create table if not exists processed_events (
  event_id     text primary key,
  processed_at timestamptz default now()
);

-- ── dead letter table ────────────────────────────────────────────────────────
-- Logs welcome emails that failed to send so they can be retried manually
create table if not exists email_failures (
  id         bigint generated always as identity primary key,
  email      text,
  event_id   text,
  error      text,
  created_at timestamptz default now()
);
