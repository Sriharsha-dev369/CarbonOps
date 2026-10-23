# CarbonOps

Finds waste in a cloud estate, prices it in ₹ and kg CO₂e, and acts on it under each client's policy, with human approval where the policy requires it.

Business logic is implemented during the hackathon; tests in `tests/` define done.

## Setup

```bash
uv sync
cp .env.example .env    # then add your key: LLM_API_KEY=...
```

## Run

```bash
./run.sh
```

This starts `moto_server -p 5000` (fake AWS) in the background and `streamlit run app.py`.
