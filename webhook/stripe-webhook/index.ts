/**
 * TX Contract Intel — Stripe Webhook Handler
 * Supabase Edge Function: /stripe-webhook
 *
 * Flow:
 *   Stripe checkout.session.completed
 *     → verify signature
 *     → idempotency check
 *     → upsert subscriber
 *     → send welcome email via Resend
 *     → record event id
 *     → return 200 (always — Stripe stops retrying on 2xx)
 *
 * Required env vars (set in Supabase Dashboard → Edge Functions → Secrets):
 *   STRIPE_SECRET_KEY
 *   STRIPE_WEBHOOK_SECRET
 *   RESEND_API_KEY
 *   SUPABASE_URL         (auto-injected)
 *   SUPABASE_SERVICE_ROLE_KEY (auto-injected)
 */

import Stripe from "https://esm.sh/stripe@14.21.0?target=deno&no-check";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.39.0";

// ── clients ──────────────────────────────────────────────────────────────────

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, {
  apiVersion: "2023-10-16",
  httpClient: Stripe.createFetchHttpClient(),
});

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
);

const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY")!;

// ── welcome email ─────────────────────────────────────────────────────────────

async function sendWelcomeEmail(email: string, name: string): Promise<void> {
  const firstName = name?.trim().split(" ")[0] || "there";

  const body = `${firstName},

Welcome to TX Contract Intel. Here's what happens next.

Every Monday morning you get a digest of open Texas government contract opportunities matched to your industry. We pull from 9 sources daily: SAM.gov, TxSmartBuy, TxDOT, and the major city and county procurement portals, so nothing slips past because it was posted on a site you don't check.

Each contract comes with a plain English summary, a complexity score, and a flag for whether it's friendly to first-time government contractors.

Two things that make the digest better:

1. Reply to this email with the categories or keywords you care about most. Matching gets sharper when I know exactly what you bid on.
2. If a digest ever misses the mark, say so. This product is shaped by subscriber feedback and I read every reply.

Your first digest arrives Monday morning. Questions before then, just reply.

King Okafor
TX Contract Intel
txcontractintel.com`;

  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: "King at TX Contract Intel <okafor@txcontractintel.com>",
      to: [email],
      subject: "You're in. First digest lands Monday.",
      text: body,
      tags: [{ name: "category", value: "welcome" }],
    }),
  });

  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(`Resend ${res.status}: ${errorText}`);
  }
}

// ── main handler ──────────────────────────────────────────────────────────────

Deno.serve(async (req) => {
  // Health check
  if (req.method === "GET") {
    return new Response("OK", { status: 200 });
  }

  // IMPORTANT: read raw body BEFORE any JSON parsing — Stripe validates the raw bytes
  const rawBody = await req.text();
  const signature = req.headers.get("stripe-signature");

  if (!signature) {
    return new Response("Missing stripe-signature header", { status: 400 });
  }

  // ── 1. Verify Stripe signature ─────────────────────────────────────────────
  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      rawBody,
      signature,
      Deno.env.get("STRIPE_WEBHOOK_SECRET")!
    );
  } catch (err) {
    console.error("Signature verification failed:", err.message);
    return new Response(`Webhook error: ${err.message}`, { status: 400 });
  }

  // ── 2. Idempotency check ───────────────────────────────────────────────────
  const { data: existing } = await supabase
    .from("processed_events")
    .select("event_id")
    .eq("event_id", event.id)
    .maybeSingle();

  if (existing) {
    console.log(`Event ${event.id} already processed — skipping.`);
    return new Response(JSON.stringify({ received: true, duplicate: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  // ── 3. Handle checkout.session.completed ──────────────────────────────────
  if (event.type === "checkout.session.completed") {
    const session = event.data.object as Stripe.Checkout.Session;

    const customerEmail =
      session.customer_details?.email ?? session.customer_email ?? null;
    const customerName = session.customer_details?.name ?? "";
    const stripeCustomerId =
      typeof session.customer === "string"
        ? session.customer
        : (session.customer as Stripe.Customer | null)?.id ?? null;
    const stripeSubscriptionId =
      typeof session.subscription === "string"
        ? session.subscription
        : (session.subscription as Stripe.Subscription | null)?.id ?? null;

    if (!customerEmail) {
      console.error("No customer email in session — recording event and moving on.");
      await supabase.from("processed_events").insert({ event_id: event.id });
      return new Response(JSON.stringify({ received: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    // ── 4. Upsert subscriber ────────────────────────────────────────────────
    const { error: upsertError } = await supabase
      .from("subscribers")
      .upsert(
        {
          email: customerEmail,
          name: customerName,
          stripe_customer_id: stripeCustomerId,
          stripe_subscription_id: stripeSubscriptionId,
          status: "active",
          source: "stripe",
        },
        { onConflict: "email" }
      );

    if (upsertError) {
      console.error("Subscriber upsert error:", upsertError.message);
      // Don't return early — still try to send the email
    }

    // ── 5. Send welcome email (return 200 even if this fails) ───────────────
    try {
      await sendWelcomeEmail(customerEmail, customerName);
      console.log(`Welcome email sent to ${customerEmail}`);
    } catch (emailErr) {
      console.error("Welcome email failed:", emailErr.message);
      // Log to dead letter table so it can be retried manually
      await supabase.from("email_failures").insert({
        email: customerEmail,
        event_id: event.id,
        error: emailErr.message,
      });
    }
  }

  // ── 6. Mark event as processed ────────────────────────────────────────────
  await supabase.from("processed_events").insert({ event_id: event.id });

  return new Response(JSON.stringify({ received: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
});
