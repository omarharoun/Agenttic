// Cross-implementation parity check: verify the Python-generated golden fixture
// with the JS SDK. Exits non-zero on any mismatch. Run: node verify_golden.js <fixture.json>
'use strict';

const fs = require('fs');
const path = require('path');
const sdk = require('./sdk');

const fixturePath = process.argv[2] || path.join(__dirname, '../../../../tests/fixtures/passport/golden.json');
const f = JSON.parse(fs.readFileSync(fixturePath, 'utf-8'));

function assert(cond, msg) {
  if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

// 1) passport verifies with the JS SDK against the JWKS
const claims = sdk.verifyPassport(f.passport, f.jwks, { now: f.now });
assert(claims.tier === f.expected.tier, 'tier mismatch');

// 2) tampering is caught
let tampered = JSON.parse(JSON.stringify(f.passport));
tampered.claims.tier = 'A';
let caught = false;
try { sdk.verifyPassport(tampered, f.jwks, { now: f.now }); } catch (e) { caught = e instanceof sdk.TamperedError; }
assert(caught, 'tampered passport not rejected with TamperedError');

// 3) revoked status beats a valid signature
caught = false;
try { sdk.verifyPassport(f.passport, f.jwks, { now: f.now, status: 'revoked' }); } catch (e) { caught = e instanceof sdk.RevokedError; }
assert(caught, 'revoked status not rejected with RevokedError');

// 4) receipt + chain verify
sdk.verifyReceipt(f.receipt, f.jwks);
const chain = sdk.verifyChain(f.chain, f.jwks);
assert(chain.resolved, 'chain did not resolve');
assert(chain.hops.length === 2, 'chain hop count');

console.log('JS SDK parity OK (passport, tamper, revoke, receipt, chain)');
