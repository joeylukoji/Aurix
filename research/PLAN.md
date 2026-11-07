# AURIX : plan d'implémentation scientifique (de 6,8/10 vers 9,5/10)

Statut : **plan, rien n'est encore exécuté.** Il traite les 15 points du diagnostic
« AURIX MVP 0.1 — Diagnostic scientifique et plan de progression vers 9,5/10 ».

Principes de travail :

- **Pas de nouvelle fonctionnalité tant qu'une affirmation n'est pas mesurée.** La note monte quand une preuve existe, est reproductible et est interprétée honnêtement, pas quand un fichier est ajouté.
- **Chaque affirmation de l'article est rattachée à un artefact :** un test, un script d'expérience, ou un résultat brut versionné dans `research/results/raw/`. Sinon, elle est formulée comme hypothèse ou perspective.
- **Une expérience = une commande.** Configuration versionnée, graine fixée, sorties brutes conservées et figures régénérées par script.

## 0. Où en est le code (point de départ honnête)

| Élément | État au 7 nov. | Remarque scientifique |
| --- | --- | --- |
| Chaîne Cosmos SDK, `x/smartmoney`, k-of-n, gouvernance, slashing | implémenté, testé | Tests unitaires et scripts de scénarios, mais pas encore de tests de propriétés |
| Neural Guardian (LightGBM + SHAP, PaySim) | implémenté | **Les seuils 0,1 et 0,5 ont été choisis sur le jeu de test** (point 3, confirmé) |
| Passerelle, payments (PayPal), indexeur PostgreSQL | implémenté | Le découpage en services vient d'être fait (cette version) |
| PayPal | **simulé** (`paypal-sim/`) | Aucun test sur le vrai Sandbox |
| Décentralisation | 4 validateurs, **une seule machine, un seul opérateur** | Paquet multi-opérateurs prêt, jamais lancé |
| Mesures de performance de bout en bout | **aucune** | |
| Baselines et ablations | **aucune** | |
| Modèle de menace écrit | partiel (README, `docs/planning/decentralization.md`) | Pas de matrice menace, défense et test |

## 1. Organisation du dépôt (à créer en premier, J1)

```
research/
├── PLAN.md                      ← ce fichier
├── questions-hypotheses/        research-questions.md, hypotheses.md, contributions.md
├── methodology/                 system-model.md, assumptions.md, threat-model.md, invariants.md
├── related-work/                literature.md (tableau comparatif), bibliography.bib
├── experiments/
│   ├── ml-evaluation/           protocole, config YAML, script, README
│   ├── attestation/             matrice k/n, scénarios d'attaque
│   ├── consensus/               pannes, partitions, quorum, topologie isolée
│   ├── performance/             charge de bout en bout B0 à B4
│   └── ablations/               configurations B0 à B4
├── datasets/                    README (provenance, SHA-256, licence) ; aucune donnée lourde versionnée
├── results/raw/                 sorties brutes horodatées (JSON/CSV), jamais éditées à la main
├── results/processed/           agrégats produits par script
├── figures/                     figures régénérées par script (`make figures`)
└── reproducibility/             environment.md, run-all.sh, experiment-configs/, lockfiles
papers/                          sources LaTeX de l'article (template IEEE/ACM)
```

Conventions communes à toutes les expériences :

- **Config :** `research/reproducibility/experiment-configs/<id>.yaml`, avec la graine, les versions et les paramètres.
- **Sortie :** `research/results/raw/<id>/<horodatage>-<sha git>/` contient `config.yaml`, `env.json` (machine, OS, versions) et les mesures.
- **Exécution :** `research/reproducibility/run-all.sh [id…]` lance une ou toutes les expériences, puis `make figures`.
- **Synthèse :** un fichier `RESULTS.md` par expérience, qui renvoie au dossier brut exact utilisé.

---

## Phase 1 : fondations scientifiques (points 1, 2, 8, 9, 12, 15) — semaines 1 et 2

