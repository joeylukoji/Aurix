// AURIX wallet keys: BIP39 mnemonic -> BIP32 m/44'/118'/0'/0/0 (Cosmos standard, same as
// `aurixd keys add --recover`) and a password-encrypted vault (WebCrypto PBKDF2 + AES-GCM).
// Needs vendor/noble.min.js (secp256k1, sha256), vendor/bip39-english.js and cosmos-tx.js.
// Works in browsers and in Node >= 19 (globalThis.crypto.subtle) for tests.
(() => {
  const { secp256k1, sha256 } = window.noble;
  const WORDS = window.BIP39_ENGLISH;
  const subtle = globalThis.crypto.subtle;
  const enc = new TextEncoder();
  const N = BigInt("0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141");
  const PATH = "m/44'/118'/0'/0/0";
  const VAULT_KEY = "aurix.vault";
  const KDF_ITER = 310000;

  const toHex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
  const fromHex = (h) => Uint8Array.from(h.match(/../g) || [], (x) => parseInt(x, 16));
  const b64 = (u8) => btoa(String.fromCharCode(...u8));
  const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const be32 = (n) => Uint8Array.from([n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]);
  const concat = (...a) => { const o = new Uint8Array(a.reduce((n, x) => n + x.length, 0)); let i = 0; for (const x of a) { o.set(x, i); i += x.length; } return o; };
  const big = (u8) => BigInt("0x" + (toHex(u8) || "0"));
  const bytes32 = (n) => fromHex(n.toString(16).padStart(64, "0"));

  // ---------- BIP39 ----------
  const normalize = (m) => String(m || "").normalize("NFKD").trim().toLowerCase().split(/\s+/).filter(Boolean).join(" ");
  function entropyToMnemonic(ent) {
    const cs = ent.length / 4; // checksum bits
    const bits = [...ent].map((b) => b.toString(2).padStart(8, "0")).join("") +
      [...sha256(ent)].map((b) => b.toString(2).padStart(8, "0")).join("").slice(0, cs);
    return bits.match(/.{11}/g).map((b) => WORDS[parseInt(b, 2)]).join(" ");
  }
  function generateMnemonic(strength = 128) {
    return entropyToMnemonic(globalThis.crypto.getRandomValues(new Uint8Array(strength / 8)));
  }
  function validateMnemonic(m) {
    const w = normalize(m).split(" ");
    if (![12, 15, 18, 21, 24].includes(w.length)) return false;
    const idx = w.map((x) => WORDS.indexOf(x));
    if (idx.some((i) => i < 0)) return false;
    const bits = idx.map((i) => i.toString(2).padStart(11, "0")).join("");
    const entBits = (w.length * 11 * 32) / 33;
    const ent = Uint8Array.from(bits.slice(0, entBits).match(/.{8}/g), (b) => parseInt(b, 2));
    return entropyToMnemonic(ent) === w.join(" ");
  }
  async function mnemonicToSeed(m, passphrase = "") {
    const key = await subtle.importKey("raw", enc.encode(normalize(m)), "PBKDF2", false, ["deriveBits"]);
    return new Uint8Array(await subtle.deriveBits({ name: "PBKDF2", hash: "SHA-512",
      salt: enc.encode("mnemonic" + passphrase.normalize("NFKD")), iterations: 2048 }, key, 512));
  }

  // ---------- BIP32 (private derivation only) ----------
  async function hmac512(key, data) {
    const k = await subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-512" }, false, ["sign"]);
    return new Uint8Array(await subtle.sign("HMAC", k, data));
  }
  async function derivePath(seed, path = PATH) {
    let I = await hmac512(enc.encode("Bitcoin seed"), seed);
    let k = I.slice(0, 32), c = I.slice(32);
    for (const part of path.split("/").slice(1)) {
      const hard = part.endsWith("'"), i = (parseInt(part, 10) + (hard ? 0x80000000 : 0)) >>> 0;
      const data = hard ? concat(new Uint8Array([0]), k, be32(i)) : concat(secp256k1.getPublicKey(k, true), be32(i));
      I = await hmac512(c, data);
      const il = big(I.slice(0, 32)), child = (il + big(k)) % N;
      if (il >= N || child === 0n) throw new Error("invalid child key, try the next index");
      k = bytes32(child); c = I.slice(32);
    }
    return k;
  }

  function keyFromPriv(priv, extra = {}) {
    const pub = secp256k1.getPublicKey(priv, true);
    return { ...extra, priv, pub, address: AurixTx.addressFromPub(pub) };
  }
  async function keyFromMnemonic(m) {
    if (!validateMnemonic(m)) throw new Error("invalid recovery phrase (unknown word or bad checksum)");
    return keyFromPriv(await derivePath(await mnemonicToSeed(m)), { kind: "mnemonic", path: PATH });
  }

  // ---------- encrypted vault ----------
  async function aesKey(password, salt, iterations) {
    const base = await subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveKey"]);
    return subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, base,
      { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function encryptVault(mnemonic, password, address) {
    const salt = globalThis.crypto.getRandomValues(new Uint8Array(16)), iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
    const ct = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv }, await aesKey(password, salt, KDF_ITER), enc.encode(normalize(mnemonic))));
    return { v: 1, kdf: "PBKDF2-SHA256", iter: KDF_ITER, cipher: "AES-256-GCM", salt: b64(salt), iv: b64(iv), ct: b64(ct), address, path: PATH };
  }
  async function decryptVault(vault, password) {
    let pt;
    try {
      pt = await subtle.decrypt({ name: "AES-GCM", iv: unb64(vault.iv) }, await aesKey(password, unb64(vault.salt), vault.iter), unb64(vault.ct));
    } catch { throw new Error("wrong password"); }
    return new TextDecoder().decode(pt);
  }
  // localStorage may be blocked (private mode, sandboxed iframes): never throw from storage.
  const loadVault = () => { try { const v = localStorage.getItem(VAULT_KEY); return v ? JSON.parse(v) : null; } catch { return null; } };
  const saveVault = (v) => { try { v ? localStorage.setItem(VAULT_KEY, JSON.stringify(v)) : localStorage.removeItem(VAULT_KEY); return true; } catch { return false; } };

  window.AurixKeys = { PATH, generateMnemonic, validateMnemonic, mnemonicToSeed, derivePath, keyFromMnemonic, keyFromPriv,
    encryptVault, decryptVault, loadVault, saveVault, normalize, toHex, fromHex };
})();
