// Agenttic passport verifier SDK (JS) — SPEC-2 T33.2.
//
// Offline verification of passports / receipts / chains against a JWKS, using
// Node's built-in Ed25519. Mirrors ascore/verify/sdk.py exactly (same canonical
// JSON, same distinct named errors) so a passport signed by the server verifies
// identically in Python and JS.
'use strict';

const crypto = require('crypto');

class VerifyError extends Error {}
class TamperedError extends VerifyError {}
class ExpiredError extends VerifyError {}
class RevokedError extends VerifyError {}
class UnknownKeyError extends VerifyError {}

// Canonical JSON: sorted keys (recursively), tight separators, UTF-8 — must be
// byte-identical to Python's certification.hashing.canonical_json.
function canonicalJSON(value) {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return '[' + value.map(canonicalJSON).join(',') + ']';
  }
  const keys = Object.keys(value).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJSON(value[k])).join(',') + '}';
}

function pubKeyFromJwks(jwks, kid) {
  for (const k of jwks.keys || []) {
    if (k.kid === kid) {
      return crypto.createPublicKey({ key: { kty: 'OKP', crv: 'Ed25519', x: k.x }, format: 'jwk' });
    }
  }
  throw new UnknownKeyError(`no key with kid ${kid} in JWKS`);
}

function verifySignature(publicKey, payload, signatureB64) {
  const msg = Buffer.from(canonicalJSON(payload), 'utf-8');
  const sig = Buffer.from(signatureB64, 'base64');
  const ok = crypto.verify(null, msg, publicKey, sig);
  if (!ok) throw new TamperedError('signature does not verify');
}

function verifyPassport(passport, jwks, opts = {}) {
  const claims = passport.claims;
  const pub = pubKeyFromJwks(jwks, claims.key_id);
  verifySignature(pub, claims, passport.signature || '');
  const now = opts.now ? new Date(opts.now) : new Date();
  if (now >= new Date(claims.expires_at)) {
    throw new ExpiredError(`passport expired at ${claims.expires_at}`);
  }
  if (opts.status === 'revoked') {
    throw new RevokedError('passport status is revoked');
  }
  return claims;
}

function verifyReceipt(receipt, jwks) {
  const pub = pubKeyFromJwks(jwks, receipt.key_id || '');
  const payload = {};
  for (const k of Object.keys(receipt)) {
    if (k !== 'signature' && k !== 'created_at') payload[k] = receipt[k];
  }
  verifySignature(pub, payload, receipt.signature || '');
  return receipt;
}

function verifyChain(receipts, jwks) {
  const byId = {};
  for (const r of receipts) byId[r.receipt_id] = r;
  if (receipts.length === 0) throw new VerifyError('empty chain');
  let current = receipts[0].receipt_id;
  const hops = [];
  const seen = new Set();
  let principal = null;
  while (current) {
    if (seen.has(current)) throw new VerifyError(`cycle at receipt ${current}`);
    seen.add(current);
    const r = byId[current];
    if (!r) throw new VerifyError(`broken hop: receipt ${current} not in chain`);
    verifyReceipt(r, jwks);
    hops.push({ receipt_id: r.receipt_id, policy_hash: r.policy_hash || '' });
    if (!r.parent_receipt_id) {
      principal = { passport_id: r.passport_id, agent_id: r.agent_id };
      break;
    }
    current = r.parent_receipt_id;
  }
  return { resolved: principal !== null, hops, principal };
}

function checkStatus(body) {
  return (body && body.status) || 'active';
}

module.exports = {
  VerifyError, TamperedError, ExpiredError, RevokedError, UnknownKeyError,
  canonicalJSON, verifyPassport, verifyReceipt, verifyChain, checkStatus,
};
