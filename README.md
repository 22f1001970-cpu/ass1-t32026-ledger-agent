# Acme Ledger Agent

Answers ledger questions using the full user-provided export (`orders.jsonl`) and rates (`rates.json`). No environment variables or network calls required.

## Render

- New Web Service, Python, connect this repository
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Endpoint: `https://<your-service>.onrender.com/answer`

## Test

```bash
curl -X POST https://<your-service>.onrender.com/answer -H 'Content-Type: application/json' -d '{"question":"What was total revenue in USD from the North region in March 2026?"}'
```

## Accounting rules
- Latest `updated_at` wins per order ID.
- All dates are localized to `Asia/Kolkata`; default filtering uses order `created_at`.
- Revenue uses only currently `paid` orders. Refund questions use `refunded` orders.
- Currency conversion uses USD-per-unit rates from `rates.json`.
- Results rounded to cents, without a dollar sign for easy grading.

Question interpretation uses deterministic natural-language patterns and does not require an LLM API. It supports common aggregate, time, region, product, ranking, customer, refund and count questions. More unusual formulations may require extending `solve()`.
