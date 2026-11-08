# deploy/secrets

Mounted read-only at `/run/aurix-secrets` in the `payments` and `delivery-sim` containers
(override the host folder with `AURIX_SECRETS_DIR`). Everything here except this file is git-ignored.

| File (example name) | Used by | Variable | Content |
| --- | --- | --- | --- |
| `escrow.key` | payments | `ESCROW_KEY_FILE=/run/aurix-secrets/escrow.key` | hex 32-byte Ed25519 seed (AURIX escrow signing key) |
| `trusted-keys.json` | payments | `ESCROW_TRUSTED_KEYS_FILE=/run/aurix-secrets/trusted-keys.json` | `{"keys": [...]}`: carriers and arbiters AURIX accepts. Include the demo carrier's entry from `GET http://<delivery-sim>/key` |
| `delivery.key` | delivery-sim | `DELIVERY_SIM_KEY_FILE=/run/aurix-secrets/delivery.key` | hex 32-byte Ed25519 seed (demo carrier) |

Generate a seed: `python3 -c "import secrets; print(secrets.token_hex(32))" > escrow.key && chmod 600 escrow.key`.
Without these files (and without `AURIX_ENV=production`), the services use dev seeds and log a warning.
