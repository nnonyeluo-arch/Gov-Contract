/**
 * TX Contract Intel — Stripe Webhook Handler
 * Supabase Edge Function: /stripe-webhook
 *
 * No Stripe npm dependency — uses Web Crypto API for signature verification,
 * which is natively supported in Supabase's Deno runtime.
 *
 * Required secrets (Supabase Dashboard → Edge Functions → Secrets):
 *   STRIPE_WEBHOOK_SECRET   — whsec_... from Stripe Dashboard → Webhooks
 *   RESEND_API_KEY          — from Resend Dashboard
 *   SUPABASE_URL            — auto-injected
 *   SUPABASE_SERVICE_ROLE_KEY — auto-injected
 */

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.39.0";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
);

const RESEND_API_KEY      = Deno.env.get("RESEND_API_KEY")!;
const WEBHOOK_SECRET      = Deno.env.get("STRIPE_WEBHOOK_SECRET")!;
const TOLERANCE_SECONDS   = 300; // 5 minutes


// ── Stripe signature verification (no library needed) ─────────────────────────

async function verifyStripeSignature(
  rawBody: string,
  sigHeader: string,
  secret: string
): Promise<boolean> {
  // Parse timestamp and signatures from the header
  const parts = Object.fromEntries(
    sigHeader.split(",").map((p) => p.split("=") as [string, string])
  );
  const timestamp = parts["t"];
  const signatures = sigHeader
    .split(",")
    .filter((p) => p.startsWith("v1="))
    .map((p) => p.slice(3));

  if (!timestamp || signatures.length === 0) return false;

  // Reject stale events
  const age = Math.floor(Date.now() / 1000) - parseInt(timestamp, 10);
  if (age > TOLERANCE_SECONDS) return false;

  // Compute HMAC-SHA256(secret, "timestamp.body")
  const encoder   = new TextEncoder();
  const keyData   = encoder.encode(secret);
  const msgData   = encoder.encode(`${timestamp}.${rawBody}`);

  const cryptoKey = await crypto.subtle.importKey(
    "raw", keyData, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sigBuffer = await crypto.subtle.sign("HMAC", cryptoKey, msgData);
  const computed  = Array.from(new Uint8Array(sigBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  return signatures.some((s) => s === computed);
}


// ── Welcome email ──────────────────────────────────────────────────────────────

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
    const err = await res.text();
    throw new Error(`Resend ${res.status}: ${err}`);
  }
}


// ── Main handler ───────────────────────────────────────────────────────────────

Deno.serve(async (req) => {
  if (req.method === "GET") {
    return new Response("OK", { status: 200 });
  }

  // Read raw body BEFORE any parsing
  const rawBody  = await req.text();
  const sigHeader = req.headers.get("stripe-signature");

  if (!sigHeader) {
    return new Response("Missing stripe-signature", { status: 400 });
  }

  // 1. Verify signature
  const valid = await verifyStripeSignature(rawBody, sigHeader, WEBHOOK_SECRET);
  if (!valid) {
    console.error("Invalid Stripe signature");
    return new Response("Invalid signature", { status: 400 });
  }

  // Parse event
  let event: { id: string; type: string; data: { object: Record<string, unknown> } };
  try {
    event = JSON.parse(rawBody);
  } catch {
    return new Response("Invalid JSON", { status: 400 });
  }

  console.log(`Event received: ${event.type} / ${event.id}`);

  // 2. Idempotency check
  const { data: existing, error: selectErr } = await supabase
    .from("processed_events")
    .select("event_id")
    .eq("event_id", event.id)
    .maybeSingle();

  if (selectErr) console.error("processed_events select error:", selectErr.message, selectErr.code);

  if (existing) {
    console.log(`Event ${event.id} already processed — skipping.`);
    return new Response(JSON.stringify({ received: true, duplicate: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  // 3. Handle checkout.session.completed
  if (event.type === "checkout.session.completed") {
    const session = event.data.object as Record<string, unknown>;

    const details = (session.customer_details as Record<string, string> | null) ?? {};
    const customerEmail = (details.email ?? session.customer_email ?? "") as string;
    const customerName  = (details.name ?? "") as string;
    const stripeCustomerId     = (session.customer ?? null) as string | null;
    const stripeSubscriptionId = (session.subscription ?? null) as string | null;

    if (customerEmail) {
      // 4. Upsert subscriber
      const { error: upsertErr } = await supabase
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

      if (upsertErr) console.error("Subscriber upsert error:", upsertErr.message);

      // 5. Send welcome email — always return 200 even on failure
      try {
        await sendWelcomeEmail(customerEmail, customerName);
        console.log(`Welcome email sent to ${customerEmail}`);
      } catch (emailErr) {
        console.error("Welcome email failed:", emailErr.message);
        await supabase.from("email_failures").insert({
          email: customerEmail,
          event_id: event.id,
          error: emailErr.message,
        });
      }
    }
  }

  // 6. Record event
  const { error: insertErr } = await supabase
    .from("processed_events")
    .insert({ event_id: event.id });

  if (insertErr) {
    console.error("processed_events insert error:", insertErr.message, insertErr.code);
  } else {
    console.log(`Event ${event.id} recorded in processed_events.`);
  }

  return new Response(JSON.stringify({ received: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
});
