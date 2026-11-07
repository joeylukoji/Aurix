# Diagrammes

## 1. Diagramme de contexte (C4 niveau 1)

```mermaid
flowchart TB
    USER((Utilisateur<br/>acheteur / vendeur))
    ARB((Arbitre))
    JURY((Jury / auditeur))
    AURIX[Système AURIX<br/>protocole + services]
    PAYPAL[PayPal Sandbox<br/>Orders API + Webhooks]
    USER -->|wallet, paiements| AURIX
    ARB -->|libère les séquestres| AURIX
    JURY -->|consulte l'explorateur| AURIX
    AURIX <-->|commandes USD, webhooks| PAYPAL
```

## 2. Diagramme de conteneurs (C4 niveau 2)

```mermaid
flowchart TB
    subgraph AURIX[Système AURIX]
        WEB[web<br/>Wallet + explorateur]
        GW[gateway<br/>orchestration REST]
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

## 3. Cas d'utilisation

```mermaid
flowchart LR
    U((Utilisateur))
    A((Arbitre))
    P((PayPal))
    subgraph Système AURIX
        UC1([Créer un wallet])
        UC2([Consulter le solde])
        UC3([Envoyer des AUR])
        UC4([Créer un séquestre])
        UC5([Libérer un séquestre])
        UC6([Rembourser à l'échéance])
        UC7([Payer en USD via PayPal])
        UC8([Obtenir un score de risque])
        UC9([Consulter l'explorateur])
        UC10([Recevoir un webhook])
    end
    U --> UC1 & UC2 & UC3 & UC4 & UC6 & UC7 & UC9
    A --> UC5
    P --> UC10
    UC3 -. include .-> UC8
    UC7 -. include .-> UC8
```

## 4. Séquence — transfert AUR

```mermaid
sequenceDiagram
    actor U as Utilisateur
    participant W as web
    participant G as gateway
    participant N as guardian
    participant C as chain
    participant I as indexer
    U->>W: Saisit destinataire + montant
    W->>G: POST /api/transfers (brouillon)
    G->>N: POST /v1/risk/score
    alt Guardian disponible
        N-->>G: score + motifs
    else indisponible
        G-->>G: « analyse indisponible »
    end
    G-->>W: Alerte
    U->>W: Confirme et signe (clé locale)
    W->>G: MsgSend signé
    G->>C: Broadcast Tx
    C->>C: CheckTx (signature, nonce, solde) puis consensus
    C-->>G: Tx incluse (hauteur h)
    C-->>I: Événement de bloc
    I->>I: Écrit dans PostgreSQL
    G-->>W: Confirmation
```

## 5. Séquence — paiement PayPal + preuve

```mermaid
sequenceDiagram
    actor U as Utilisateur
    participant G as gateway
    participant N as guardian
    participant P as PayPal Sandbox
    participant C as chain
    participant D as PostgreSQL
    U->>G: POST /api/paypal/orders
    G->>P: Create Order (USD)
    G->>N: Score (context = paypal_order)
    N-->>G: score + motifs
    G-->>U: Commande + alerte
    U->>G: POST /orders/{id}/approve
    G->>P: Capture
    P-->>G: Webhook PAYMENT.CAPTURE.COMPLETED
    G->>P: Verify webhook signature
    alt signature invalide
        G-->>P: 400 rejeté
    else event_id déjà vu
        G-->>P: 200 ignoré
    else nouveau
        G->>D: INSERT webhook_events
        G->>C: MsgAnchorProof(hash(order_id, capture_id))
        C-->>G: Bloc confirmé
        G-->>P: 200
    end
```

## 6. Séquence — Smart Money

```mermaid
sequenceDiagram
    actor S as Émetteur
    actor A as Arbitre
    actor R as Bénéficiaire
    participant C as chain
    S->>C: MsgCreateEscrow(R, A, montant, échéance)
    C-->>S: Escrow #id LOCKED
    alt condition remplie
        A->>C: MsgReleaseEscrow(#id)
        C-->>R: Fonds crédités (RELEASED)
    else échéance dépassée
        S->>C: MsgRefundEscrow(#id)
        C-->>S: Fonds rendus (REFUNDED)
    end
```

## 7. Diagrammes d'états

Séquestre :

```mermaid
stateDiagram-v2
    [*] --> LOCKED: MsgCreateEscrow
    LOCKED --> RELEASED: MsgReleaseEscrow
    LOCKED --> REFUNDED: MsgRefundEscrow (hauteur ≥ échéance)
    RELEASED --> [*]
    REFUNDED --> [*]
```

Commande PayPal : voir [services.md](services.md#cycle-de-vie-dune-commande-paypal).

## 8. Diagramme de classes — gateway

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

## 9. Frontières de confiance

```mermaid
flowchart LR
    subgraph TRUST[Autorité monétaire]
        L1[chain]
    end
    subgraph ADVISORY[Consultatif]
        L3[guardian]
    end
    subgraph UNTRUSTED[Non fiable par défaut]
        L4[PayPal / webhooks]
        IDX[PostgreSQL]
    end
    L3 -. conseil .-> USER((Utilisateur))
    USER -->|signe| L1
    L4 -->|signature vérifiée| GW[gateway]
    GW -->|preuve uniquement| L1
    L1 --> IDX
```

## 10. Déploiement

```mermaid
flowchart TB
    subgraph DEV[Développement — Docker Compose]
        d1[chain] & d2[gateway] & d3[guardian] & d4[indexer] & d5[web] & d6[(postgres)] & d7[(redis)]
    end
    subgraph DEMO[Démo — Render]
        r1[web static site]
        r2[gateway web service]
        r3[guardian web service]
        r4[chain service Docker persistant + disque]
        r5[(Render PostgreSQL)]
    end
    subgraph TARGET[Cible — Kubernetes]
        k1[Deployments + StatefulSet validateurs]
    end
    DEV --> DEMO --> TARGET
```
