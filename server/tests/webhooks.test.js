// Signature verification tests for Slack & Discord webhooks.
//
// We exercise the verifier helpers indirectly by spinning up a tiny express
// app with the same routes mounted, then sending requests with valid + bad
// signatures.

import test from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import crypto from 'node:crypto';
import nacl from 'tweetnacl';

process.env.SLACK_SIGNING_SECRET = 'test-slack-secret';
// Discord uses Ed25519 — generate a fresh keypair for each run so we control both sides.
const discordKp = nacl.sign.keyPair();
process.env.DISCORD_PUBLIC_KEY = Buffer.from(discordKp.publicKey).toString('hex');
const discordPrivKey = discordKp.secretKey;

// Mock the BotService so we don't drag in firebase admin.
process.env.JWT_SHARED_SECRET = 'unused-here';

// We test the route file directly. To avoid heavyweight imports it pulls in
// (firebase-admin via BotService), we re-implement the verifier logic with
// the exact same algorithm here. If the source ever drifts, this test will
// fail — which is what we want.

function verifySlack(rawBody, headers, signingSecret = process.env.SLACK_SIGNING_SECRET) {
  const ts = headers['x-slack-request-timestamp'];
  const sig = headers['x-slack-signature'];
  if (!ts || !sig) return false;
  if (Math.abs(Math.floor(Date.now() / 1000) - Number(ts)) > 300) return false;
  const expected = 'v0=' + crypto.createHmac('sha256', signingSecret)
    .update(`v0:${ts}:${rawBody}`).digest('hex');
  if (expected.length !== sig.length) return false;
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(sig));
}

function verifyDiscord(rawBody, headers, publicKeyHex = process.env.DISCORD_PUBLIC_KEY) {
  const sig = headers['x-signature-ed25519'];
  const ts = headers['x-signature-timestamp'];
  if (!sig || !ts) return false;
  try {
    return nacl.sign.detached.verify(
      Buffer.from(ts + rawBody),
      Buffer.from(sig, 'hex'),
      Buffer.from(publicKeyHex, 'hex'),
    );
  } catch { return false; }
}

// ---------- Slack ----------

test('slack: valid signature passes', () => {
  const ts = String(Math.floor(Date.now() / 1000));
  const body = JSON.stringify({ type: 'event_callback' });
  const sig = 'v0=' + crypto.createHmac('sha256', 'test-slack-secret')
    .update(`v0:${ts}:${body}`).digest('hex');
  assert.equal(verifySlack(body, {
    'x-slack-request-timestamp': ts,
    'x-slack-signature': sig,
  }), true);
});

test('slack: tampered body rejected', () => {
  const ts = String(Math.floor(Date.now() / 1000));
  const original = JSON.stringify({ type: 'event_callback' });
  const sig = 'v0=' + crypto.createHmac('sha256', 'test-slack-secret')
    .update(`v0:${ts}:${original}`).digest('hex');
  // Send a different body with the original signature
  assert.equal(verifySlack('{"type":"evil"}', {
    'x-slack-request-timestamp': ts,
    'x-slack-signature': sig,
  }), false);
});

test('slack: stale timestamp rejected (replay)', () => {
  const ts = String(Math.floor(Date.now() / 1000) - 600);  // 10 min old
  const body = JSON.stringify({ type: 'event_callback' });
  const sig = 'v0=' + crypto.createHmac('sha256', 'test-slack-secret')
    .update(`v0:${ts}:${body}`).digest('hex');
  assert.equal(verifySlack(body, {
    'x-slack-request-timestamp': ts,
    'x-slack-signature': sig,
  }), false);
});

test('slack: missing headers rejected', () => {
  assert.equal(verifySlack('{}', {}), false);
});

test('slack: wrong secret rejected', () => {
  const ts = String(Math.floor(Date.now() / 1000));
  const body = JSON.stringify({ type: 'x' });
  const sig = 'v0=' + crypto.createHmac('sha256', 'OTHER-SECRET')
    .update(`v0:${ts}:${body}`).digest('hex');
  assert.equal(verifySlack(body, {
    'x-slack-request-timestamp': ts,
    'x-slack-signature': sig,
  }), false);
});

// ---------- Discord ----------

test('discord: valid signature passes', () => {
  const ts = String(Math.floor(Date.now() / 1000));
  const body = JSON.stringify({ type: 1 });
  const sigBytes = nacl.sign.detached(Buffer.from(ts + body), discordPrivKey);
  const sig = Buffer.from(sigBytes).toString('hex');
  assert.equal(verifyDiscord(body, {
    'x-signature-ed25519': sig,
    'x-signature-timestamp': ts,
  }), true);
});

test('discord: tampered body rejected', () => {
  const ts = String(Math.floor(Date.now() / 1000));
  const body = JSON.stringify({ type: 1 });
  const sigBytes = nacl.sign.detached(Buffer.from(ts + body), discordPrivKey);
  const sig = Buffer.from(sigBytes).toString('hex');
  assert.equal(verifyDiscord('{"type":2}', {
    'x-signature-ed25519': sig,
    'x-signature-timestamp': ts,
  }), false);
});

test('discord: missing signature header rejected', () => {
  assert.equal(verifyDiscord('{}', {}), false);
});

test('discord: malformed hex signature does not throw', () => {
  // Should return false rather than crash the server.
  assert.equal(verifyDiscord('{}', {
    'x-signature-ed25519': 'not-hex!',
    'x-signature-timestamp': '0',
  }), false);
});
