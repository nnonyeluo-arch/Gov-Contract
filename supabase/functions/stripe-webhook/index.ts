// Supabase Edge Function: stripe-webhook
// Listens for Stripe events and syncs client state to Supabase.
//
// Deploy:
//   supabase functions deploy stripe-webhook --no-verify-jwt
//
// Add secrets:
//   supabase secrets set STRIPE_WEBHOOK_SECRET=whsec_...
//   supabase secrets set SUPABASE_SERVICE_ROLE_KEY=...  (already set)

import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import Stripe from "https://esm.sh/stripe@14?target=deno";

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, {
  apiVersion: "2023-10-16",
  httpClient: Stripe.createFetchHttpClient(),
});

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
);

const webhookSecret = Deno.env.get("STRIPE_WEBHOOK_SECRET")!;

serve(async (req) => {
  const signature = req.headers.get("stripe-signature");
  if (!signature) {
    return new Response("Missing stripe-signature header", { status: 400 });
  }

  const body = await req.text();

  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(body, signature, webhookSecret);
  } catch (err) {
    console.error("Webhook signature verification failed:", err);
    return new Response(`Webhook Error: ${err.message}`, { status: 400 });
  }

  console.log(`[webhook] Received event: ${event.type}`);

  try {
    switch (event.type) {

      // ── New paying customer ──────────────────────────────────────────
      case "checkout.session.completed": {
        const session = event.data.object as Stripe.Checkout.Session;

        if (session.mode !== "subscription") break;

        const email = session.customer_details?.email || session.customer_email;
        const name  = session.customer_details?.name || "";
        const customerId     = session.customer as string;
        const subscriptionId = session.subscription as string;

        if (!email) {
          console.error("[webhook] No email on checkout session:", session.id);
          break;
        }

        const { error } = await supabase.from("clients").upsert({
          email,
          name,
          company: name,
          stripe_customer_id: customerId,
          stripe_subscription_id: subscriptionId,
          active: true,
          niches: [],               // empty = receive all contract categories
          source: "stripe",
        }, { onConflict: "email" });

        if (error) {
          console.error("[webhook] Error inserting client:", error);
        } else {
          console.log(`[webhook] ✓ Client added: ${email}`);
        }
        break;
      }

      // ── Subscription cancelled / expired ────────────────────────────
      case "customer.subscription.deleted": {
        const sub = event.data.object as Stripe.Subscription;
        const customerId = sub.customer as string;

        const { error } = await supabase
          .from("clients")
          .update({ active: false })
          .eq("stripe_customer_id", customerId);

        if (error) {
          console.error("[webhook] Error deactivating client:", error);
        } else {
          console.log(`[webhook] ✓ Client deactivated: customer ${customerId}`);
        }
        break;
      }

      // ── Payment failed — optional: flag but don't deactivate yet ────
      case "invoice.payment_failed": {
        const invoice = event.data.object as Stripe.Invoice;
        const customerId = invoice.customer as string;

        // Log the failure but keep active=true (Stripe retries for ~7 days)
        console.warn(`[webhook] Payment failed for customer ${customerId} — Stripe will retry`);
        break;
      }

      default:
        console.log(`[webhook] Unhandled event type: ${event.type}`);
    }
  } catch (err) {
    console.error("[webhook] Handler error:", err);
    return new Response("Internal error", { status: 500 });
  }

  return new Response(JSON.stringify({ received: true }), {
    headers: { "Content-Type": "application/json" },
    status: 200,
  });
});
