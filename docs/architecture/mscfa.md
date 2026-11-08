# AURIX — MSCFA : architecture d'intégration et cahier des charges

**Module :** Multi-Source Cryptographic Financial Attestation (MSCFA)
**Version :** 1.0 (intégration) — basée sur le cahier des charges technique MSCFA v1.0
**Statut :** spécification proposée pour **AURIX V2** (après le hackathon), **non implémentée**
**Date :** 2026-11-08

> **Décision (2026-11-08) : MSCFA est reporté à la V2.** Il ne fait pas partie de la soumission du hackathon ;
> il y est présenté comme prochaine étape. Le découpage en lots du §14 sert de plan pour la V2.
>
> Ce document adapte le cahier des charges MSCFA v1.0 au code AURIX **existant**. Il précise ce qui est
> réutilisé, ce qui est nouveau, les écarts assumés par rapport au cahier initial, et un découpage
> compatible avec l'échéance du hackathon (12 novembre 2026).

---

## 1. Résumé

MSCFA ajoute à AURIX une **couche de preuves multi-sources** : collecter des événements provenant de
plusieurs organisations (PayPal, transporteur, chaîne AURIX…), vérifier leur provenance, les normaliser
dans un format commun signé (Ed25519), les relier à une même opération, détecter les incohérences, et
exporter un **dossier de preuve portable** vérifiable par un tiers **sans accès au compte PayPal ni à
la base AURIX**.

Trois notions restent séparées partout (API, dossier, interface) :

| Notion | Question | Ce qui la garantit |
| --- | --- | --- |
| **Authenticité** | La donnée vient-elle de la source annoncée ? | Vérification propre au fournisseur (signature webhook PayPal, clé de la source) |
| **Intégrité** | A-t-elle été modifiée depuis ? | SHA-256 sur JSON canonique (RFC 8785) + signature Ed25519 AURIX |
| **Véracité** | L'événement est-il vrai dans le monde réel ? | **Jamais garantie** par la cryptographie ; seulement corroborée par plusieurs sources |

---

## 2. Ce qui existe déjà dans AURIX (analyse du graphe Graphify)

MSCFA n'est pas construit de zéro : une partie de la chaîne de preuve existe pour PayPal.

| Brique existante | Emplacement | Rôle actuel | Réutilisation par MSCFA |
| --- | --- | --- | --- |
| Connecteur PayPal | `payments/aurix_payments/main.py` (`webhook()`, `handle_event()`) | Orders v2, **vérification de signature des webhooks**, idempotence par `event id`, approbation explicite | **Source n°1** : `payments` reste le seul à parler à PayPal ; il transmet à MSCFA les événements **déjà vérifiés** + le hash du payload brut. Pas de second connecteur PayPal. |
| Ancrage on-chain | `chain/x/smartmoney` : `MsgAnchorProof`, `PaymentProof` (`hash = sha256(order_id\|capture_id)`, `amount`, `currency`, `attestations[]`, `status`) | Preuve de paiement ancrée et **attestée k-sur-n** | **Source n°2 (réelle, indépendante)** : la preuve on-chain confirmée devient une attestation `aurix_chain` dans le dossier. |
| Attestor | `attestor/attestor/service.py` | Démon indépendant : re-vérifie chaque capture chez PayPal puis atteste/refuse | Fournit l'indépendance de la source n°2 (opérateurs distincts du service `payments`). |
| Guardian | `guardian/`, appelé par `payments` (`context="paypal_order"`) | Score de risque consultatif signé par quorum | Optionnel : le score signé peut être joint comme attestation de type `risk.assessment` (consultative, jamais une preuve). |
| Bibliothèque commune | `common/aurix_common` (`crypto.py`, `keys.py`, `clients.py`) | Clés secp256k1 (chaîne), clients HTTP | Clients et chargement de secrets réutilisés ; **clés MSCFA séparées** (Ed25519, autre usage). |
| PostgreSQL | `deploy/docker-compose.yml` (service `postgres`, utilisé par `indexer`) | Base de l'indexer | Même instance, **base/schéma dédié `mscfa`**. |
| Wallet web | `web/` | Explorateur AG Grid, signature navigateur | Nouvelles vues : timeline, centre de preuves, page de vérification. |

