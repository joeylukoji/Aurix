# Diagrams (as built — MVP)

Dashed boxes = **planned, not built**. "PayPal" below is the local **PayPal simulator** (`paypal-sim/`) until Sandbox credentials are added; the gateway code path is identical for both.

## 1. Context diagram (C4 level 1)

```mermaid
flowchart TB
    USER((User<br/>buyer / seller))
    ARB((Arbiter))
    JURY((Judge / auditor))
    AURIX[AURIX system<br/>testnet protocol + services]
    PAYPAL[PayPal simulator<br/>Sandbox later]
    USER -->|wallet, payments| AURIX
    ARB -->|releases escrows| AURIX
    JURY -->|browses the explorer| AURIX
    AURIX <-->|USD orders, webhooks| PAYPAL
```

## 2. Container diagram (C4 level 2)

```mermaid
flowchart TB
    subgraph AURIX[AURIX system]
        WEB["web (browser)<br/>secp256k1 keys, SIGN_MODE_DIRECT, AG Grid"]
        GW["gateway :8080<br/>FastAPI + SQLite"]
        GD["guardian :8000<br/>FastAPI + logistic regression + SHAP"]
        CH["chain aurixd<br/>RPC :26657, REST :1317, gRPC :9090"]
        DB[(SQLite)]
        IX["indexer + PostgreSQL + Redis<br/>planned"]
    end
    PP["paypal-sim :8090"]
    WEB -->|REST /api/*| GW
    GW --> GD
    GW -->|broadcast, tx_search, REST queries| CH
    GW --> DB
    GW -->|OAuth2, Orders v2| PP
    PP -->|signed webhook| GW
    CH -.-> IX
    style IX stroke-dasharray: 5 5
```

## 3. Use cases

```mermaid
flowchart LR
    U((User))
    A((Arbiter))
    P((PayPal))
    subgraph AURIX system
        UC1([Create a wallet])
        UC2([Check balance / faucet])
        UC3([Send AUR])
        UC4([Create an escrow])
        UC5([Release an escrow])
        UC6([Refund at deadline])
        UC7([Pay in USD via PayPal])
        UC8([Get a risk score])
        UC9([Browse the explorer])
        UC10([Receive a webhook])
    end
    U --> UC1 & UC2 & UC3 & UC4 & UC6 & UC7 & UC8 & UC9
    A --> UC5
    P --> UC10
```

## 4. Sequence — AUR transfer (browser-side signing)

```mermaid
sequenceDiagram
    actor U as User
    participant W as web (browser)
    participant G as gateway
    participant N as guardian
    participant C as aurixd
    U->>W: Enter recipient and amount
    W->>G: POST /api/txs/prepare
    G->>C: account number + sequence (REST 1317)
    G->>N: POST /v1/risk/score
    N-->>G: score, level, SHAP reasons (advisory_only)
    G-->>W: sign doc inputs + risk
    W-->>U: Show risk and reasons
    U->>W: Confirm
    W->>W: Build MsgSend, sign secp256k1 (SIGN_MODE_DIRECT)
    W->>G: POST /api/txs (signed tx bytes)
    G->>C: broadcast_tx_sync (RPC 26657)
    C-->>G: tx hash, height
    G-->>W: confirmed
```

## 5. Sequence — PayPal payment + proof

```mermaid
sequenceDiagram
    actor B as Buyer
    participant W as web
    participant G as gateway
    participant N as guardian
    participant P as paypal-sim
    participant C as aurixd
    B->>W: Amount in USD
    W->>G: POST /api/paypal/orders
    G->>P: POST /v2/checkout/orders (CREATED)
    G->>N: risk score
    G-->>W: order SCORED + approve_url
    B->>P: Approve on buyer page /checkoutnow
    B->>W: Confirm capture
    W->>G: POST /api/paypal/orders/{id}/approve (confirm=true)
    G->>P: POST /v2/checkout/orders/{id}/capture
    G-->>W: CAPTURED
    P->>G: POST /api/paypal/webhook (signed PAYMENT.CAPTURE.COMPLETED)
    G->>P: POST /v1/notifications/verify-webhook-signature
    G->>G: idempotency check, CONFIRMED
    G->>C: MsgAnchorProof sha256(order_id|capture_id) + amount, currency
    C-->>G: height
    G->>G: ANCHORED
```

## 6. Sequence — Smart Money

```mermaid
sequenceDiagram
    actor S as Sender
    actor A as Arbiter
    participant W as web
    participant G as gateway
    participant C as aurixd
    S->>W: Recipient, arbiter, amount, deadline
    W->>G: POST /api/txs (signed MsgCreateEscrow)
    G->>C: broadcast
    C-->>G: escrow LOCKED
    alt Condition met
        A->>W: Release
        W->>G: POST /api/txs (signed MsgReleaseEscrow)
        G->>C: broadcast, RELEASED
    else Deadline reached
        S->>W: Refund
        W->>G: POST /api/txs (signed MsgRefundEscrow)
        G->>C: broadcast, REFUNDED
    end
```

## 7. State diagrams

```mermaid
stateDiagram-v2
    [*] --> LOCKED: MsgCreateEscrow
    LOCKED --> RELEASED: MsgReleaseEscrow (sender or arbiter)
    LOCKED --> REFUNDED: MsgRefundEscrow (height >= deadline)
    RELEASED --> [*]
    REFUNDED --> [*]
```

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> SCORED
    SCORED --> CANCELLED
    SCORED --> CAPTURED: human approval
    CAPTURED --> CONFIRMED: verified webhook
    CONFIRMED --> ANCHORED: MsgAnchorProof
    ANCHORED --> [*]
    CANCELLED --> [*]
```

## 8. Class diagram — gateway

```mermaid
classDiagram
    class App {
        +create_app()
        +handle_event(event)
        +anchor(order_id, capture_id)
    }
    class ChainClient {
        +broadcast(tx_bytes)
        +status()
        +account(addr)
        +escrows()
        +escrow(id)
        +blocks(limit)
        +search_txs(query, limit)
        +proof(hash)
    }
    class PayPalClient {
        +create_order(value, description)
        +capture(order_id)
        +verify_webhook(headers, event)
    }
    class GuardianClient {
        +score(body)
    }
    class DB {
        +record_event_once(event_id, type, order_id)
        +save_risk(context, ref, risk)
        +update_order(order_id, fields)
    }
    App --> ChainClient
    App --> PayPalClient
    App --> GuardianClient
    App --> DB
```

## 9. Trust boundaries

```mermaid
flowchart LR
    subgraph Browser[Browser - holds private keys]
        W[web]
    end
    subgraph Server[Server side - no user keys]
        G[gateway]
        N[guardian - advisory only]
    end
    subgraph Ledger[Source of truth]
        C[aurixd]
    end
    subgraph External[External]
        P[PayPal simulator / Sandbox]
    end
    W -->|signed tx only| G
    G --> N
    G --> C
    P -->|signature verified| G
```

## 10. Deployment

```mermaid
flowchart TB
    subgraph Compose[Docker Compose - deploy/docker-compose.yml]
        CH["chain :26657 :1317"]
        GD["guardian :8000"]
        PS["paypal-sim :8090"]
        GW["gateway :8080 + web"]
    end
    subgraph Render[Render blueprint - deploy/render.yaml]
        R1[same services]
    end
    K8S["Kubernetes - planned"]
    style K8S stroke-dasharray: 5 5
    GW --> CH & GD & PS
```

See [`deploy/README.md`](../../deploy/README.md) for healthchecks and the Render setup.