### Étape 1.1 : état de l'art et contribution (point 1)

1. **Corpus.** Recherche dans Google Scholar, IEEE Xplore et ACM DL, mots-clés : *conditional payments / escrow smart contracts*, *threshold attestation / oracles* (Chainlink OCR, DECO, Town Crier), *payment proof anchoring*, *hybrid on-chain/off-chain payment orchestration*, *ML fraud detection on blockchain*, *CBDC programmable money*. Viser 25 à 40 références, en commençant par Chainlink OCR, Town Crier, DECO, les travaux sur les oracles TEE, Lightning/HTLC, l'escrow dans Cosmos/Ethereum, les modèles de fraude PaySim et IEEE-CIS, et Chiu & Monnet (programmable money).
2. **Tableau comparatif** (`related-work/literature.md`), une ligne par système, avec ces colonnes :
   - exécution conditionnelle déterministe ;
   - preuve de paiement externe ;
   - modèle de confiance des oracles (1 / k-of-n / TEE / zk) ;
   - IA dans la boucle ;
   - rôle de l'IA (bloquant / consultatif) ;
   - vérifiabilité a posteriori ;
   - évaluation publiée.
3. **Énoncé de contribution** (`contributions.md`). Au plus 3 contributions, chacune falsifiable et rattachée à une expérience :
   - **C1** : architecture qui sépare explicitement *exécution déterministe* (chaîne), *attestation k-of-n de faits externes* et *évaluation consultative du risque* (jamais bloquante, signée et vérifiable).
   - **C2** : protocole d'ancrage de preuves de paiement externes à seuil, avec vérification indépendante par chaque attestateur, plus l'analyse de ses garanties sous un modèle de menace explicite.
   - **C3** : évaluation expérimentale (ablations B0 à B4) du coût et du bénéfice de chaque couche.
4. **Critère d'acceptation :** un relecteur peut dire, pour chaque contribution, quelle expérience la démontre et quel travail antérieur s'en rapproche le plus.

### Étape 1.2 : questions de recherche et hypothèses (point 2)

`research-questions.md` et `hypotheses.md` reprennent RQ1 à RQ3 et H1 à H3 du diagnostic, rendus **opérationnels** : chaque hypothèse a une métrique, un seuil de réfutation et une expérience.

