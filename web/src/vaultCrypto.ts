// On-device vault encryption (Web Crypto). The server and the AI only ever see
// ciphertext, a data key sealed with the recipient's public key, and backups of the
// owner's data key and the recipient's private key, each encrypted with that person's
// recovery code.
//
// Neither person's key is tied to one phone:
//   - The key is created in the app, then encrypted with a key derived from a
//     recovery code (PBKDF2-SHA256, 600,000 iterations), and the encrypted backup
//     is stored on the server.
//   - A new or replacement phone downloads the backup and unlocks it with the code.
//   - Anvaya never sees the code, so it can never open the backup.
//
// Demo simplification: keys are cached in localStorage. Production keeps them as
// non-extractable keys in IndexedDB and can add a passkey as a second unlock.

import { currentOwnerId } from "./api";

const b64 = (buf: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const unb64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

// Keys are kept per demo copy (PR-1.4): the owner's data key and the recipient's private key
// have different names, and the copy's owner ID keeps two copies in one browser apart.
const DATA_KEY = "owner.dataKey";
const RECIPIENT_PRIVATE = "recipient.privateJwk";
// Before this change keys had one name for every copy; only the local demo (no sign-in) still
// has the owner they belonged to, so only its keys are carried over.
const LEGACY_OWNER = "owner_demo_vikram";

function scoped(key: string): string {
  const owner = currentOwnerId();
  if (!owner) throw new Error("The demo isn't ready yet. Please wait a moment and try again.");
  return `anvaya.${owner}.${key}`;
}

function load(key: string): string | null {
  try {
    const value = localStorage.getItem(scoped(key));
    if (value !== null || currentOwnerId() !== LEGACY_OWNER) return value;
    const legacy = localStorage.getItem(`life-after.${key}`);
    if (legacy !== null) {
      localStorage.setItem(scoped(key), legacy);
      localStorage.removeItem(`life-after.${key}`);
    }
    return legacy;
  } catch {
    return null;
  }
}

function save(key: string, value: string) {
  const name = scoped(key); // never save a key before the copy is known
  try {
    localStorage.setItem(name, value);
  } catch {
    /* storage unavailable: key lives only for this session */
  }
}

function remove(key: string) {
  try {
    localStorage.removeItem(scoped(key));
  } catch {
    /* nothing to remove */
  }
}

const RSA = { name: "RSA-OAEP", hash: "SHA-256" } as const;

// ---- Owner -----------------------------------------------------------------
//
// The owner's data key is cached on this device and kept in the cloud, locked with his recovery
// code (PR-14.9), so a new phone or a cleared browser can open his vault again. Anvaya never
// sees the code, so it can never open the backup.

/** The owner's data key on this device, or null. Never makes one: a key made by mistake would
 *  open nothing he kept, and secrets locked with it could not be opened by his recipient. */
export async function ownerDataKey(): Promise<CryptoKey | null> {
  const stored = load(DATA_KEY);
  return stored ? crypto.subtle.importKey("raw", unb64(stored), "AES-GCM", true, ["encrypt", "decrypt"]) : null;
}

/** A new data key for a vault that has none yet. The caller checks that none exists anywhere. */
export async function createOwnerDataKey(): Promise<CryptoKey> {
  if (load(DATA_KEY)) throw new Error("This phone already has your key.");
  const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
  save(DATA_KEY, b64(await crypto.subtle.exportKey("raw", key)));
  return key;
}

export function hasOwnerDataKey(): boolean {
  return load(DATA_KEY) !== null;
}

/** Lock this device's data key with the owner's recovery code, for the cloud. */
export async function backupOwnerKey(recoveryCode: string): Promise<KeyBackup> {
  const key = await ownerDataKey();
  if (!key) throw new Error("This phone doesn't have your key.");
  return lockWithCode(await crypto.subtle.exportKey("raw", key), recoveryCode);
}

/** On a new phone or a cleared browser: unlock the cloud backup with the recovery code. */
export async function restoreOwnerKey(backup: KeyBackup, recoveryCode: string): Promise<void> {
  const raw = await unlockWithCode(backup, recoveryCode);
  await crypto.subtle.importKey("raw", raw, "AES-GCM", true, ["encrypt", "decrypt"]); // a real key, or throws
  save(DATA_KEY, b64(raw));
}

/** For a shared device: the key stays in the cloud, and the recovery code brings it back. */
export function forgetOwnerKeyOnThisDevice() {
  remove(DATA_KEY);
}

export async function encryptSecret(key: CryptoKey, payload: object) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = new TextEncoder().encode(JSON.stringify(payload));
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, data);
  return { iv: b64(iv), ciphertext: b64(ciphertext) };
}

export async function wrapForRecipient(dataKey: CryptoKey, recipientPublic: JsonWebKey): Promise<string> {
  const pub = await crypto.subtle.importKey("jwk", recipientPublic, RSA, false, ["encrypt"]);
  const raw = await crypto.subtle.exportKey("raw", dataKey);
  return b64(await crypto.subtle.encrypt(RSA, pub, raw));
}

