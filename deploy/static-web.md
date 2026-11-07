# Deploying the web wallet as a static site

`web/` is plain static files. Once the wallet talks to the chain directly (workstream 2), it needs no
gateway: only a `config.json` next to `index.html`:

```json
{
  "chain_id": "aurix-testnet-2",
  "endpoints": [
    { "rest": "https://rpc1.example.org/rest", "rpc": "https://rpc1.example.org/rpc" },
    { "rest": "https://rpc2.example.net/rest", "rpc": "https://rpc2.example.net/rpc" }
  ],
  "gateway": null,
  "faucet": null
}
```

List endpoints run by **different operators** (see `deploy/public-node/`). Each endpoint must allow
the wallet's origin in CORS, so the origin you deploy to must match `WALLET_ORIGIN` on the nodes.

## CDN (Cloudflare Pages, Netlify, S3 + CloudFront, GitHub Pages)

1. Publish the `web/` directory as is (no build step). Set `config.json` per environment.
2. Serve over HTTPS only and add security headers:
   - `Content-Security-Policy: default-src 'self'; connect-src 'self' https://rpc1.example.org https://rpc2.example.net; script-src 'self'; object-src 'none'; frame-ancestors 'none'`
   - `Strict-Transport-Security: max-age=31536000`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`
3. Cache: `index.html` and `config.json` with `Cache-Control: no-cache`; other assets may be long-cached.

## IPFS

1. `ipfs add -r --cid-version 1 web/` and note the root CID; pin it on 2+ pinning services.
2. Point a DNSLink record (`_dnslink.wallet.example.org TXT "dnslink=/ipfs/<cid>"`) at it, or share
   `https://<cid>.ipfs.dweb.link/`.
3. Publish the CID with each release (next to the binary SHA-256s) so users can check what they load.
4. The origin then is the gateway's (`https://<cid>.ipfs.dweb.link` or your DNSLink domain): add it to
   the nodes' allowed CORS origin.

Keys are generated and used only in the browser; the static host never sees them, but whoever controls
the host controls the code. Prefer content-addressed (IPFS) releases for that reason.