Conséquence : pour la démonstration, AURIX peut montrer un dossier à **trois sources** dont deux
réelles (PayPal Sandbox vérifié + chaîne AURIX attestée k-sur-n) et une simulée (livraison).

---

## 3. Architecture cible

### 3.1. Vue d'ensemble

```mermaid
flowchart LR
    PP[PayPal Sandbox] -- webhook signé --> PAY[payments :8100<br/>vérifie la signature PayPal]
    PAY -- événement vérifié<br/>+ raw_payload_hash --> EV
    PAY -- MsgAnchorProof --> CHAIN[(Chaîne AURIX<br/>x/smartmoney)]
    ATT[attestor(s)] -- attestation k-sur-n --> CHAIN
    DEL[delivery-sim :8095<br/>SIMULÉ, clé démo Ed25519] -- événement signé --> EV
    CHAIN -- PaymentProof CONFIRMED --> EV

    subgraph EV[evidence :8110 — service MSCFA]
      CM[Connector Manager] --> SV[Source Verification]
      SV --> NE[Normalization]
      NE --> CE[Crypto Engine<br/>JCS + SHA-256 + Ed25519]
      CE --> REG[(Attestation Registry<br/>PostgreSQL schéma mscfa)]
      REG --> AGG[Evidence Aggregator]
      AGG --> CON[Consistency Engine]
      CON --> PKG[Proof Package builder]
    end

    PKG -- export .zip --> VER[aurix-verify<br/>vérificateur indépendant<br/>CLI + page web]
    GW[gateway :8080] -- reverse proxy /api/v1/evidence --> EV
    WEB[web wallet] --> GW
    WEB -. vérification locale .-> VER
```

### 3.2. Principe de dépendance

- `evidence` **ne parle jamais directement à PayPal** : il fait confiance à `payments` pour la
  vérification PayPal et l'enregistre comme telle (`verification_method = paypal_webhook_signature`,
  `verified_by = payments`). Un seul composant détient les secrets PayPal.
- `evidence` lit la chaîne en **lecture seule** (REST `:1317`, requête `proof`), sans clé de chaîne.
- Le vérificateur `aurix-verify` **n'importe aucun code serveur** et ne fait **aucun appel réseau**
  par défaut (mode hors ligne) ; un mode `--online` optionnel peut consulter la liste de révocation.
