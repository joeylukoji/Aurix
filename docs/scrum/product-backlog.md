# Product Backlog

Priorité MoSCoW : **M** (Must), **S** (Should), **C** (Could), **W** (Won't, V1).

## Épopées

| ID | Épopée | Couche | Service principal |
| --- | --- | --- | --- |
| E1 | Wallet & transferts AUR | 1 | Noeud AURIX (`x/aurixbank`) |
| E2 | Smart Money (séquestre) | 2 | Noeud AURIX (`x/smartmoney`) |
| E3 | Neural Guardian | 3 | Guardian |
| E4 | Connecteur PayPal | 4 | API Gateway |
| E5 | Explorateur & interface | — | Web + Indexeur |
| E6 | Infrastructure & livraison | — | Deploy |

## User stories

| ID | Épopée | User story | Critères d'acceptation | Prio | Pts |
| --- | --- | --- | --- | --- | --- |
| US-01 | E6 | En tant que développeur, je veux un environnement Docker Compose afin de lancer tous les services en une commande | `docker compose up` démarre chain, gateway, guardian, indexer, postgres, redis, web | M | 5 |
| US-02 | E1 | En tant qu'utilisateur, je veux créer un wallet de démo afin d'obtenir une adresse `aur1…` | Adresse renvoyée ; clé privée jamais stockée côté serveur en clair | M | 3 |
| US-03 | E1 | En tant qu'utilisateur, je veux consulter mon solde AUR afin de connaître mes fonds | Solde lu sur la chaîne, pas dans PostgreSQL | M | 2 |
| US-04 | E1 | En tant qu'utilisateur, je veux envoyer des AUR afin de payer un pair | `MsgSend` signé, frais prélevés, nonce incrémenté, rejeu refusé, solde jamais négatif | M | 8 |
| US-05 | E2 | En tant qu'acheteur, je veux verrouiller des AUR dans un séquestre afin de payer sous condition | `MsgCreateEscrow` → état `LOCKED`, fonds débités | M | 5 |
| US-06 | E2 | En tant qu'arbitre/émetteur, je veux libérer un séquestre afin de payer le bénéficiaire | `LOCKED → RELEASED` une seule fois | M | 3 |
| US-07 | E2 | En tant qu'émetteur, je veux être remboursé à l'échéance afin de ne pas perdre mes fonds | `LOCKED → REFUNDED` si hauteur de bloc ≥ échéance ; pas de libération après | M | 3 |
| US-08 | E3 | En tant qu'utilisateur, je veux un score de risque avant de confirmer afin d'éviter une erreur ou une fraude | `POST /v1/risk/score` renvoie score, niveau, motifs, `advisory_only: true` | M | 8 |
| US-09 | E3 | En tant qu'utilisateur, je veux comprendre pourquoi une alerte est levée | Au moins 2 motifs SHAP lisibles affichés | S | 3 |
| US-10 | E3 | En tant qu'utilisateur, je veux pouvoir continuer si l'IA est indisponible | Message « analyse indisponible », transaction possible | M | 2 |
| US-11 | E4 | En tant qu'acheteur, je veux créer une commande PayPal Sandbox en USD | Orders API appelée, ID de commande renvoyé, score Guardian joint | M | 5 |
| US-12 | E4 | En tant qu'acheteur, je veux approuver explicitement avant capture | Aucune capture sans approbation (invariant 8) | M | 3 |
| US-13 | E4 | En tant que système, je veux vérifier les webhooks PayPal | Signature invalide rejetée ; doublon ignoré (HTTP 200) | M | 5 |
| US-14 | E4 | En tant qu'auditeur, je veux une preuve de suivi sur AURIX | `MsgAnchorProof` avec hash ; aucun AUR créé | M | 3 |
| US-15 | E5 | En tant qu'utilisateur, je veux un historique et un explorateur de blocs | Tableau AG Grid des blocs et transactions | S | 5 |
| US-16 | E5 | En tant qu'utilisateur, je veux distinguer visuellement USD et AUR | Soldes et couleurs séparés | M | 2 |
| US-17 | E6 | En tant que jury, je veux une démo en ligne | Déploiement Render accessible | S | 5 |
| US-18 | E6 | En tant que jury, je veux une spec OpenAPI | `api/openapi.yaml` valide, importable dans APIMatic | S | 3 |
| US-19 | E6 | En tant qu'équipe, je veux une vidéo < 3 min | Vidéo hébergée, sous-titrée | M | 5 |
| US-20 | E3 | Analyse relationnelle par graphes (GNN) | — | W | 13 |
| US-21 | E6 | Déploiement Kubernetes | — | W | 8 |
| US-22 | E1 | Réseau multi-validateurs | — | W | 13 |

**Total V1 (M + S)** : 78 points de stories + 3 points de tâches techniques de cadrage (S0) = **81 points**.