// ---- Recovery code ---------------------------------------------------------

// Crockford base32: no I, L, O or U, so the code is easy to read aloud and type.
const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/** 25 characters in 5 groups = 125 bits of randomness. */
export function newRecoveryCode(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(25));
  const chars = Array.from(bytes, (b) => ALPHABET[b % 32]).join("");
  return chars.match(/.{5}/g)!.join("-");
}

/** Accepts lower case, spaces, missing dashes and look-alike letters. */
export function normalizeRecoveryCode(input: string): string {
  const cleaned = input.toUpperCase().replace(/[^0-9A-Z]/g, "").replace(/O/g, "0").replace(/[IL]/g, "1");
  return cleaned.match(/.{1,5}/g)?.join("-") ?? "";
}

export type KeyBackup = { kdf: "PBKDF2-SHA256"; iterations: number; salt: string; iv: string; ciphertext: string };

const ITERATIONS = 600_000;

async function keyFromCode(code: string, salt: BufferSource, iterations: number): Promise<CryptoKey> {
  const material = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(normalizeRecoveryCode(code)), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

/** A key's raw bytes, locked with a recovery code: the backup the server keeps. */
async function lockWithCode(raw: ArrayBuffer, recoveryCode: string): Promise<KeyBackup> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await keyFromCode(recoveryCode, salt, ITERATIONS), raw);
  return { kdf: "PBKDF2-SHA256", iterations: ITERATIONS, salt: b64(salt), iv: b64(iv), ciphertext: b64(ciphertext) };
}

async function unlockWithCode(backup: KeyBackup, recoveryCode: string): Promise<ArrayBuffer> {
  const key = await keyFromCode(recoveryCode, unb64(backup.salt), backup.iterations);
  try {
    return await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(backup.iv) }, key, unb64(backup.ciphertext));
  } catch {
    throw new Error("That recovery code doesn't match. Check it and try again.");
  }
}

// ---- Recipient -------------------------------------------------------------

/** Create the recipient key pair and an encrypted backup of the private key. */
export async function createRecipientKeys(recoveryCode: string): Promise<{ publicJwk: JsonWebKey; backup: KeyBackup }> {
  const pair = await crypto.subtle.generateKey(
    { ...RSA, modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]) },
    true,
    ["encrypt", "decrypt"],
  );
  const backup = await lockWithCode(await crypto.subtle.exportKey("pkcs8", pair.privateKey), recoveryCode);
  save(RECIPIENT_PRIVATE, JSON.stringify(await crypto.subtle.exportKey("jwk", pair.privateKey)));
  return { publicJwk: await crypto.subtle.exportKey("jwk", pair.publicKey), backup };
}

/** On a new or replacement phone: unlock the downloaded backup with the recovery code. */
export async function restoreRecipientKey(backup: KeyBackup, recoveryCode: string): Promise<void> {
  const pkcs8 = await unlockWithCode(backup, recoveryCode);
  const priv = await crypto.subtle.importKey("pkcs8", pkcs8, RSA, true, ["decrypt"]);
  save(RECIPIENT_PRIVATE, JSON.stringify(await crypto.subtle.exportKey("jwk", priv)));
}

export function hasRecipientKeys(): boolean {
  return load(RECIPIENT_PRIVATE) !== null;
}

/** Demo helper: behave like a lost or new phone. */
export function forgetRecipientKeyOnThisDevice() {
  remove(RECIPIENT_PRIVATE);
}

export async function unwrapAsRecipient(wrappedKey: string): Promise<CryptoKey> {
  const jwk = load(RECIPIENT_PRIVATE);
  if (!jwk) throw new Error("This phone doesn't have the recipient key yet. Restore it with the recovery code first.");
  const priv = await crypto.subtle.importKey("jwk", JSON.parse(jwk), RSA, false, ["decrypt"]);
  const raw = await crypto.subtle.decrypt(RSA, priv, unb64(wrappedKey));
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["decrypt"]);
}

export async function decryptSecret<T>(key: CryptoKey, iv: string, ciphertext: string): Promise<T> {
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(iv) }, key, unb64(ciphertext));
  return JSON.parse(new TextDecoder().decode(plain)) as T;
}

/**
 * A short code both phones show for the recipient's key, e.g. "3F9A-12C4-77B0-E1D2".
 * Computed on each device from the key itself, so a swapped key shows a different code.
 */
export async function keyFingerprint(publicJwk: JsonWebKey): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${publicJwk.e}.${publicJwk.n}`));
  const hex = Array.from(new Uint8Array(digest).slice(0, 8), (b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
  return hex.match(/.{4}/g)!.join("-");
}

export async function myRecipientFingerprint(): Promise<string | null> {
  const jwk = load(RECIPIENT_PRIVATE);
  if (!jwk) return null;
  const { e, n } = JSON.parse(jwk) as JsonWebKey;
  return keyFingerprint({ e, n });
}