- La chaîne et la monnaie AUR **ne sont pas requises** pour MSCFA : sans chaîne, le dossier perd
  seulement la source `aurix_chain` (conforme au §2.2 du cahier initial : MSCFA n'ajoute pas de blockchain).

### 3.3. Flux de bout en bout (scénario de démo)

```mermaid
sequenceDiagram
    participant U as Acheteur (web)
    participant P as payments
    participant PP as PayPal Sandbox
    participant E as evidence (MSCFA)
    participant C as Chaîne AURIX
    participant D as delivery-sim
    participant V as aurix-verify

    U->>P: créer commande (op_id AURIX)
    P->>E: POST /operations {op_id, order_id, montant, devise}
    U->>PP: approuver + capturer
    PP->>P: webhook PAYMENT.CAPTURE.COMPLETED (signé)
    P->>P: vérifie signature PayPal + idempotence
    P->>E: POST /internal/events (événement vérifié, raw_payload_hash)
    E->>E: normalise, signe attestation paypal
    P->>C: MsgAnchorProof ; attestors confirment k-sur-n
    E->>C: lit PaymentProof CONFIRMED → attestation aurix_chain
    D->>E: shipment.delivered (signé clé démo, source_type=simulated)
    E->>E: relie par order_id, contrôles de cohérence
    U->>E: POST /operations/{id}/consolidate → GET /proofs/{id}/export
    U->>V: aurix-verify proof.zip
    V-->>U: rapport : intégrité, signatures, sources réelles/simulées, limites
```

---

## 4. Écarts assumés par rapport au cahier des charges v1.0

| Point du cahier v1.0 | Décision d'intégration | Justification |
| --- | --- | --- |
| §13 Noyau MSCFA en **Go** | **Python 3.12 + FastAPI**, comme `payments`, `gateway`, `indexer`, `attestor` | Cohérence du dépôt, réutilisation de `aurix_common`, tests pytest existants, échéance à 4 jours. Le format de dossier et le vérificateur restent indépendants du langage ; un portage Go reste possible. |
| §13 **Redis** pour l'idempotence | **Contrainte d'unicité PostgreSQL** `(source_id, provider_event_id)` | Redis est déjà marqué « non nécessaire au MVP » ; l'unicité en base suffit et survit aux redémarrages. |
| §6 Connecteur PayPal dans MSCFA | **Réutilisation de `payments`** comme connecteur | Éviter de dupliquer secrets et vérification de webhook (principe n°1 : ne pas reconstruire l'existant). |
| §7 Une seule deuxième source (livraison simulée) | **Deux sources supplémentaires** : `aurix_chain` (réelle) + `aurix_demo_delivery` (simulée) | La chaîne fournit déjà une attestation indépendante k-sur-n ; cela renforce la démo sans coût. |
| §5.6 Confiance temporelle des clés | **Ancrage optionnel du `manifest_hash`** sur la chaîne (phase post-hackathon, nouveau message `MsgAnchorEvidence`) | Un horodatage BFT prouve qu'une preuve existait avant une révocation ; hors MVP car il faut un nouveau message protobuf. |
| §11 Table `operations.owner_id` | Propriétaire = **adresse AURIX** (`aur1…`) ou identifiant de marchand authentifié | Pas de système de comptes utilisateurs dans AURIX aujourd'hui. |

---

## 5. Exigences fonctionnelles (intégration)

Les identifiants reprennent ceux du cahier v1.0. **H** = indispensable pour le hackathon, **P** = post-hackathon.

| ID | Exigence | Composant | Lot |
| --- | --- | --- | --- |
| MSCFA-01 | Recevoir les événements vérifiés de `payments` (`PAYMENT.CAPTURE.COMPLETED`, `…REFUNDED`, `CHECKOUT.ORDER.APPROVED`) via `POST /internal/events` authentifié | `payments` → `evidence` | H |
| MSCFA-01b | Récupérer la `PaymentProof` on-chain d'une commande et la transformer en attestation quand elle est `CONFIRMED` | `evidence` (lecture chaîne) | H |
| MSCFA-01c | Recevoir les événements signés de `delivery-sim` | `evidence` | H |
| MSCFA-02 | Enregistrer pour chaque attestation la méthode et le résultat de vérification ; **rejeter** toute source non vérifiable (HTTP 422, journalisé) | `evidence` | H |
| MSCFA-03 | Normaliser vers `aurix.financial-attestation` v1.0 (§6) en conservant le statut fournisseur original et le hash du payload brut | `evidence` | H |
| MSCFA-04 | Signer chaque attestation et chaque manifeste en Ed25519 sur `SHA-256(JCS(objet))` | `evidence` | H |
| MSCFA-05 | Relier les attestations par identifiants explicites (`operation_id`, `order_id`, `capture_id`, `delivery_id`) — **jamais** par montant/date proches | `evidence` | H |
| MSCFA-06 | Détecter : montant ≠, devise ≠, commande inconnue, paiement sans livraison, livraison sans paiement, doublon, remboursement après paiement, désordre temporel | `evidence` | H (5 premiers), P (reste) |
| MSCFA-07 | Générer le dossier consolidé (§8), signé | `evidence` | H |
| MSCFA-08 | Vérifier un dossier hors ligne : empreintes, signatures, clés, relations, schéma, révocation | `aurix-verify` | H |
| MSCFA-09 | Exporter le dossier en `.zip` (et JSON unique pour la page web) | `evidence` | H |
| MSCFA-10 | Registre de clés publiques avec rotation et révocation datée | `evidence` + `trust/` du dossier | H (registre + révocation statique), P (rotation automatisée, ancrage) |
| MSCFA-11 | Vues web : détail d'opération, timeline des preuves, page de vérification | `web/` | H (timeline + vérification), P (Attestation Builder) |

---

## 6. Format d'attestation `aurix.financial-attestation` v1.0

Repris du cahier v1.0 §4.2, avec ces précisions d'intégration :

```json
{
  "schema": "aurix.financial-attestation",
  "schema_version": "1.0",
  "attestation_id": "att_01J…",
  "operation_id": "op_01J…",
  "source": {
    "provider": "paypal",
    "source_type": "real",
    "environment": "sandbox",
    "provider_event_id": "WH-…",
    "provider_reference": { "order_id": "5O1…", "capture_id": "3C6…" },
    "verification_method": "paypal_webhook_signature",
    "verified_by": "aurix-payments",
    "verification_status": "verified"
  },
  "event": {
    "type": "payment.captured",
    "provider_status": "COMPLETED",
    "amount": { "value": "1500.00", "currency": "USD" }
  },
  "timestamps": {
    "source_event_time": "2026-11-08T08:00:00Z",
    "aurix_received_at": "2026-11-08T08:00:05Z",
    "attested_at": "2026-11-08T08:00:05Z"
  },
  "provenance": {
    "connector_id": "aurix-payments/1",
    "raw_payload_hash": "sha256:…",
    "raw_payload_ref": "vault://mscfa/raw/…"
  }
}
```

Règles (en plus de celles du §4.3 du cahier v1.0) :

- `amount.value` est une **chaîne décimale** (`Decimal` en Python, `NUMERIC(20,8)` en base). Aucun `float`.
- `provider_status` est copié tel quel. `event.type` est le type AURIX normalisé ; la table de
  correspondance par fournisseur est versionnée dans `evidence/mappings/<provider>.yaml`.
  Exemple : PayPal `PAYMENT.CAPTURE.PENDING` → `payment.capture_pending` (**jamais** `payment.captured`).
- Le bloc de signature **n'est pas** dans l'objet signé ; il est stocké à côté
  (`signatures/<attestation_id>.json`) : `{alg: "Ed25519", key_id, signed_hash, signature, created_at}`.
- Source `aurix_chain` : `verification_method = "aurix_chain_kofn_attestation"`,
  `provider_reference = {proof_hash, confirmed_height, attestors: [...]}`.
- Source `aurix_demo_delivery` : `source_type = "simulated"`, `environment = "demo"`,
  `verification_method = "ed25519_demo_key"`. Ce marquage est **obligatoire** et affiché partout.

---

## 7. Cryptographie et clés

| Fonction | Choix | Bibliothèque |
| --- | --- | --- |
| Canonicalisation | JCS (RFC 8785) | implémentation interne testée sur les vecteurs RFC 8785 (`evidence/jcs.py`, partagée avec le vérificateur) |
| Empreinte | SHA-256 | `hashlib` |
| Signature | Ed25519 | `cryptography` (déjà dépendance des services) ; côté navigateur : WebCrypto `Ed25519` ou `@noble/ed25519` |
| Identifiant de clé | `key_id = "aurix-mscfa-<env>-<n>"` + empreinte `sha256(pubkey)[:16]` | — |

Clés :

| Clé | Usage | Stockage |
| --- | --- | --- |
| `aurix-mscfa-sandbox-1` | Signature des attestations et manifestes AURIX | `MSCFA_SIGNING_KEY_FILE` (fichier 0600, secret Render / Docker secret), **jamais** dans Git |
| `aurix-demo-delivery-1` | Signature des événements du transporteur simulé | `DELIVERY_SIM_KEY_FILE`, distincte de la précédente |
| Jeton interne `payments → evidence` | Authentifier `POST /internal/events` | `MSCFA_INTERNAL_TOKEN` (HMAC du corps + horodatage, fenêtre anti-rejeu 5 min) |

En mode `dev`, une graine déterministe est tolérée avec l'avertissement existant de `aurix_common.keys`
(« dev seed key — never use on a public network ») ; en production, le service **refuse de démarrer** sans fichier de clé.

Registre de confiance (`trust/public-keys.json` dans chaque dossier, et `GET /api/v1/keys`) :

```json
{ "keys": [ { "key_id": "aurix-mscfa-sandbox-1", "alg": "Ed25519", "public_key": "base64…",
    "owner": "AURIX evidence service", "valid_from": "2026-11-08T00:00:00Z", "valid_until": null,
    "revoked_at": null, "revocation_reason": null } ] }
```

Le vérificateur classe chaque signature : `valid`, `invalid`, `unknown_key`, `revoked_key`,
`valid_but_key_revoked_after_signing` (ce dernier statut est **indicatif** tant que l'ancrage
on-chain du manifeste n'existe pas : la date `created_at` seule ne prouve rien, cf. cahier v1.0 §5.6).

AURIX ne signe **jamais** au nom de PayPal : sa signature atteste « AURIX a reçu cet événement et
`payments` l'a vérifié selon la méthode PayPal à telle date ».

---

## 8. Dossier de preuve (Universal Financial Proof Package)

Structure reprise du cahier v1.0 §9.2. Le `manifest.json` liste chaque fichier avec son SHA-256 ;
seul le manifeste est signé par le dossier (signature de `SHA-256(JCS(manifest))`), les attestations
gardent en plus leur signature individuelle.

```text
aurix-proof-<proof_id>.zip
  manifest.json                 # proof_id, operation_id, schema_version, liste fichiers+hash, created_at
  manifest.sig.json             # signature Ed25519 du manifeste
  attestations/{paypal,aurix_chain,delivery}.json
  signatures/<attestation_id>.json
  relations.json                # arêtes typées : same_order, pays_for, delivers, refunds
  consistency.json              # anomalies classées (1 à 5, cahier v1.0 §8.3)
  trust/public-keys.json
  trust/trust-metadata.json     # sources réelles vs simulées, méthodes de vérification
  verification/verification-report.json   # rapport au moment de l'export
```

Minimisation : aucun e-mail, nom, adresse postale ni identifiant de payeur PayPal. Les
références fournisseur sont conservées (nécessaires à la vérification) ; le payload brut **n'est
pas** exporté, seul son hash l'est.

Rapport de vérification : format du cahier v1.0 §9.3, statuts fermés :
`integrity ∈ {valid, invalid}`, `signatures ∈ {valid, invalid, partial}`,
`source_verification ∈ {verified, partial, unverified}`, `consistency ∈ {consistent, inconsistent, incomplete}`,
plus une liste `limitations` **toujours** non vide (au minimum : « La validité cryptographique ne prouve pas la livraison physique »).
Les formulations « transaction garantie » et « paiement irrévocable » sont interdites (test automatisé sur les libellés).

---

## 9. API

Service `evidence` (port 8110), exposé au wallet via le reverse proxy du `gateway` sous `/api/v1/evidence/…`.

| Méthode | Endpoint | Accès | Fonction |
| --- | --- | --- | --- |
| POST | `/internal/events` | `payments` (HMAC) | Événement PayPal vérifié |
| POST | `/api/v1/operations` | `payments` (HMAC) ou marchand authentifié | Créer une opération (`order_id`, montant, devise attendus) |
| POST | `/api/v1/attestations` | source enregistrée (signature Ed25519 de la source) | Déposer une attestation externe (delivery-sim) |
| GET | `/api/v1/attestations/{id}` | propriétaire | Consulter |
| GET | `/api/v1/operations/{id}/evidence` | propriétaire | Attestations, relations, anomalies, timeline |
| POST | `/api/v1/operations/{id}/consolidate` | propriétaire | Construire / reconstruire le dossier (nouvelle version, l'ancienne est conservée) |
| GET | `/api/v1/proofs/{id}` | propriétaire | Métadonnées du dossier |
| GET | `/api/v1/proofs/{id}/export` | propriétaire | `.zip` (ou `?format=json`) |
| POST | `/api/v1/proofs/verify` | public, limité en débit (10/min/IP), taille max 2 Mo | Vérification serveur (même code que le CLI) |
| GET | `/api/v1/keys` | public | Registre de clés et révocations |

Authentification « propriétaire » pour le MVP : signature d'un défi par la clé secp256k1 du wallet
AURIX (déjà générée dans le navigateur), vérifiée par `evidence`. La spécification OpenAPI sera
ajoutée à `api/` comme pour le gateway.

---

## 10. Modèle de données (PostgreSQL, schéma `mscfa`)

Tables du cahier v1.0 §11, avec ces ajouts :

- `attestations` : `UNIQUE (source_id, provider_event_id)` (idempotence) ; `provider_status` séparé de
  `event_type` ; `raw_payload_hash` ; `raw_payload_enc BYTEA` (chiffré AES-GCM, clé `MSCFA_DATA_KEY`,
  purge après 90 jours).
- `signatures` : une ligne par signature (attestation ou manifeste).
- `proofs` : `version INT`, `supersedes_proof_id` — un dossier n'est jamais modifié, il est remplacé.
- `evidence_relations` : `relation_type ∈ {same_order, pays_for, delivers, refunds, anchors}`,
  `basis` (identifiant commun ayant justifié le lien) — **obligatoire**, pas de champ `confidence` heuristique au MVP.
- `anomalies` : `operation_id, class (1..5), code, details, detected_at, resolved_by_attestation_id`.
- `keys` : registre (cf. §7). `audit_logs` : sans données personnelles ni secrets.

Migrations versionnées (`evidence/migrations/`), appliquées au démarrage.

---

## 11. Nouveaux composants dans le dépôt

```text
evidence/                       # service MSCFA (FastAPI)
  aurix_evidence/{main,config,db,jcs,crypto,normalize,aggregate,consistency,package}.py
  mappings/paypal.yaml
  migrations/
  tests/
verifier/                       # vérificateur indépendant, zéro dépendance serveur
  aurix_verify/{cli,jcs,verify}.py   # `python -m aurix_verify proof.zip`
  tests/vectors/                # dossiers valides, altérés, clé inconnue, clé révoquée
delivery-sim/                   # transporteur SIMULÉ (FastAPI, clé démo distincte)
web/proof.html, web/proof.js    # timeline + page de vérification (JS, hors ligne)
```

Modifications de l'existant :

| Fichier | Changement |
| --- | --- |
| `payments/aurix_payments/main.py` | Après `handle_event()` réussi : `POST /internal/events` vers `evidence` (asynchrone, file de réessai en base comme `retry_unanchored()`) ; `POST /api/v1/operations` à la création de commande. **Désactivable** (`MSCFA_URL` vide) : `payments` fonctionne sans MSCFA. |
| `gateway/app/main.py` | Reverse proxy `/api/v1/evidence/*` (même mécanisme que pour `payments` et `indexer`). |
| `deploy/docker-compose.yml`, `deploy/render.yaml` | Services `evidence`, `delivery-sim` ; base `mscfa` sur le Postgres existant ; secrets. |
| `scripts/dev-up.sh`, `scripts/test-all.sh` | Lancement et tests des nouveaux services. |
| `tests/e2e/` | `evidence-journey.js` : scénario §3.3 + preuve altérée rejetée. |

`verifier/` duplique volontairement `jcs.py` (quelques dizaines de lignes) au lieu d'importer
`evidence` : il doit rester utilisable seul par un tiers. Un test vérifie que les deux copies sont identiques.

---

## 12. Sécurité (complément au cahier v1.0 §12)

| Menace | Mesure dans AURIX |
| --- | --- |
| Webhook PayPal falsifié | Vérifié par `payments` (existant) ; `evidence` n'accepte des événements PayPal que via `/internal/events` signé HMAC |
| Rejeu interne | HMAC sur `timestamp + body`, fenêtre 5 min, unicité `provider_event_id` |
| Source substituée | Chaque source a un `source_id` et une clé enregistrée ; une clé inconnue → 422 |
| Attestation modifiée | Hash + signature par attestation et par manifeste |
| Fausse relation | Relations uniquement par identifiant commun, `basis` obligatoire |
| Clé compromise | Révocation datée dans le registre ; ancrage on-chain en P |
| Fuite de données personnelles | Minimisation à l'export, payload brut chiffré et purgé, journaux sans PII |
| Accès à la preuve d'autrui | Contrôle propriétaire sur toutes les routes non publiques ; test dédié |
| Abus du vérificateur public | Limite de débit et de taille ; aucune donnée persistée |

---

## 13. Tests et mesures

Reprennent intégralement le cahier v1.0 §14. Spécifiques à l'intégration :

- **Unitaires** : vecteurs RFC 8785 ; `Decimal` de bout en bout ; mapping PayPal (dont `PENDING`) ; idempotence.
- **Intégration** : `payments` (simulateur puis Sandbox) → `evidence` ; lecture d'une `PaymentProof` confirmée ;
  delivery-sim ; export ; `aurix-verify` sur l'export.
- **Sécurité** : les 10 scénarios du cahier v1.0 §14.3, chacun avec un dossier de test dans `verifier/tests/vectors/`.
- **Cohérence** : les 7 scénarios du §14.4, scriptés dans `delivery-sim` (`/scenario/<nom>`).
- **Mesures** : latence de consolidation et de vérification, taux de rejet des preuves altérées (mutation
  aléatoire d'un octet sur N dossiers), faux positifs/négatifs du moteur de cohérence sur le jeu de
  scénarios. Résultats publiés dans `research/` **uniquement s'ils ont été mesurés**.

---

## 14. Planning V2

Le cahier v1.0 estime 5 à 10 jours. Il reste **4 jours** avant la soumission, et d'autres tâches
critiques sont ouvertes (identifiants PayPal Sandbox, déploiement Render, vidéo, Devpost). D'où deux lots :

### Lot H — premier incrément V2 (≈ 2,5 jours)

*Initialement prévu pour le hackathon ; reporté en V2. Les dates ci-dessous sont indicatives.*

| Jour | Contenu | Critère de sortie |
| --- | --- | --- |
| J1 (9 nov.) | `evidence` : schéma, JCS, Ed25519, registre de clés, `/internal/events`, branchement `payments` | Une capture PayPal (simulateur) produit une attestation signée |
| J2 (10 nov.) | Source `aurix_chain`, `delivery-sim`, liaison par `order_id`, 5 contrôles de cohérence, export `.zip` | Dossier à 3 sources exporté |
| J2,5 (11 nov. matin) | `aurix-verify` (CLI + page web), vecteurs valide/altéré/clé inconnue/clé révoquée, e2e | Preuve valide acceptée, preuve altérée rejetée, source simulée signalée |

À couper en premier si retard : page web de vérification (garder le CLI), contrôles de cohérence 6-8,
chiffrement du payload brut (ne pas le stocker du tout à la place).

### Lot P — suite de la V2

Attestation Builder, rotation automatisée des clés, `MsgAnchorEvidence` (horodatage BFT des manifestes),
contrôles de cohérence restants, portage Go éventuel, transporteur réel, divulgation sélective.

---

## 15. Critères d'acceptation

Ceux du cahier v1.0 §17, plus :

- [ ] `payments` fonctionne à l'identique quand `MSCFA_URL` est vide (non-régression `scripts/test-all.sh`).
- [ ] Une attestation `aurix_chain` n'est créée que pour une `PaymentProof` au statut `CONFIRMED`.
- [ ] `aurix-verify` vérifie un dossier sans réseau et sans accès à la base.
- [ ] Toute attestation `simulated` apparaît comme telle dans le rapport, la timeline et l'export.
- [ ] Aucun secret ni donnée personnelle dans les dossiers exportés (test automatisé de scan).
- [ ] Les libellés interdits (« garanti », « irrévocable ») sont absents de l'interface et des rapports (test).

---

## 16. Limites

Celles du cahier v1.0 §18 s'appliquent intégralement. En plus :

- La source `aurix_chain` est attestée par des attestors aujourd'hui opérés par la même équipe sur une
  seule machine : c'est une indépendance **technique** (re-vérification séparée chez PayPal), pas
  encore organisationnelle (voir `docs/planning/decentralization.md`).
- Sans ancrage du manifeste, la confiance temporelle des clés repose sur le registre AURIX lui-même.
- La source de livraison est simulée : elle démontre le mécanisme, pas une intégration transporteur.
