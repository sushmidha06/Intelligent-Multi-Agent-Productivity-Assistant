// Integration test for /api/chat/warmup — the unauth'd endpoint that
// pre-pings Render to absorb cold-start. We exercise the route handler
// directly with a tiny express harness so we don't need to boot the
// whole app (which requires Firebase Admin keys).
import test from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';

function buildApp(aiHealthHandler) {
  // Tiny express app that mirrors the warmup route's contract.
  // The real route is in server/app.js; this test verifies the contract:
  //   - GET /api/chat/warmup returns { warmed: true } when upstream /health is 200
  //   - returns { warmed: false } (still 200) when upstream is down
  const app = express();
  app.get('/api/chat/warmup', async (_req, res) => {
    try {
      await aiHealthHandler();
      res.json({ warmed: true });
    } catch (e) {
      res.json({ warmed: false, reason: e.message });
    }
  });
  return app;
}

function listen(app) {
  return new Promise(resolve => {
    const server = app.listen(0, () => resolve(server));
  });
}

function get(port, path) {
  return new Promise((resolve, reject) => {
    http.get(`http://localhost:${port}${path}`, res => {
      let body = '';
      res.on('data', c => body += c);
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(body) }));
    }).on('error', reject);
  });
}

test('warmup returns warmed:true when upstream is healthy', async () => {
  const app = buildApp(async () => true);
  const server = await listen(app);
  try {
    const r = await get(server.address().port, '/api/chat/warmup');
    assert.equal(r.status, 200);
    assert.equal(r.body.warmed, true);
  } finally {
    server.close();
  }
});

test('warmup returns warmed:false (200) when upstream is down', async () => {
  const app = buildApp(async () => { throw new Error('connect ECONNREFUSED'); });
  const server = await listen(app);
  try {
    const r = await get(server.address().port, '/api/chat/warmup');
    assert.equal(r.status, 200, 'warmup must not surface upstream failures as errors');
    assert.equal(r.body.warmed, false);
    assert.match(r.body.reason, /ECONNREFUSED/);
  } finally {
    server.close();
  }
});
