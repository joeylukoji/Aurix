// AURIX web: English / French. The pages and app.js are written in English; this module translates what is
// displayed, including text app.js renders later (a MutationObserver), and restores English on switch-back.
// Only exact sentences and known patterns are translated: data (addresses, hashes, code, JSON) is never touched.
(function () {
  "use strict";
  const FR = {
    // ---- landing ----
    "AURIX · Pay when the evidence says so": "AURIX · Payer quand les preuves le disent",
    "How it works": "Comment ça marche", "Evidence": "Preuves", "Network": "Réseau", "Open wallet": "Ouvrir le wallet",
    "Testnet · no real money": "Testnet · pas d'argent réel", "PayPal Sandbox": "PayPal Sandbox",
    "Programmable value, verifiable trust": "Valeur programmable, confiance vérifiable",
    "Pay only when the evidence says so.": "Payez seulement quand les preuves le disent.",
    "PayPal holds the buyer's dollars. Signed evidence from the carrier, the inspector and customs decides when they are released, and anyone can re-verify that decision offline. AURIX never holds the money.":
      "PayPal garde les dollars de l'acheteur. Des preuves signées par le transporteur, l'inspecteur et la douane décident quand ils sont libérés, et n'importe qui peut revérifier cette décision hors ligne. AURIX ne détient jamais l'argent.",
    "Open the wallet": "Ouvrir le wallet",
    "The AI advises, the rule decides, the human approves.": "L'IA conseille, la règle décide, l'humain approuve.",
    "AURIX network · 4 validators": "Réseau AURIX · 4 validateurs",
    "Condition · drafted by AI, approved by the buyer": "Condition · rédigée par l'IA, approuvée par l'acheteur",
    "Shipment within 10 days": "Expédition sous 10 jours", "carrier": "transporteur", "Inspection passed": "Inspection réussie",
    "inspector": "inspecteur", "Customs cleared": "Dédouanement", "customs": "douane", "Delivered": "Livré",
    "then 72 h dispute window": "puis 72 h de contestation", "2 / 4 evidence": "2 / 4 preuves",
    "// How it works": "// Comment ça marche",
    "A trust layer on top of PayPal, not a new place to keep money.": "Une couche de confiance au-dessus de PayPal, pas un nouvel endroit où garder l'argent.",
    "Describe the deal": "Décrivez l'achat",
    "Explain the purchase in your own words. The AI advisor drafts a condition from the evidence sources AURIX knows. You read it, edit it and approve it.":
      "Expliquez l'achat avec vos mots. L'IA rédige une condition à partir des sources de preuves connues d'AURIX. Vous la lisez, la modifiez et l'approuvez.",
    "PayPal holds the funds": "PayPal garde les fonds",
    "The buyer approves on PayPal. The dollars are authorised, not captured: nothing is charged until the condition is met.":
      "L'acheteur approuve sur PayPal. Les dollars sont autorisés, pas encaissés : rien n'est débité tant que la condition n'est pas remplie.",
    "Independent evidence": "Des preuves indépendantes",
    "Each source signs its own events with its own key: shipment, inspection, customs, delivery. A forged or misattributed signature is refused.":
      "Chaque source signe ses propres événements avec sa propre clé : expédition, inspection, douane, livraison. Une signature falsifiée ou mal attribuée est refusée.",
    "A decision anyone can replay": "Une décision que tout le monde peut rejouer",
    "Release or void follows a public, deterministic rule. The proof package lets anyone re-run it offline, in the browser or on the command line.":
      "La libération ou l'annulation suit une règle publique et déterministe. Le dossier de preuve permet à chacun de la rejouer hors ligne, dans le navigateur ou en ligne de commande.",
    "// Evidence, not promises": "// Des preuves, pas des promesses",
    "Change one digit, and the proof says so.": "Changez un chiffre, et la preuve le dit.",
    "Every step becomes a signed attestation. The proof package holds the terms, the evidence, the receipts and the decision. Tamper with a single byte and verification fails. A valid signature proves who signed what; it does not prove that goods physically arrived.":
      "Chaque étape devient une attestation signée. Le dossier de preuve contient les conditions, les preuves, les reçus et la décision. Modifiez un seul octet et la vérification échoue. Une signature valide prouve qui a signé quoi ; elle ne prouve pas que la marchandise est physiquement arrivée.",
    "Verify a proof in the wallet": "Vérifier une preuve dans le wallet",
    "Integrity": "Intégrité", "Signatures": "Signatures", "Decision replayed": "Décision rejouée", "Rule": "Règle",
    "Sources": "Sources", "carrier, customs simulated": "transporteur, douane simulés", "RELEASE · match": "RELEASE · identique",
    "// Under the hood": "// Sous le capot", "Small, auditable parts.": "Des briques simples et auditables.",
    "CometBFT validators: the chain keeps running with one faulty node.": "validateurs CometBFT : la chaîne continue avec un nœud défaillant.",
    "2 of 2": "2 sur 2",
    "independent attestors re-check each PayPal capture before its proof is confirmed on chain.":
      "attestors indépendants revérifient chaque encaissement PayPal avant que sa preuve soit confirmée sur la chaîne.",
    "29 days": "29 jours",
    "the longest a PayPal authorization can hold funds: conditions must fit inside it.":
      "la durée maximale pendant laquelle une autorisation PayPal bloque les fonds : les conditions doivent tenir dedans.",
    "Try it now": "Essayez maintenant",
    "Create a wallet, describe a purchase, watch the evidence decide.": "Créez un wallet, décrivez un achat, regardez les preuves décider.",
    "Demonstration testnet: AUR has no monetary value. PayPal runs in Sandbox mode.":
      "Testnet de démonstration : l'AUR n'a aucune valeur monétaire. PayPal fonctionne en mode Sandbox.",
    "Main": "Principal",
    // ---- wallet: shell and tabs ----
    "AURIX Wallet": "Wallet AURIX", "← Home": "← Accueil", "Programmable Value, Verifiable Trust.": "Valeur programmable, confiance vérifiable.",
    "connecting…": "connexion…", "chain offline": "chaîne hors ligne", "Wallet": "Wallet", "Send AUR": "Envoyer des AUR",
    "Smart Money": "Smart Money", "PayPal (USD)": "PayPal (USD)", "Conditional (USD)": "Conditionnel (USD)", "Explorer": "Explorateur",
    "Settings": "Réglages",
    // ---- wallet tab ----
    "My wallet": "Mon wallet",
    "Your key is generated and kept in this browser only. Testnet: AUR has no monetary value.":
      "Votre clé est générée et conservée uniquement dans ce navigateur. Testnet : l'AUR n'a aucune valeur monétaire.",
    "Create wallet (recovery phrase)": "Créer un wallet (phrase de récupération)",
    "Import a recovery phrase (12 or 24 words)": "Importer une phrase de récupération (12 ou 24 mots)",
    "Recovery phrase": "Phrase de récupération", "Password (encrypts it in this browser)": "Mot de passe (la chiffre dans ce navigateur)",
    "Repeat password": "Répéter le mot de passe", "Import": "Importer",
    "Testnet demo: a throwaway dev key (sha256 of a random seed, kept unencrypted in this browser). Never use it on a public network.":
      "Démo testnet : une clé de dev jetable (sha256 d'une graine aléatoire, gardée non chiffrée dans ce navigateur). Ne l'utilisez jamais sur un réseau public.",
    "Demo account": "Compte de démo", "Import a demo seed": "Importer une graine de démo",
    "Write down your recovery phrase": "Notez votre phrase de récupération",
    "These 12 words are shown once. Anyone who has them controls the funds; without them a lost password means lost funds. Path m/44'/118'/0'/0/0 (same as":
      "Ces 12 mots ne sont affichés qu'une fois. Quiconque les possède contrôle les fonds ; sans eux, un mot de passe perdu signifie des fonds perdus. Chemin m/44'/118'/0'/0/0 (comme",
    "I wrote the 12 words down, in order, offline": "J'ai noté les 12 mots, dans l'ordre, hors ligne",
    "Password (encrypts the phrase in this browser)": "Mot de passe (chiffre la phrase dans ce navigateur)",
    "Save wallet": "Enregistrer le wallet", "Cancel": "Annuler", "Unlock wallet": "Déverrouiller le wallet", "Address": "Adresse",
    "Password": "Mot de passe", "Unlock": "Déverrouiller", "Remove wallet": "Supprimer le wallet", "Copy": "Copier",
    "AUR balance (on-chain)": "Solde AUR (sur la chaîne)", "USD (PayPal Sandbox)": "USD (PayPal Sandbox)", "separate rail": "rail séparé",
    "Get 100 test AUR": "Recevoir 100 AUR de test", "Refresh": "Actualiser", "Show seed": "Afficher la graine", "Lock": "Verrouiller",
    "Forget wallet": "Oublier le wallet", "History": "Historique", "No transactions.": "Aucune transaction.",
    "Nodes disagree.": "Les nœuds ne sont pas d'accord.", "Do not rely on this balance until they match.": "Ne vous fiez pas à ce solde tant qu'ils ne concordent pas.",
    // ---- send / smart money ----
    "Recipient": "Destinataire", "Amount (AUR)": "Montant (AUR)", "Check with Neural Guardian": "Vérifier avec Neural Guardian",
    "Guardian signatures": "Signatures Guardian", "Analysis unavailable": "Analyse indisponible", "No risk factor stands out.": "Aucun facteur de risque ne ressort.",
    "Create a conditional payment": "Créer un paiement conditionnel",
    "Funds are locked by the protocol, released by you or the arbiter, or refunded once the deadline block is reached.":
      "Les fonds sont bloqués par le protocole, libérés par vous ou l'arbitre, ou remboursés une fois le bloc d'échéance atteint.",
    "Arbiter (optional)": "Arbitre (optionnel)", "Deadline in blocks from now": "Échéance en blocs à partir de maintenant",
    "Check and lock": "Vérifier et bloquer", "Escrows": "Séquestres", "Release": "Libérer", "Refund": "Rembourser",
    // ---- PayPal ----
    "Pay in USD with PayPal Sandbox": "Payer en USD avec PayPal Sandbox",
    "USD never becomes AUR. After capture, only a proof (hash) is anchored on AURIX.":
      "Les USD ne deviennent jamais des AUR. Après l'encaissement, seule une preuve (hash) est ancrée sur AURIX.",
    "Amount (USD)": "Montant (USD)", "Description": "Description", "Create order & assess risk": "Créer la commande et évaluer le risque",
    "Orders": "Commandes", "Order": "Commande", "and pay as the buyer.": "et payez en tant qu'acheteur.",
    "Come back and confirm the capture below.": "Revenez et confirmez l'encaissement ci-dessous.", "Cancel order": "Annuler la commande",
    "Captured. Waiting for PayPal's signed webhook…": "Encaissé. En attente du webhook signé de PayPal…",
    "Captured & anchored": "Encaissé et ancré", "Proof": "Preuve", "Payment denied by PayPal": "Paiement refusé par PayPal",
    "Payment refunded": "Paiement remboursé",
    ". The proof of the original capture stays on AURIX; the refund is recorded by the payments service.":
      ". La preuve de l'encaissement d'origine reste sur AURIX ; le remboursement est enregistré par le service payments.",
    // ---- conditional ----
    "Conditional payment in USD": "Paiement conditionnel en USD",
    "PayPal holds the buyer's funds. AURIX captures them only when the carrier's signed delivery evidence arrives before the deadline, and releases them back to the buyer otherwise. AURIX never holds the money.":
      "PayPal garde les fonds de l'acheteur. AURIX ne les encaisse que lorsque les preuves signées exigées arrivent avant l'échéance, et les rend à l'acheteur sinon. AURIX ne détient jamais l'argent.",
    "Custom condition (AI draft)": "Condition sur mesure (proposée par l'IA)",
    "Describe the purchase in your own words, in any language. The AI drafts a condition and you review it. It is enforced only if you create the order with it; the AI never decides the payment.":
      "Décrivez l'achat avec vos mots, dans n'importe quelle langue. L'IA propose une condition et vous la relisez. Elle ne s'applique que si vous créez la commande avec ; l'IA ne décide jamais du paiement.",
    "Draft a condition": "Proposer une condition", "Deadline": "Échéance", "7 days": "7 jours", "3 days": "3 jours", "14 days": "14 jours",
    "29 days (PayPal maximum)": "29 jours (maximum PayPal)", "Demo: 2 minutes": "Démo : 2 minutes",
    "Designate the demo arbiter (can hold a disputed payment)": "Désigner l'arbitre de démo (peut bloquer un paiement contesté)",
    "Create conditional order & assess risk": "Créer la commande conditionnelle et évaluer le risque",
    "Conditional orders": "Commandes conditionnelles",
    "Condition (JSON, editable)": "Condition (JSON, modifiable)", "Buyer dispute window (hours)": "Délai de contestation de l'acheteur (heures)",
    "I approve this condition for the next order": "J'approuve cette condition pour la prochaine commande",
    "With \"Demo: 2 minutes\" the relative time limits (within_days) are dropped so the demo fits in the deadline.":
      "Avec « Démo : 2 minutes », les délais relatifs (within_days) sont retirés pour que la démo tienne dans l'échéance.",
    "Condition": "Condition", "No evidence yet.": "Aucune preuve pour l'instant.",
    "Demo sources (simulated, each with its own signing key):": "Sources de démo (simulées, chacune avec sa propre clé de signature) :",
    "Send a forged delivery": "Envoyer une fausse livraison",
    "Buyer: you can dispute until the dispute window closes. The payment is then held until the arbiter decides, and refunded at the deadline otherwise.":
      "Acheteur : vous pouvez contester jusqu'à la fin du délai de contestation. Le paiement est alors bloqué jusqu'à la décision de l'arbitre, et remboursé à l'échéance sinon.",
    "Dispute this payment": "Contester ce paiement", "Reason (optional)": "Motif (optionnel)",
    "Demo carrier (simulated, its own signing key):": "Transporteur de démo (simulé, avec sa propre clé de signature) :",
    "Carrier: report delivery": "Transporteur : déclarer la livraison", "Carrier: report shipment": "Transporteur : déclarer l'expédition",
    "Inspector: report inspection passed": "Inspecteur : déclarer l'inspection réussie", "Customs: report clearance": "Douane : déclarer le dédouanement",
    "No demo carrier configured (\"delivery\" in config.json).": "Aucun transporteur de démo configuré (« delivery » dans config.json).",
    "Download proof package": "Télécharger le dossier de preuve", "Verify in browser": "Vérifier dans le navigateur",
    "Tamper one byte & verify": "Modifier un octet et vérifier", "Verify it anywhere, offline:": "Vérifiez-le partout, hors ligne :",
    "AI advisor": "Conseiller IA",
    "Explains and suggests. It never decides: the decision is the public rule, or the arbiter's signature.":
      "Il explique et suggère. Il ne décide jamais : la décision, c'est la règle publique ou la signature de l'arbitre.",
    "For": "Pour", "the buyer": "l'acheteur", "the merchant": "le marchand", "an auditor": "un auditeur",
    "Explain this payment": "Expliquer ce paiement", "Brief for the arbiter": "Note pour l'arbitre", "Arbiter": "Arbitre",
    "Demo arbiter: in this testnet the server holds the demo arbiter's key and signs on your click. A real arbiter signs with their own key, outside AURIX.":
      "Arbitre de démo : sur ce testnet, le serveur détient la clé de l'arbitre de démo et signe à votre clic. Un vrai arbitre signe avec sa propre clé, en dehors d'AURIX.",
    "Inconsistencies": "Incohérences", "To check outside AURIX": "À vérifier en dehors d'AURIX", "Suggested action:": "Action suggérée :",
    "A suggestion only: nothing happens until the arbiter signs.": "Une simple suggestion : rien ne se passe tant que l'arbitre n'a pas signé.",
    "Template text (no AI model reachable)": "Texte modèle (aucun modèle d'IA joignable)",
    "Template draft (no AI model reachable)": "Proposition modèle (aucun modèle d'IA joignable)",
    "The payment amount in": "Le montant du paiement dans", "was changed by one digit.": "a été modifié d'un chiffre.",
    "Conditional order": "Commande conditionnelle", "and approve as the buyer.": "et approuvez en tant qu'acheteur.",
    "Come back and authorize: PayPal holds the funds, nothing is charged yet.":
      "Revenez et autorisez : PayPal bloque les fonds, rien n'est encore débité.",
    "Waiting for the buyer's approval on PayPal": "En attente de l'approbation de l'acheteur sur PayPal",
    "Evidence accepted: capturing at PayPal…": "Preuves acceptées : encaissement chez PayPal…",
    "Captured: waiting for PayPal's signed webhook…": "Encaissé : en attente du webhook signé de PayPal…",
    "Captured: anchoring the proof…": "Encaissé : ancrage de la preuve…",
    "Captured: proof waiting for independent attestors…": "Encaissé : preuve en attente des attestors indépendants…",
    "Released: captured on evidence, proof anchored": "Libéré : encaissé sur preuves, preuve ancrée",
    "Deadline passed: releasing the funds…": "Échéance dépassée : libération des fonds…",
    "Voided: the buyer was not charged": "Annulé : l'acheteur n'a pas été débité", "PayPal refused the operation": "PayPal a refusé l'opération",
    // ---- verification ----
    "Verify a proof package": "Vérifier un dossier de preuve", "Runs in your browser only: nothing is uploaded. Same checks as":
      "S'exécute uniquement dans votre navigateur : rien n'est envoyé. Mêmes contrôles que",
    "Proof package (.zip)": "Dossier de preuve (.zip)",
    "Check against AURIX's published keys (otherwise the keys inside the package)": "Vérifier avec les clés publiées par AURIX (sinon, les clés du dossier)",
    "Problems": "Problèmes", "Anomalies": "Anomalies", "Limitations": "Limites", "Consistency": "Cohérence", "Keys": "Clés", "Decision": "Décision",
    "This browser cannot verify Ed25519 signatures (WebCrypto). Use a recent Chrome, Firefox or Safari, or":
      "Ce navigateur ne sait pas vérifier les signatures Ed25519 (WebCrypto). Utilisez un Chrome, Firefox ou Safari récent, ou",
    // ---- explorer / settings ----
    "Block height": "Hauteur de bloc", "Txs shown": "Transactions affichées", "Locked escrows": "Séquestres bloqués",
    "PayPal proofs anchored": "Preuves PayPal ancrées", "Search": "Rechercher", "Blocks": "Blocs", "Transactions": "Transactions",
    "Searching…": "Recherche…", "Close": "Fermer", "Remove": "Retirer", "No endpoint configured.": "Aucun point d'accès configuré.",
    "The wallet reads and broadcasts directly to AURIX nodes. Endpoints are tried in order; on a timeout or server error the next one is used. The wallet trusts the node it reads from, so prefer nodes you or several independent operators run.":
      "Le wallet lit et diffuse directement auprès des nœuds AURIX. Les points d'accès sont essayés dans l'ordre ; en cas de délai dépassé ou d'erreur serveur, le suivant est utilisé. Le wallet fait confiance au nœud qu'il lit : préférez des nœuds que vous ou plusieurs opérateurs indépendants exploitez.",
    "Expected chain id": "Chain id attendu", "Gateway": "Gateway", "Faucet": "Faucet", "Chain endpoints (REST)": "Points d'accès de la chaîne (REST)",
    "Add your own node": "Ajouter votre propre nœud", "REST URL": "URL REST", "RPC URL (optional)": "URL RPC (optionnelle)", "Add node": "Ajouter le nœud",
    "Gateway (optional: PayPal and Neural Guardian)": "Gateway (optionnel : PayPal et Neural Guardian)",
    "Gateway URL — empty uses config.json,": "URL du gateway — vide = config.json,", "disables it": "le désactive", "Save gateway": "Enregistrer le gateway",
    // ---- placeholders ----
    "word1 word2 … word12": "mot1 mot2 … mot12", "64 hex chars": "64 caractères hexadécimaux",
    "e.g. I buy 500 phones from a supplier in Shenzhen, delivered to Kinshasa": "ex. : j'achète 500 téléphones à un fournisseur de Shenzhen, livrés à Kinshasa",
    "Search tx hash, aur1… address, block height, escrow id or proof hash": "Rechercher un hash de tx, une adresse aur1…, une hauteur de bloc, un id de séquestre ou un hash de preuve",
    // ---- toasts ----
    "Choose a password of at least 8 characters": "Choisissez un mot de passe d'au moins 8 caractères",
    "The two passwords differ": "Les deux mots de passe sont différents", "Confirm you wrote the 12 words down": "Confirmez que vous avez noté les 12 mots",
    "Invalid recovery phrase (unknown word or bad checksum)": "Phrase de récupération invalide (mot inconnu ou somme de contrôle incorrecte)",
    "Create a wallet first": "Créez d'abord un wallet", "Enter a valid aur1… recipient and amount": "Saisissez un destinataire aur1… et un montant valides",
    "Check addresses, amount and deadline": "Vérifiez les adresses, le montant et l'échéance", "The condition is not valid JSON": "La condition n'est pas un JSON valide",
    "Address copied": "Adresse copiée", "Node added: it is tried first": "Nœud ajouté : il est essayé en premier",
    "demo account: dev key, never use on a public network": "compte de démo : clé de dev, ne jamais utiliser sur un réseau public",
    // ---- table headers (AG Grid) ----
    "Block": "Bloc", "Type": "Type", "From": "De", "To": "À", "Result": "Résultat", "Status": "Statut", "Hash": "Hash",
    "Time": "Heure", "Height": "Hauteur", "Amount": "Montant", "Risk": "Risque", "Evidence": "Preuves",
    "Simulated sources": "Sources simulées", "Proof block": "Bloc de la preuve", "Validator": "Validateur", "Escrow": "Séquestre",
    "Txs": "Txs", "Rail": "Rail", "Attestations": "Attestations", "Order Id": "Id de commande", "Amount Usd": "Montant USD",
    "No Rows To Show": "Aucune ligne à afficher", "Loading...": "Chargement…",
    // ---- transaction workflow ----
    "Transaction workflow": "Déroulement de la transaction", "· deadline": "· échéance", "· deadline (days)": "· échéance (jours)",
    "· dispute window": "· délai de contestation", "Buyer": "Acheteur", "Independent sources": "Sources indépendantes",
    "AURIX rule": "Règle AURIX", "Carrier": "Transporteur", "Inspector": "Inspecteur", "Customs": "Douane", "Source": "Source",
    "Shipment confirmed": "Expédition confirmée", "Delivery confirmed": "Livraison confirmée",
    "Quality inspection passed": "Inspection qualité réussie", "Customs clearance confirmed": "Dédouanement confirmé",
    "Order and risk check": "Commande et contrôle du risque",
    "The order is created and Neural Guardian gives an advisory risk score before any money moves.":
      "La commande est créée et Neural Guardian donne un score de risque indicatif avant tout mouvement d'argent.",
    "Funds authorised and held": "Fonds autorisés et bloqués",
    "The buyer approves on PayPal: the amount is held, not charged. AURIX never holds the money.":
      "L'acheteur approuve sur PayPal : le montant est bloqué, pas débité. AURIX ne détient jamais l'argent.",
    "Signed evidence": "Preuves signées",
    "All of these, each signed with the source's own key, before the deadline:": "Toutes ces preuves, chacune signée avec la clé de sa source, avant l'échéance :",
    "Any one of these, each signed with the source's own key, before the deadline:": "Au moins une de ces preuves, signée avec la clé de sa source, avant l'échéance :",
    "Dispute window": "Délai de contestation",
    "Once the evidence is complete, the buyer can still dispute. A dispute holds the payment for the arbiter.":
      "Une fois les preuves réunies, l'acheteur peut encore contester. Une contestation bloque le paiement pour l'arbitre.",
    "Deterministic decision": "Décision déterministe",
    "The public rule checks signatures, amount, references and deadlines, then signs RELEASE or VOID.":
      "La règle publique vérifie les signatures, le montant, les références et les délais, puis signe RELEASE ou VOID.",
    "Release": "Libération", "Void": "Annulation", "Dispute": "Litige",
    "PayPal captures the held amount": "PayPal encaisse le montant bloqué", "Two independent attestors re-check the capture": "Deux attestors indépendants revérifient l'encaissement",
    "The proof is anchored on the AURIX chain": "La preuve est ancrée sur la chaîne AURIX", "A proof package anyone can verify offline": "Un dossier de preuve vérifiable hors ligne par tous",
    "Deadline reached without the evidence, or an arbiter decision": "Échéance atteinte sans les preuves, ou décision de l'arbitre",
    "PayPal voids the authorisation": "PayPal annule l'autorisation", "The buyer is not charged": "L'acheteur n'est pas débité",
    "The payment is held": "Le paiement est bloqué", "The arbiter decides: release or void": "L'arbitre décide : libérer ou annuler",
    "No decision by the deadline: refund": "Pas de décision à l'échéance : remboursement", "Condition (JSON)": "Condition (JSON)",
    // ---- guided demo and proof pages ----
    "AURIX demo": "Démo AURIX", "AURIX proof": "Preuve AURIX", "Home": "Accueil", "Demo": "Démo", "Proof": "Preuve",
    "Launch the demo": "Lancer la démo", "Verify a proof": "Vérifier une preuve",
    "Describe a purchase, let the AI draft the rule, watch the evidence decide.": "Décrivez un achat, laissez l'IA rédiger la règle, regardez les preuves décider.",
    "Live demo · PayPal Sandbox": "Démo en direct · PayPal Sandbox", "From a sentence to a payment rule.": "D'une phrase à une règle de paiement.",
    "Describe a purchase. The AI drafts the condition, you approve it, PayPal holds the money, and signed evidence decides.":
      "Décrivez un achat. L'IA rédige la condition, vous l'approuvez, PayPal garde l'argent, et des preuves signées décident.",
    "Describe the purchase": "Décrivez l'achat", "In your own words, in any language.": "Avec vos mots, dans n'importe quelle langue.",
    "Phones from China to Kinshasa": "Téléphones de Chine vers Kinshasa", "Cement from Turkey to Lubumbashi": "Ciment de Turquie vers Lubumbashi",
    "Laptop delivered in Kinshasa": "Ordinateur livré à Kinshasa", "Ask the AI": "Demander à l'IA",
    "The AI is drafting the condition… (about 40 s)": "L'IA rédige la condition… (environ 40 s)",
    "Review the AI condition": "Relisez la condition de l'IA", "The AI proposes. You decide.": "L'IA propose. Vous décidez.",
    "The buyer pays on PayPal": "L'acheteur paie sur PayPal", "The money is held by PayPal, not charged.": "L'argent est bloqué par PayPal, pas débité.",
    "Evidence arrives": "Les preuves arrivent", "Each source signs with its own key (simulated sources in this demo).": "Chaque source signe avec sa propre clé (sources simulées dans cette démo).",
    "Decision and explanation": "Décision et explication", "The rule decides. The AI explains.": "La règle décide. L'IA explique.",
    "Edit the condition (JSON)": "Modifier la condition (JSON)",
    "Demo: release right after the evidence (no dispute window)": "Démo : libérer dès les preuves reçues (sans délai de contestation)",
    "Approve this condition and create the order": "Approuver cette condition et créer la commande",
    "Neural Guardian risk:": "Risque Neural Guardian :", "and approve as the buyer.": "et approuvez en tant qu'acheteur.",
    "Come back here and confirm: PayPal holds the funds, nothing is charged yet.": "Revenez ici et confirmez : PayPal bloque les fonds, rien n'est encore débité.",
    "I approved on PayPal: hold the funds": "J'ai approuvé sur PayPal : bloquer les fonds", "Funds held by PayPal": "Fonds bloqués par PayPal",
    "Try a forged delivery": "Tenter une fausse livraison", "AI: explain this payment": "IA : expliquer ce paiement",
    "See and verify the proof →": "Voir et vérifier la preuve →", "The AI is writing… (about 30 s)": "L'IA rédige… (environ 30 s)",
    "Describe the purchase first": "Décrivez d'abord l'achat", "No simulated sources configured": "Aucune source simulée configurée",
    "Verifiable by anyone": "Vérifiable par tous", "Don't trust AURIX. Check it.": "Ne faites pas confiance à AURIX. Vérifiez.",
    "Every decision comes with a proof package: the terms, the signed evidence and the decision. Your browser re-checks it and replays the rule, offline.":
      "Chaque décision s'accompagne d'un dossier de preuve : les conditions, les preuves signées et la décision. Votre navigateur le revérifie et rejoue la règle, hors ligne.",
    "1 · Verify the proof": "1 · Vérifier la preuve", "Runs in your browser only: nothing is uploaded.": "S'exécute uniquement dans votre navigateur : rien n'est envoyé.",
    "Change one digit & verify again": "Changer un chiffre et revérifier", "2 · Ask the AI to explain it": "2 · Demander à l'IA de l'expliquer",
    "For an auditor: what happened, why the rule decided, and the limits. Advisory only.": "Pour un auditeur : ce qui s'est passé, pourquoi la règle a décidé, et les limites. Simple conseil.",
    "AI: explain for an auditor": "IA : expliquer pour un auditeur", "No decided order yet: run the demo first": "Aucune commande décidée : lancez d'abord la démo",
    "Every signature checks out and the replayed decision matches.": "Toutes les signatures sont valides et la décision rejouée est identique.",
    "One digit of the payment amount was changed: the proof no longer holds.": "Un chiffre du montant a été modifié : la preuve ne tient plus.",
    "This browser cannot verify Ed25519 signatures: use a recent Chrome, Edge, Firefox or Safari": "Ce navigateur ne sait pas vérifier les signatures Ed25519 : utilisez un Chrome, Edge, Firefox ou Safari récent",
    // ---- rule reasons (from the server) ----
    "payment authorised and delivery attested before the deadline": "paiement autorisé et livraison attestée avant l'échéance",
    "payment authorised and the condition was met": "paiement autorisé et condition remplie",
    "payment authorised and the condition was met, no dispute in the window": "paiement autorisé et condition remplie, sans contestation pendant le délai",
    "waiting for the payment authorisation": "en attente de l'autorisation de paiement", "waiting for the delivery attestation": "en attente de l'attestation de livraison",
    "deadline passed without the required evidence": "échéance dépassée sans les preuves exigées",
    "buyer disputed: waiting for the arbiter (refund at the deadline)": "contestation de l'acheteur : en attente de l'arbitre (remboursement à l'échéance)",
    "deadline passed with the buyer's dispute unresolved": "échéance dépassée avec la contestation de l'acheteur non résolue",
    "arbiter hold in place": "blocage de l'arbitre en place", "deadline passed with the arbiter's hold still in place": "échéance dépassée avec le blocage de l'arbitre toujours en place",
    "payment authorised for a different amount or currency": "paiement autorisé pour un autre montant ou une autre devise",
  };
  const PATTERNS = [
    [/^(.+) · block #(\d+)$/, "$1 · bloc n°$2"],
    [/^Confirmed in block (\d+) \((.+)\)$/, "Confirmé dans le bloc $1 ($2)"],
    [/^Escrow #(\d+) locked \((.+)\)$/, "Séquestre n°$1 bloqué ($2)"],
    [/^\+100 AUR in block (\d+)$/, "+100 AUR dans le bloc $1"],
    [/^Arbiter (\w+): rule now says (\w+)$/, "Arbitre $1 : la règle dit maintenant $2"],
    [/^Dispute recorded: rule now says (\w+)$/, "Contestation enregistrée : la règle dit maintenant $1"],
    [/^AURIX refused the evidence: (.+)$/, "AURIX a refusé la preuve : $1"],
    [/^([\w.]+) accepted: rule says (\w+)$/, "$1 acceptée : la règle dit $2"],
    [/^Delivery evidence accepted: (\w+)$/, "Preuve de livraison acceptée : $1"],
    [/^No answer after (\d+) s: try again in a moment$/, "Pas de réponse après $1 s : réessayez dans un instant"],
    [/^proof export failed \((\d+)\)$/, "échec de l'export de la preuve ($1)"],
    [/^Funds held by PayPal · rule says (\w+)$/, "Fonds bloqués par PayPal · la règle dit $1"],
    [/^(\d+) days$/, "$1 jours"],
    [/^Drafted by (.+) · advisory only: review it before use\.$/, "Proposé par $1 · simple conseil : relisez-le avant de l'utiliser."],
    [/^Template draft \(no AI model reachable\) · advisory only: review it before use\.$/, "Proposition modèle (aucun modèle d'IA joignable) · simple conseil : relisez-la avant de l'utiliser."],
    [/^Written by (.+) · advisory only, may contain errors · saved in the proof package\.$/, "Écrit par $1 · simple conseil, peut contenir des erreurs · enregistré dans le dossier de preuve."],
    [/^I approve: hold (.+) USD$/, "J'approuve : bloquer $1 USD"],
    [/^Open the (PayPal simulator|PayPal Sandbox) page$/, "Ouvrez la page $1"],
    [/^Deadline in (\d+:\d+)$/, "Échéance dans $1"],
    [/^· buyer dispute window (\d+) h$/, "· délai de contestation de l'acheteur $1 h"],
    [/^At least (\d+) of these, each signed with the source's own key, before the deadline:$/, "Au moins $1 de ces preuves, chacune signée avec la clé de sa source, avant l'échéance :"],
    [/^≤ (\d+) d$/, "≤ $1 j"],
    [/^Drafted by (.+)$/, "Proposé par $1"],
    [/^Written by (.+)$/, "Écrit par $1"],
    [/^(.+) unreachable \((\w+)\): check the network and try again$/, "$1 injoignable ($2) : vérifiez le réseau et réessayez"],
    [/^Nothing found for (.+)$/, "Rien trouvé pour $1"],
    [/^waiting for ([\w.]+) from (\w+)$/, "en attente de $1 de $2"],
    [/^condition met; buyer dispute window open until (.+)$/, "condition remplie ; délai de contestation ouvert jusqu'au $1"],
    [/^arbiter decided (\w+)$/, "l'arbitre a décidé $1"],
    [/^order is (\w+)$/, "la commande est $1"],
    [/^Simulated sources: (.+)\. A valid signature proves who signed, not that a parcel was physically delivered\.$/,
      "Sources simulées : $1. Une signature valide prouve qui a signé, pas que le colis a été physiquement livré."],
  ];
  const SKIP = new Set(["CODE", "PRE", "SCRIPT", "STYLE", "TEXTAREA", "KBD", "SAMP"]);
  const ATTRS = ["placeholder", "title", "aria-label"];
  const orig = new WeakMap();        // text node -> its English text
  const mine = new WeakMap();        // text node -> the text this module wrote (to ignore our own mutations)
  let lang = "en";

  function toFr(s) {
    const t = s.trim();
    if (!t) return null;
    const key = t.replace(/\s+/g, " ");  // HTML source wraps long sentences over several lines
    let out = FR[key];
    if (out === undefined) for (const [re, rep] of PATTERNS) if (re.test(key)) { out = key.replace(re, rep); break; }
    return out === undefined ? null : s.replace(t, out);
  }
  function skipped(el) {
    for (let e = el; e; e = e.parentElement) if (SKIP.has(e.tagName) || e.hasAttribute?.("data-no-i18n")) return true;
    return false;
  }
  function doText(n) {
    if (mine.get(n) === n.nodeValue) return;  // our own write
    orig.set(n, n.nodeValue);
    if (lang !== "fr" || skipped(n.parentElement)) return;
    const fr = toFr(n.nodeValue);
    if (fr !== null) { n.nodeValue = fr; mine.set(n, fr); }
  }
  function doAttrs(el) {
    for (const a of ATTRS) {
      if (!el.hasAttribute(a)) continue;
      const key = `data-i18n-${a}`;
      if (!el.hasAttribute(key) || el.getAttribute(a) !== el.getAttribute(`${key}-fr`)) el.setAttribute(key, el.getAttribute(a));
      const en = el.getAttribute(key), fr = lang === "fr" ? toFr(en) : null;
      const want = fr ?? en;
      if (el.getAttribute(a) !== want) el.setAttribute(a, want);
      if (fr) el.setAttribute(`${key}-fr`, fr); else el.removeAttribute(`${key}-fr`);
    }
  }
  function walk(root) {
    if (root.nodeType === 3) return doText(root);
    if (root.nodeType !== 1) return;
    doAttrs(root);
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = tw.nextNode(); n; n = tw.nextNode()) n.nodeType === 3 ? doText(n) : doAttrs(n);
  }
  function restore(root) {
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = tw.nextNode(); n; n = tw.nextNode()) {
      if (n.nodeType === 3) { const en = orig.get(n); if (en !== undefined && n.nodeValue !== en && mine.get(n) === n.nodeValue) { n.nodeValue = en; mine.set(n, en); } }
      else doAttrs(n);
    }
  }
  function title() {
    if (!document.documentElement.dataset.titleEn) document.documentElement.dataset.titleEn = document.title;
    const en = document.documentElement.dataset.titleEn;
    document.title = lang === "fr" ? (toFr(en) ?? en) : en;
  }
  function apply() {
    document.documentElement.lang = lang;
    if (lang === "fr") walk(document.body); else restore(document.body);
    title();
    document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
  }
  function set(l) {
    lang = l === "fr" ? "fr" : "en";
    try { localStorage.setItem("aurix.lang", lang); } catch { /* storage blocked: the choice lasts for this page */ }
    apply();
  }
  let saved = null;
  try { saved = localStorage.getItem("aurix.lang"); } catch { /* storage blocked */ }
  lang = saved || ((navigator.language || "").toLowerCase().startsWith("fr") ? "fr" : "en");

  new MutationObserver((ms) => {
    for (const m of ms) {
      if (m.type === "characterData") doText(m.target);
      else if (m.type === "attributes") { if (!m.attributeName.startsWith("data-i18n")) doAttrs(m.target); }
      else m.addedNodes.forEach(walk);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-lang]").forEach((b) => (b.onclick = () => set(b.dataset.lang)));
    apply();
  });
  // app.js asks which language the AI advisor should answer in.
  window.AurixI18n = { lang: () => lang, advisorLang: () => (lang === "fr" ? "French" : "English") };
})();