| Hyp. | Métrique | Réfutée si | Expérience |
| --- | --- | --- | --- |
| H1 (seuil ⇒ vérifiabilité) | taux d'acceptation d'une preuve falsifiée selon le nombre f d'attestateurs compromis, pour k et n donnés | une preuve fausse est acceptée avec f < k | `attestation/` (étape 3.3) |
| H2 (ancrage ⇒ détection d'altération) | part des altérations détectées (montant, devise, référence, rejeu), comparée à un journal applicatif SQLite signé ou non | taux de détection ≤ celui de la baseline B1 | `ablations/` B1 et B3 |
| H3 (ML utile et acceptable) | PR-AUC, rappel à taux de faux positifs fixé (1 %), surcoût de latence p95 | pas de gain significatif sur la baseline à règles (IC 95 %), ou surcoût p95 > budget fixé (ex. 50 ms) | `ml-evaluation/`, `performance/` |

Chaque hypothèse précise son *budget* (seuils de latence, taux de faux positifs) **avant** l'expérience, par pré-enregistrement dans le fichier, daté et commité.

### Étape 1.3 : modèle système et hypothèses de confiance (points 1 et 15)

`system-model.md` :
- **Acteurs :** payeur, bénéficiaire, arbitre, validateurs, attestateurs, Guardians, passerelle, payments, PayPal.
- **Canaux :** P2P CometBFT, REST, webhooks.
- **Hypothèse de synchronie :** partielle, celle de CometBFT.
- **Hypothèses de confiance :** moins d'1/3 des votes byzantins ; moins de k attestateurs corrompus ; PayPal honnête sur ses propres données ; Guardians non fiables (consultatifs) ; passerelle non fiable pour les fonds.

`assumptions.md` : ce qui est **hors périmètre** (vie privée on-chain, régulation, MEV, sécurité du navigateur).

### Étape 1.4 : modèle de menace (point 8)

`threat-model.md` contient une matrice **menace → capacité de l'attaquant → propriété protégée → défense → test**. Chaque ligne pointe vers un test existant ou à écrire.

| # | Menace | Défense actuelle | Test existant | À écrire |
| --- | --- | --- | --- | --- |
| T1 | Faux paiement (preuve forgée) | k-of-n + chaque attestateur revérifie la capture chez PayPal | `scripts/attest-test.sh` (preuve forgée reste PENDING) | variation de f et de k (étape 3.3) |
| T2 | Rejeu de webhook | idempotence par event id + signature | tests payments (`duplicate`) | rejeu après redémarrage du service, rejeu croisé entre commandes |
| T3 | Altération montant ou devise | montant et devise dans `MsgAnchorProof`, `ErrProofMismatch` | `scripts/harden-test.sh` | fuzzing des champs |
| T4 | Attestateur compromis | seuil k | partiel | k−1 compromis : aucune confirmation |
| T5 | Collusion de k attestateurs | **aucune** (hors modèle) | — | démontrer l'échec attendu et le documenter comme limite |
| T6 | Vol de clé | clés séparées, seeds refusées en production, gouvernance pour révoquer un signataire | `gov-test.sh` | rotation de signataire par proposition, puis l'ancienne clé est refusée |
| T7 | Double libération d'escrow | machine à états | tests keeper | tests de propriétés (étape 1.5) |
| T8 | Déni de service (Guardian, indexeur, PayPal en panne) | IA consultative, repli sur la chaîne, 503 propres | `gateway-off.js`, `test_indexer_down_*` | injection de pannes pendant la charge (étape 4.2) |
| T9 | Désynchronisation PayPal et chaîne | relance des CONFIRMED non ancrées, expiration des PENDING | tests de relance (payments) | scénarios chaos (PayPal capture, mais pas de webhook pendant N min) |
| T10 | Validateur malveillant (double signature) | slashing + evidence | `scripts/slashing-test.sh` (absence seulement) | double signature : preuve et jailing |

### Étape 1.5 : invariants formalisés (point 9)

`invariants.md` liste I1 à I8 avec leur énoncé formel, l'endroit du code qui les garantit (`file:line`) et le test qui les vérifie :

- **I1 :** une libération au plus par escrow. **I2 :** montant libéré ou remboursé = montant verrouillé.
- **I3 :** pas de remboursement après libération, et inversement. **I4 :** Σ escrows LOCKED = solde du compte module.
- **I5 :** l'offre totale est constante (pas de `x/mint`). **I6 :** une preuve est CONFIRMED ⇔ au moins k signataires autorisés distincts.
- **I7 :** une seule attestation par signataire et par hash, et les champs ne divergent pas. **I8 :** les transitions temporelles (remboursement seulement après échéance, sauf par l'arbitre) sont respectées.

Implémentation : **tests de propriétés** en Go avec `pgregory.net/rapid`. On génère des séquences aléatoires de messages (create, release, refund, anchor, avance de bloc) contre le keeper, puis on vérifie I1 à I8 après chaque pas. Cible : 10⁴ séquences par invariant en CI, plus une campagne longue de 10⁶ séquences hors CI. Optionnel : une spécification TLA+ de la machine à états escrow + preuve (environ 150 lignes), vérifiée par TLC.

**Acceptation :** `invariants.md` contient un tableau invariant → code → test. Les tests de propriétés passent en CI, et un bug volontairement injecté (mutation) est détecté.

### Étape 1.6 : documentation alignée (point 12)

- Tableau de statut de chaque composant (`implemented` / `simulated` / `planned` / `experimental`) dans le README et dans `research/methodology/system-model.md`.
- Corriger `AURIX_SOFTWARE_ARCHITECTURE.md` et `AURIX_ARCHITECTURE_LOGICIELLE.md` : la cible (Spring Boot, PyTorch/GNN, Redis) est marquée `planned` ; l'implémenté (FastAPI, LightGBM, SQLite pour payments, PostgreSQL pour l'indexeur) est marqué `implemented`.
- Trois diagrammes distincts : conceptuel, implémenté, futur.

**Livrable de phase 1 :** les 6 fichiers `research/…`, relus par une personne extérieure au projet (étape 5.3).

---

## Phase 2 : fiabiliser Neural Guardian (points 3, 4, 14) — semaines 2 et 3

### Étape 2.1 : protocole train / validation / test (point 3, priorité P0)

1. **Découpage temporel en trois périodes** sur PaySim (744 pas horaires) :

   | Ensemble | Pas | Usage |
   | --- | --- | --- |
   | train | 1–400 | apprentissage |
   | validation | 401–550 | choix des hyperparamètres, du seuil et de la calibration |
   | test | 551–744 | une seule évaluation finale |

   Les bornes sont fixées dans la config **avant** de regarder les résultats, et le test n'est jamais lu pendant le réglage.
2. **Seuils** choisis sur la validation selon une règle annoncée à l'avance, par exemple « seuil *high* = taux de faux positifs ≤ 0,5 % » et « seuil *medium* = rappel ≥ 90 % ». Remplacer les seuils actuels de `guardian/app/model.py`, choisis sur le test.
3. **Calibration** : isotonic ou Platt, ajustée sur la validation. Rapporter le Brier score, l'ECE et la courbe de fiabilité sur le test. Le score exposé devient une probabilité calibrée.
4. **Incertitude** : intervalles de confiance à 95 % par bootstrap stratifié (1 000 rééchantillonnages) pour ROC-AUC, PR-AUC, précision et rappel aux seuils. Ajouter 5 graines d'entraînement différentes et rapporter moyenne ± écart-type.
5. **Baselines** sur les mêmes données et le même protocole :
   - B-rules : règles métier (compte vidé, destinataire nouveau, montant supérieur à x fois la moyenne) ;
   - régression logistique ;
   - Random Forest ;
   - XGBoost ;
   - LightGBM (actuel).
   Comparer les PR-AUC par test de DeLong (AUC) et par bootstrap apparié.
6. **Sorties :**
   - matrices de confusion aux deux seuils ;
   - courbes ROC et Precision-Recall avec IC ;
   - courbe de calibration ;
   - tableau comparatif des baselines ;
   - le tout dans `results/raw/ml-eval-*`.

**Code :** `guardian/training/evaluate.py` (nouveau) et `research/experiments/ml-evaluation/` (config + README). Le modèle livré n'est régénéré qu'à partir de ce protocole, et `models/paysim_metrics.json` contient les métriques **test** plus la provenance des seuils.

**Acceptation :** un script unique reproduit tous les chiffres, et le test n'apparaît que dans l'étape d'évaluation finale. Un test unitaire vérifie que l'évaluateur échoue si un seuil a été calculé sur le test (mesure de la fuite).

### Étape 2.2 : limites de PaySim et transfert de domaine (point 4)

1. **Analyse descriptive** : taux de fraude par type de transaction, par heure et par montant. Montrer que `PAYMENT` ne contient aucune fraude, donc qu'aucune affirmation n'est possible sur les paiements marchands ni sur PayPal.
2. **Évaluation par sous-population** (TRANSFER / CASH_OUT) et par tranche de montant.
3. **Robustesse :**
   - bruit sur les variables ;
   - suppression d'une variable (ablation des features) ;
   - décalage temporel (entraîner sur les pas 1 à 200, tester sur les pas 551 à 744) ;
   - perturbation adversariale simple, par exemple fractionner un vidage de compte en 2 transferts.
4. **Transfert de domaine** : une seconde source documentée.
   - Option A : IEEE-CIS Fraud Detection, ou Credit Card Fraud (ULB), pour tester la *méthode* sur un autre domaine.
   - Option B : un générateur de scénarios marchands documenté (`datasets/merchant-synthetic/`), dont chaque type de fraude correspond à une règle publiée. Ses résultats sont présentés comme *validation de mécanisme*, pas comme performance réelle.
5. **Formulation finale** dans l'article : « Neural Guardian détecte le schéma *account takeover / cash-out* de PaySim (PR-AUC X ± Y) ; aucune performance n'est revendiquée sur des paiements marchands ou PayPal. »

### Étape 2.3 : explicabilité SHAP (point 14)

1. **Stabilité** : pour 1 000 transactions du test, perturbations de ±1 % sur les variables continues. Mesurer la corrélation de Spearman entre classements SHAP et l'overlap du top-3. Cible annoncée à l'avance, par exemple overlap top-3 ≥ 0,8.
2. **Cohérence** : est-ce que SHAP pointe `drains_account` / `amount_to_balance` quand la règle métier « vidage » est vraie ? On mesure l'accord entre explication et règle.
3. **Analyse d'erreurs** : 50 faux positifs et 50 faux négatifs, avec la catégorie d'erreur et le fait que l'explication aide ou non à comprendre l'erreur (annotation à deux personnes, kappa de Cohen).
4. **Global et local** : importance globale (moyenne de |SHAP|) d'un côté, explications locales de l'autre, avec leurs limites (corrélation entre variables, interactions).

---

## Phase 3 : validation du protocole, de la crypto et du consensus (points 7, 9, 13) — semaines 3 et 4

### Étape 3.1 : tests de propriétés et mutation (point 9)

Implémentation de l'étape 1.5, plus des tests de mutation (`go-mutesting`) sur `x/smartmoney/keeper`. Score de mutation rapporté.

### Étape 3.2 : signatures et attestations Guardian

Tests négatifs systématiques :
- signature invalide ;
- clé non autorisée ;
- payload modifié ;
- attestation expirée ou du futur ;
- doublon de Guardian dans le quorum.

On mesure le taux d'acceptation, qui doit être 0, sur 10⁴ cas générés.

### Étape 3.3 : matrice k-of-n (point 13)

Script `research/experiments/attestation/run.py`, sur une pile locale où le nombre d'attestateurs est paramétrable :

| Paramètre | Valeurs |
| --- | --- |
| n | 3, 5, 7 |
| k | 1 … n |
| attestateurs absents | 0 … n |
| attestateurs compromis f (attestent une preuve forgée) | 0 … n |
| comportements | signature invalide, doublon, champs contradictoires, attestation tardive (après expiration des PENDING) |

Mesures :
- décision finale : CONFIRMED / PENDING / expirée ;
- **temps jusqu'au quorum** (p50/p95) ;
- nombre de transactions et coût en gas.

Résultat attendu, et vérifié : une preuve forgée n'est confirmée que si f ≥ k ; une preuve honnête est confirmée tant qu'au moins k attestateurs sont disponibles. Chaque cellule est répétée 30 fois.

### Étape 3.4 : consensus et décentralisation (point 7)

1. **Topologie isolée** : 4 puis 7 validateurs sur **machines ou VM distinctes**, idéalement chez 2 ou 3 fournisseurs cloud et dans des régions différentes, avec des clés générées sur chaque machine (cérémonie `networks/aurix-testnet-2`).
   - Variante expérimentale reproductible : conteneurs isolés avec latence et perte injectées par `tc netem`, scripté dans `research/experiments/consensus/`.
2. **Scénarios :**
   - perte de 1 puis 2 validateurs ;
   - partition réseau 2/2 et 3/1 ;
   - latence de 50, 200 et 500 ms ;
   - redémarrage ;
   - double signature volontaire (validateur malveillant, prouvé par evidence et slashing).
3. **Mesures :**
   - finalité (temps entre soumission et inclusion) ;
   - blocs perdus ;
   - temps de reprise après retour du quorum ;
   - comportement sous 2/3 (arrêt, sans fork).
4. **Rapport** `results/…/consensus/RESULTS.md` : topologie (schéma, fournisseurs, régions, opérateurs) et analyse au regard de l'hypothèse « moins d'1/3 byzantin ».
5. **Formulation honnête :** « tolérance aux pannes démontrée sur N machines isolées opérées par M opérateurs » ; la décentralisation de gouvernance reste une perspective tant que M est égal à 1.

---

## Phase 4 : campagne expérimentale (points 5, 6, 10) — semaines 4 à 6

### Étape 4.1 : configurations B0 à B4 (point 5)

Une seule application paramétrée par des *feature flags*, pour comparer à code égal :

| Config | Composition | Implémentation |
| --- | --- | --- |
| B0 | orchestration centralisée : base SQL + API, sans chaîne, ni règles, ni IA | service `research/baselines/b0` (FastAPI + PostgreSQL), mêmes endpoints |
| B1 | B0 + règles conditionnelles (escrow en SQL, transactions ACID) | idem, avec la machine à états escrow côté serveur |
| B2 | B1 + scoring ML (Guardian) | flag `GUARDIAN=on` |
| B3 | B1 + attestations k-of-n + ancrage (sur la chaîne) | AURIX sans Guardian |
| B4 | AURIX complet | pile actuelle |

### Étape 4.2 : benchmark de bout en bout (point 6)

1. **Générateur de charge** : Locust ou k6, scénarios scriptés.
   - Transfert simple.
   - Escrow complet : création, puis libération.
   - Paiement PayPal complet : commande, approbation, capture, webhook, attestations, ANCHORED.
2. **Paliers** : 10, 50, 100, 500, 1 000 utilisateurs simultanés (ajustés à la machine), en boucle ouverte avec un débit cible (arrivées Poisson), pour éviter le *coordinated omission*.
3. **Mesures :**
   - latences p50/p95/p99 par étape (instrumentation OpenTelemetry : passerelle → payments → chaîne → attestateurs) ;
   - TPS soutenu et taux d'erreur ;
   - CPU et RAM par service (cAdvisor / `docker stats`) ;
   - taille de la chaîne et de PostgreSQL.
4. **Répétitions** : 5 exécutions par configuration et par palier, avec moyenne, IC 95 % et test de Mann-Whitney entre configurations.
5. **Injection de pannes pendant la charge :** arrêt d'un validateur, d'un attestateur, du Guardian, de l'indexeur ; PayPal lent (latence injectée dans le simulateur). On mesure la dégradation et la reprise.
6. **Environnement** documenté automatiquement (`env.json`) : CPU, RAM, OS, versions Go/Python, commit, paramètres CometBFT.
7. **Coût** : estimation par configuration (instances Render ou cloud équivalentes, prix public daté).

### Étape 4.3 : PayPal Sandbox (point 10)

1. Compte développeur, application REST Sandbox, webhook vers une URL publique de la passerelle (déploiement Render, phase v0.2), événements `PAYMENT.CAPTURE.COMPLETED`, `…DENIED` et `…REFUNDED`.
2. Tests réels :
   - vérification de signature (certificat PayPal) ;
   - événements dupliqués (renvoi depuis le dashboard) ;
   - retards ;
   - remboursement (`/v2/payments/captures/{id}/refund` : quel effet sur la preuve ancrée ? à spécifier, par exemple un statut `REFUNDED` ancré).
3. **Réconciliation** : job qui compare les statuts PayPal et chaîne, et rapport d'incohérences pendant 1 semaine de tests.
4. Garder le simulateur pour la charge et le chaos (PayPal Sandbox limite les débits). Le tableau des fonctionnalités indique implémenté, simulé ou planifié, avec Onafriq en *planned*.

---

## Phase 5 : reproductibilité et validation indépendante (point 11) — semaines 6 et 7

### Étape 5.1 : environnement reproductible

- Images Docker épinglées par digest, lockfiles (`pip-compile` pour Python, `go.sum` déjà présent).
- `research/reproducibility/environment.md` : matériel de référence et durée attendue de chaque expérience.
- `run-all.sh` et `make figures` fonctionnent depuis un clone neuf. Vérifié en CI par un job nocturne, avec une version réduite des expériences.
- Données : script de téléchargement + SHA-256 (déjà fait pour PaySim : `guardian/training/fetch_paysim.sh`).

### Étape 5.2 : archivage

Release taguée (`v0.2.0-paper`), DOI Zenodo du dépôt et des résultats bruts, et badge « Artifacts Available / Evaluated » visé (ACM).

### Étape 5.3 : revue et reproduction indépendantes

- Une personne extérieure reproduit 3 expériences centrales (ML, k-of-n, performance B4) **sans aide**, depuis le README, et note les écarts dans `research/reproducibility/REPLICATION-REPORT.md`.
- Revue du code critique (`x/smartmoney`, ancrage, vérification de webhooks) par une seconde personne, avec checklist OWASP / Cosmos SDK security.
- Revue des conclusions : chaque phrase de l'article doit pointer vers un résultat (étape 5.4).

### Étape 5.4 : affirmations encadrées (point 15)

Section **Scope and Limitations** de l'article, plus un *claims ledger* (`papers/claims.md`) : une ligne par affirmation, avec sa preuve (fichier de résultat) ou son statut (hypothèse ou perspective). Une affirmation sans preuve est retirée ou reformulée.

---

## Calendrier (indicatif, 1 à 2 personnes)

| Semaine | Phases | Jalons vérifiables |
| --- | --- | --- |
| S1 | 1.1 à 1.4 | `contributions.md`, `hypotheses.md` (pré-enregistrées), `threat-model.md` |
| S2 | 1.5, 1.6, 2.1 | tests de propriétés en CI ; nouveau protocole ML ; seuils re-choisis sur la validation |
| S3 | 2.2, 2.3, 3.1, 3.2 | rapport PaySim et transfert ; étude SHAP ; score de mutation |
| S4 | 3.3, 3.4 | matrice k-of-n complète ; réseau sur machines isolées et rapport consensus |
| S5 | 4.1, 4.2 | baselines B0 à B4 ; premier benchmark complet |
| S6 | 4.2 (répétitions), 4.3 | résultats répétés avec IC ; PayPal Sandbox et réconciliation |
| S7 | 5.1 à 5.4 | reproduction indépendante ; DOI ; claims ledger ; brouillon de l'article |

## Grille de suivi (à mettre à jour seulement avec une preuve)

| Dimension | Actuel (diagnostic) | Preuve qui fait monter la note | Cible |
| --- | --- | --- | --- |
| Formalisation | 7,0 | contributions, hypothèses pré-enregistrées, invariants et modèle de menace relus | 9,5 |
| Validation expérimentale | 4,0 | B0 à B4 répétés avec IC ; matrice k-of-n ; consensus isolé | 9,5 |
| ML | — | protocole sans fuite ; baselines ; calibration ; IC ; analyse PaySim | 9,5 |
| Sécurité et résilience | 6,0 | matrice menace, défense et test entièrement couverte ; tests de propriétés et mutation ; chaos | 9,5 |
| Reproductibilité | 7,0 | reproduction indépendante réussie depuis un clone neuf | 9,5 |
| Originalité démontrée | 5,0 | tableau comparatif + ablations qui montrent l'apport de C1 et C2 | 9,5 |
| Documentation | 8,0 | statuts implémenté, simulé ou planifié partout ; trois architectures | 9,5 |

**Risque principal :** la décentralisation réelle (étape 3.4) et le PayPal Sandbox (étape 4.3) dépendent de personnes et de comptes extérieurs. Il faut commencer leur organisation dès S1 pour qu'ils ne bloquent pas S4 et S6.
