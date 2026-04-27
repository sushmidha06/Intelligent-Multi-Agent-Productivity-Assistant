// Tests for the service-token JWT helpers.
// Uses Node's built-in test runner — no devDeps required.
import test from 'node:test';
import assert from 'node:assert/strict';

process.env.JWT_SHARED_SECRET = 'test-secret-shared-with-python';

const { signServiceToken, verifyServiceToken } = await import('../services/jwtService.js');

test('signServiceToken + verifyServiceToken roundtrip', () => {
  const tok = signServiceToken({ userId: 'u-1', email: 'a@b.com' });
  const claims = verifyServiceToken(tok);
  assert.equal(claims.userId, 'u-1');
  assert.equal(claims.email, 'a@b.com');
  assert.ok(claims.exp > claims.iat);
});

test('verifyServiceToken returns null on bad token', () => {
  assert.equal(verifyServiceToken('not.a.jwt'), null);
});

test('verifyServiceToken returns null on wrong-secret token', async () => {
  const jwt = (await import('jsonwebtoken')).default;
  const tok = jwt.sign({ userId: 'x' }, 'different-secret', { algorithm: 'HS256', expiresIn: 60 });
  assert.equal(verifyServiceToken(tok), null);
});

test('signServiceToken honours ttl', async () => {
  const tok = signServiceToken({ userId: 'u' }, 1);
  // Sleep 1.5s to let it expire — keep this short so CI stays fast.
  await new Promise(r => setTimeout(r, 1500));
  assert.equal(verifyServiceToken(tok), null);
});
