# Diagrams

## 1. Context diagram (C4 level 1)

```mermaid
flowchart TB
    USER((User<br/>buyer / seller))
    ARB((Arbiter))
    JURY((Judge / auditor))
    AURIX[AURIX system<br/>protocol + services]
    PAYPAL[PayPal Sandbox<br/>Orders API + Webhooks]
    USER -->|wallet, payments| AURIX
    ARB -->|releases escrows| AURIX
    JURY -->|browses the explorer| AURIX
    AURIX <-->|USD orders, webhooks| PAYPAL
```

## 2. Container diagram (C4 level 2)

```mermaid
flowchart TB
    subgraph AURIX[AURIX system]
        WEB[web<br/>Wallet + explorer]
        GW[gateway<br/>REST orchestration]
        GD[guardian<br/>FastAPI + LightGBM]
        CH[chain<br/>Cosmos SDK + CometBFT]
        IX[indexer]
        PG[(PostgreSQL)]
        RD[(Redis)]
    end
    PP[PayPal Sandbox]
    WEB --> GW
    GW --> GD
    GW --> CH
    GW --> PP
    PP --> GW
    CH --> IX --> PG
    GW --> PG
    GW --> RD
```

## 3. Use cases

```mermaid
flowchart LR
    U((User))
    A((Arbiter))
    P((PayPal))
    subgraph AURIX system
        UC1([Create a wallet])
        UC2([Check balance])
        UC3([Send AUR])
        UC4([Create an escrow])
        UC5([Release an escrow])
        UC6([Refund at deadline])
        UC7([Pay in USD via PayPal])
        UC8([Get a risk score])
        UC9([Browse the explorer])
        UC10([Receive a webhook])
    end
    U --> UC1 & UC2 & UC3 & UC4 & UC6 & UC7 & UC9
    A --> UC5
    P --> UC10
    UC3 -. include .-> UC8
    UC7 -. include .-> UC8
```

## 4. Sequence — AUR transfer

```mermaid
sequenceDiagram
    actor U as User
    participant W as web
    participant G as gateway
    participant N as guardian
    participant C as chain
    participant I as indexer
    U->>W: Enters recipient + amount
    W->>G: POST /api/transfers (draft)
    G->>N: POST /v1/risk/score
    alt Guardian available
        N-->>G: score + reasons
    else unavailable
        G-->>G: "analysis unavailable"
    end
    G-->>W: Alert
    U->>W: Confirms and signs (local key)
    W->>G: Signed MsgSend
    G->>C: Broadcast Tx
    C->>C: CheckTx (signature, nonce, balance) then consensus
    C-->>G: Tx included (height h)
    C-->>I: Block event
    I->>I: Writes to PostgreSQL
    G-->>W: Confirmation
```

## 5. Sequence — PayPal payment + proof

```mermaid
sequenceDiagram
    actor U as User
    participant G as gateway
    participant N as guardian
    participant P as PayPal Sandbox
    participant C as chain
    participant D as PostgreSQL
    U->>G: POST /api/paypal/orders
    G->>P: Create Order (USD)
    G->>N: Score (context = paypal_order)
    N-->>G: score + reasons
    G-->>U: Order + alert
    U->>G: POST /orders/{id}/approve
    G->>P: Capture
    P-->>G: Webhook PAYMENT.CAPTURE.COMPLETED
    G->>P: Verify webhook signature
    alt invalid signature
        G-->>P: 400 rejected
    else event_id already seen
        G-->>P: 200 ignored
    else new event
        G->>D: INSERT webhook_events
        G->>C: MsgAnchorProof(hash(order_id, capture_id))
        C-->>G: Block confirmed
        G-->>P: 200
    end
```

## 6. Sequence — Smart Money

```mermaid
sequenceDiagram
    actor S as Sender
    actor A as Arbiter
    actor R as Recipient
    participant C as chain
    S->>C: MsgCreateEscrow(R, A, amount, deadline)
    C-->>S: Escrow #id LOCKED
    alt condition met
        A->>C: MsgReleaseEscrow(#id)
        C-->>R: Funds credited (RELEASED)
    else deadline passed
        S->>C: MsgRefundEscrow(#id)
        C-->>S: Funds returned (REFUNDED)
    end
```

## 7. State diagrams

Escrow:

```mermaid
stateDiagram-v2
    [*] --> LOCKED: MsgCreateEscrow
    LOCKED --> RELEASED: MsgReleaseEscrow
    LOCKED --> REFUNDED: MsgRefundEscrow (height ≥ deadline)
    RELEASED --> [*]
    REFUNDED --> [*]
```

PayPal order: see [services.md](services.md#paypal-order-lifecycle).

## 8. Class diagram — gateway

```mermaid
classDiagram
    class PaymentService {
        +createOrder(amountUsd) Order
        +approve(orderId) Order
    }
    class WebhookHandler {
        +handle(headers, body) HttpStatus
    }
    class TransferService {
        +prepare(draft) RiskAssessment
        +submit(signedTx) TxResult
    }
    class EscrowService {
        +create(req) TxResult
        +release(id) TxResult
        +refund(id) TxResult
    }
    class RiskClient {
        +score(ctx) RiskAssessment
    }
    class PayPalClient {
        +createOrder()
        +capture()
        +verifySignature()
    }
    class ChainClient {
        +broadcast(tx) TxResult
        +balance(addr) Coin
    }
    class IdempotencyStore {
        +seen(eventId) bool
        +record(eventId)
    }
    class RiskAssessment {
        +float riskScore
        +string level
        +Reason[] reasons
        +bool advisoryOnly
    }
    PaymentService --> PayPalClient
    PaymentService --> RiskClient
    TransferService --> RiskClient
    TransferService --> ChainClient
    EscrowService --> ChainClient
    WebhookHandler --> PayPalClient
    WebhookHandler --> IdempotencyStore
    WebhookHandler --> ChainClient
    RiskClient --> RiskAssessment
```

## 9. Trust boundaries

```mermaid
flowchart LR
    subgraph TRUST[Monetary authority]
        L1[chain]
    end
    subgraph ADVISORY[Advisory]
        L3[guardian]
    end
    subgraph UNTRUSTED[Untrusted by default]
        L4[PayPal / webhooks]
        IDX[PostgreSQL]
    end
    L3 -. advice .-> USER((User))
    USER -->|signs| L1
    L4 -->|signature verified| GW[gateway]
    GW -->|proof only| L1
    L1 --> IDX
```

## 10. Deployment

```mermaid
flowchart TB
    subgraph DEV[Development — Docker Compose]
        d1[chain] & d2[gateway] & d3[guardian] & d4[indexer] & d5[web] & d6[(postgres)] & d7[(redis)]
    end
    subgraph DEMO[Demo — Render]
        r1[web static site]
        r2[gateway web service]
        r3[guardian web service]
        r4[chain persistent Docker service + disk]
        r5[(Render PostgreSQL)]
    end
    subgraph TARGET[Target — Kubernetes]
        k1[Deployments + validator StatefulSet]
    end
    DEV --> DEMO --> TARGET
```
