# ADAR Product Subscriptions

DocIntel supports an independent Stripe subscription for ADAR Knowledge
Academy. It does not alter the user's Free, Pro, or Enterprise platform tier.
ADAR Front Desk is deployed and billed by `adar-core`'s scheduling service.

## Plan catalog

| Product | Interval | Price | Stripe secret environment variable |
| --- | --- | ---: | --- |
| ADAR Knowledge Academy | Monthly | $125 USD | `STRIPE_KNOWLEDGE_ACADEMY_MONTHLY_PRICE_ID` |
| ADAR Knowledge Academy | Yearly | $1,200 USD | `STRIPE_KNOWLEDGE_ACADEMY_YEARLY_PRICE_ID` |

The yearly Knowledge Academy plan saves $300 compared with twelve monthly
payments.

## 1. Create Stripe products and prices

Use Stripe test mode first.

1. Open Stripe Dashboard, then Product catalog.
2. Create product `ADAR Knowledge Academy`.
3. Add a recurring monthly price of `$125.00 USD`.
4. Add a recurring yearly price of `$1,200.00 USD`.
5. Copy each `price_...` identifier. Do not use the `prod_...` identifier.

The backend retrieves each Stripe Price before checkout and rejects a price
whose amount, currency, interval, or active state does not match this catalog.

## 2. Store price IDs in Google Secret Manager

Create each secret once:

```bash
PROJECT_ID=bdas-493785

for SECRET in \
  docintel-stripe-knowledge-academy-monthly-price-id \
  docintel-stripe-knowledge-academy-yearly-price-id; do
  gcloud secrets describe "$SECRET" --project="$PROJECT_ID" >/dev/null 2>&1 || \
    gcloud secrets create "$SECRET" --replication-policy=automatic --project="$PROJECT_ID"
done
```

Add the four price IDs without putting them directly in source control:

```bash
read -r -p "Knowledge Academy monthly price ID: " VALUE
printf %s "$VALUE" | gcloud secrets versions add \
  docintel-stripe-knowledge-academy-monthly-price-id --data-file=- --project="$PROJECT_ID"

read -r -p "Knowledge Academy yearly price ID: " VALUE
printf %s "$VALUE" | gcloud secrets versions add \
  docintel-stripe-knowledge-academy-yearly-price-id --data-file=- --project="$PROJECT_ID"

```

The backend deployment script maps these secrets to the corresponding runtime
environment variables when the secrets exist.

## 3. Configure the Stripe webhook

Use the existing billing endpoint:

```text
https://docintel.adar.agomoniai.com/api/billing/webhook
```

Subscribe it to:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

Store the webhook signing secret in `docintel-stripe-webhook-secret`. The
backend verifies the `Stripe-Signature` header before processing an event.

## 4. Configure Stripe Customer Portal

In Stripe Dashboard, open Billing, Customer portal, then enable:

- payment method updates;
- subscription cancellation;
- switching between the monthly and yearly price for each product;
- invoice history.

Keep unrelated product switches disabled. DocIntel creates a short-lived portal
session only after an authenticated user selects Manage subscriptions.

## 5. Deploy

```bash
cd /Users/brajadas/project/adar-rag
bash deploy.sh --backend
bash deploy.sh --frontend
```

On backend startup, the schema initializer creates `product_subscriptions`.

## 6. Verify in test mode

1. Sign in to DocIntel.
2. Open Plans & Billing.
3. Confirm the Knowledge Academy card displays monthly and yearly choices.
4. Select each plan and verify Stripe Checkout shows the exact expected price.
5. Complete checkout with a Stripe test card.
6. Return to DocIntel and confirm the product displays Active and its interval.
7. Select Manage and verify the Stripe Customer Portal opens.
8. Switch monthly to yearly, then confirm `customer.subscription.updated`
   updates the interval in DocIntel.
9. Cancel and confirm the status changes after Stripe sends the webhook.
10. Verify purchasing the product did not change the user's platform tier.

## 7. Promote to live mode

Stripe test and live objects have different IDs. Recreate the product and its
two recurring prices in live mode, add the live `price_...` values as new
Secret Manager versions, configure a live webhook endpoint and signing secret,
then redeploy and complete one low-risk live transaction.

Usage limits can be added later as product entitlements keyed by
`product_key`, subscription `status`, and the selected `billing_interval`.
