// Minimal Cosmos SDK tx builder for the browser (SIGN_MODE_DIRECT, secp256k1).
// Mirrors gateway/app/crypto.py; private keys never leave the browser.
(() => {
  const { secp256k1, sha256, ripemd160 } = window.noble;
  const enc = new TextEncoder();
  const concat = (...a) => { const o = new Uint8Array(a.reduce((n, x) => n + x.length, 0)); let i = 0; for (const x of a) { o.set(x, i); i += x.length; } return o; };

  // ---- protobuf wire encoding ----
  const varint = (n) => { n = BigInt(n); const o = []; do { let b = Number(n & 0x7fn); n >>= 7n; o.push(n ? b | 0x80 : b); } while (n); return Uint8Array.from(o); };
  const tag = (f, w) => varint((f << 3) | w);
  const uint = (f, n) => (n ? concat(tag(f, 0), varint(n)) : new Uint8Array());
  const bytes = (f, b) => (b.length ? concat(tag(f, 2), varint(b.length), b) : new Uint8Array());
  const str = (f, s) => (s ? bytes(f, enc.encode(s)) : new Uint8Array());
  const msg = (f, b) => concat(tag(f, 2), varint(b.length), b);
  const coin = (denom, amount) => concat(str(1, denom), str(2, String(amount)));
  const any = (typeUrl, value) => concat(str(1, typeUrl), bytes(2, value));

  // ---- bech32 ----
  const CH = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";
  const polymod = (v) => { const G = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]; let c = 1; for (const x of v) { const b = c >>> 25; c = ((c & 0x1ffffff) << 5) ^ x; for (let i = 0; i < 5; i++) if ((b >>> i) & 1) c ^= G[i]; } return c >>> 0; };
  const hrpExp = (h) => [...h].map((c) => c.charCodeAt(0) >> 5).concat([0], [...h].map((c) => c.charCodeAt(0) & 31));
  const conv = (data, from, to) => { let acc = 0, bits = 0; const o = []; for (const v of data) { acc = (acc << from) | v; bits += from; while (bits >= to) { bits -= to; o.push((acc >> bits) & ((1 << to) - 1)); } } if (bits) o.push((acc << (to - bits)) & ((1 << to) - 1)); return o; };
  const bech32 = (hrp, data) => { const d = conv(data, 8, 5); const chk = polymod(hrpExp(hrp).concat(d, [0, 0, 0, 0, 0, 0])) ^ 1; return hrp + "1" + d.concat([0, 1, 2, 3, 4, 5].map((i) => (chk >>> (5 * (5 - i))) & 31)).map((x) => CH[x]).join(""); };
  const validAddress = (a) => { try { a = a.toLowerCase(); const p = a.lastIndexOf("1"); return a.slice(0, p) === "aur" && polymod(hrpExp("aur").concat([...a.slice(p + 1)].map((c) => { const i = CH.indexOf(c); if (i < 0) throw 0; return i; }))) === 1; } catch { return false; } };

  // ---- DEMO keys only: privkey = sha256(seed string), the gateway's dev mode. Real wallets use keys.js ----
  function keyFromSeed(seed) {
    const priv = sha256(enc.encode(seed));
    const pub = secp256k1.getPublicKey(priv, true);
    return { kind: "demo", seed, priv, pub, address: addressFromPub(pub) };
  }
  const addressFromPub = (pub) => bech32("aur", ripemd160(sha256(pub)));
  // Re-encodes a bech32 address (e.g. cosmos1...) with another prefix.
  const rebech = (a, hrp) => { const p = a.lastIndexOf("1"); const d = [...a.slice(p + 1, -6)].map((c) => CH.indexOf(c)); return bech32(hrp, conv(d, 5, 8).slice(0, Math.floor(d.length * 5 / 8))); };

  // ---- messages (proto field numbers from chain/proto and cosmos-sdk) ----
  function encodeMsg(m, sender) {
    switch (m.type) {
      case "MsgSend":
        return any("/cosmos.bank.v1beta1.MsgSend", concat(str(1, sender), str(2, m.to), msg(3, coin("uaur", m.amount))));
      case "MsgCreateEscrow":
        return any("/aurix.smartmoney.v1.MsgCreateEscrow", concat(str(1, sender), str(2, m.to), str(3, m.arbiter || ""),
          msg(4, coin("uaur", m.amount)), uint(5, m.deadline_height)));
      case "MsgReleaseEscrow": case "MsgRefundEscrow":
        return any(`/aurix.smartmoney.v1.${m.type}`, concat(str(1, sender), uint(2, m.escrow_id)));
      default: throw new Error("unsupported message " + m.type);
    }
  }

  // Builds and signs a TxRaw from {chain_id, account_number, sequence, fee, msg} (read from the chain by chain.js).
  function signTx(key, prep) {
    const body = concat(msg(1, encodeMsg(prep.msg, key.address)));
    const pubAny = any("/cosmos.crypto.secp256k1.PubKey", bytes(1, key.pub));
    const signerInfo = concat(msg(1, pubAny), msg(2, msg(1, uint(1, 1))), uint(3, prep.sequence));
    const fee = concat(msg(1, coin(prep.fee.denom, prep.fee.amount)), uint(2, prep.fee.gas_limit));
    const authInfo = concat(msg(1, signerInfo), msg(2, fee));
    const signDoc = concat(bytes(1, body), bytes(2, authInfo), str(3, prep.chain_id), uint(4, prep.account_number));
    const sig = secp256k1.sign(sha256(signDoc), key.priv, { lowS: true }).toCompactRawBytes();
    const raw = concat(bytes(1, body), bytes(2, authInfo), bytes(3, sig));
    return btoa(String.fromCharCode(...raw));
  }

  window.AurixTx = { keyFromSeed, signTx, validAddress, addressFromPub, rebech };
})();
