# Revue de code et refactor : rôles des skills

Six skills installés dans `.agents/skills/` (liés dans `.claude/skills/`, versions figées dans
`skills-lock.json`). Chacun a **un rôle et un périmètre** ; ils ne se recouvrent pas. Les règles du dépôt
([`CODING_STANDARDS.md`](../../CODING_STANDARDS.md)) priment toujours sur les conseils génériques d'un skill.

Sur un nouveau clone, `.claude/` étant ignoré par Git, recréer les liens pour Claude Code avec :

```bash
npx skills add https://github.com/mattpocock/skills --skill code-review
npx skills add https://github.com/github/awesome-copilot --skill refactor
npx skills add https://github.com/getsentry/skills --skill security-review
npx skills add https://github.com/samber/cc-skills-golang --skill golang-design-patterns
npx skills add https://github.com/wshobson/agents --skill python-design-patterns
npx skills add https://github.com/jeffallan/claude-skills --skill devops-engineer
```

> Contenu vérifié à l'installation (2026-11-08) : uniquement du Markdown, aucun script ; les commandes réseau
> présentes sont des exemples dans la documentation de référence. Relire un skill avant de le mettre à jour.

## Les rôles

| Skill (source) | Rôle | Périmètre | Il produit | Il ne fait pas |
| --- | --- | --- | --- | --- |
| `code-review` (mattpocock/skills) | **Reviewer principal**, ouvre et clôt chaque revue | Le diff depuis un point fixe (`git diff <ref>...HEAD`) | Deux rapports séparés : **Standards** (vs `CODING_STANDARDS.md` + odeurs de Fowler) et **Spec** (vs la spec du domaine, table ci-dessous) | Corriger le code ; classer ensemble les deux axes |
| `security-review` (getsentry/skills) | **Auditeur sécurité** | `payments/`, `gateway/`, `attestor/`, `verifier/`, `common/`, `web/` (clés, signature, `verify.js`), Dockerfiles | Vulnérabilités avec niveau de confiance : crypto (JCS, Ed25519, secp256k1), webhooks, idempotence, secrets, injection (SQL, prompt), zip, CORS, fonctions de démo actives en production | Refactor de style ; remonter des doutes de faible confiance comme des failles |
| `golang-design-patterns` (samber/cc-skills-golang) | **Expert Go** | `chain/` (module `x/smartmoney`, `app/`, `cmd/aurixd`) | Problèmes de conception Go : erreurs, cycle de vie, timeouts, API de constructeurs | Toucher `*.pb.go` / `*.pb.gw.go` ; contredire les conventions Cosmos SDK (keeper, msg_server, genesis) |
| `python-design-patterns` (wshobson/agents) | **Expert Python** | `payments/`, `gateway/`, `guardian/`, `indexer/`, `attestor/`, `verifier/`, `delivery-sim/`, `common/`, `paypal-sim/` | Problèmes de structure : responsabilités mêlées, couplage, I/O dans la logique, abstractions inutiles | Rendre impure une fonction de règle ou de format ; ajouter des couches pour le principe |
| `refactor` (github/awesome-copilot) | **Exécutant**, le seul qui modifie le code | Les constats validés par les rôles ci-dessus | Petits changements sans effet sur le comportement, un par commit, tests verts avant et après | Changer un comportement, un format signé ou une API publique ; refactor sans constat préalable |
| `devops-engineer` (jeffallan/claude-skills) | **Ingénieur DevOps** | `deploy/` (Compose, `render.yaml`, monitoring), `*/Dockerfile*`, `scripts/`, `.github/workflows/` | Constats et correctifs sur builds, CI, healthchecks, secrets de déploiement, images non root | Kubernetes, Terraform ou toute infra que le projet n'a pas choisie (`CODING_STANDARDS.md` §5, pas de complexité gratuite) |

### Spec de référence pour l'axe Spec de `code-review`

| Domaine | Spec |
| --- | --- |
| Paiement conditionnel USD, vérificateur, conseiller IA (MVP 0.2) | `docs/architecture/evidence-escrow.md` |
| MSCFA complet (MVP 0.3) | `docs/architecture/mscfa.md` |
| Services, API, données | `docs/architecture/services.md`, `api/*.openapi.json` |
| Chaîne, invariants, architecture d'ensemble | `AURIX_SOFTWARE_ARCHITECTURE.md` (§8 invariants), `chain/README.md` |
| Backlog et critères d'acceptation | `docs/scrum/product-backlog.md` |

## Déroulé d'une revue

1. **Carte du code** : `graphify update .` puis `graphify explain` / `graphify path` sur les éléments touchés,
   pour connaître les dépendances avant de juger (règle du projet : Graphify à chaque implémentation).
2. **`code-review`** sur le diff : rapports Standards et Spec.
3. **Experts en parallèle**, seulement sur ce que le diff touche : `security-review` (toujours si le diff touche
   un fichier de son périmètre), `golang-design-patterns` pour `chain/`, `python-design-patterns` pour les
   services Python, `devops-engineer` pour le déploiement et la CI.
4. **Tri** : chaque constat est classé *bloquant* (invariant, faille, spec non respectée), *à corriger*, ou
   *suggestion*. Les conseils génériques contraires à `CODING_STANDARDS.md` sont écartés.
5. **`refactor`** applique les constats retenus, un par commit, en relançant les tests du service touché.
6. **Contrôle final** : tests du service (`pytest -q` / `go test ./...`) ; `scripts/test-all.sh` si plusieurs
   services ; `security-review` relancé si un fichier de son périmètre a changé.

## Zones protégées (aucun refactor sans décision explicite)

- `verifier/aurix_verify/rule.py`, `jcs.py`, `crypto.py`, `package.py` et `web/verify.js` : un changement change
  ce que les dossiers déjà émis signifient (voir `CODING_STANDARDS.md` §1, points 15 et 16).
- `chain/proto/` et les fichiers générés ; genesis et paramètres de gouvernance.
- Les invariants 1 à 17 de `CODING_STANDARDS.md` et leurs tests.

## Noms en double avec Claude Code

`code-review` et `security-review` portent le même nom que des commandes intégrées à Claude Code. Dans ce dépôt,
« le skill code-review » désigne celui de `.claude/skills/code-review` (deux axes Standards et Spec) ; préciser
« skill du dépôt » en cas de doute. La commande intégrée `/security-review` reste utilisable en complément.
