import express from 'express';
import crypto from 'crypto';
import nacl from 'tweetnacl';
import { BotService } from '../services/botService.js';
import { ConnectionsService } from '../services/connectionsService.js';
import axios from 'axios';

const router = express.Router();

// Raw body capture lives on the global `express.json()` in app.js so a single
// parser reads each request's body. Webhook routes just read `req.rawBody`.


// ---------------------------------------------------------------------------
// SLACK WEBHOOK
// ---------------------------------------------------------------------------
//
// Verifies Slack's HMAC-SHA256 signature per
// https://api.slack.com/authentication/verifying-requests-from-slack
//
// Anyone with the webhook URL can hit it; without verification an attacker
// could replay arbitrary commands as any Slack user. We compute the same
// signature Slack would have produced and compare with `timingSafeEqual` to
// avoid leaking timing information.
function verifySlackSignature(req) {
  const signingSecret = process.env.SLACK_SIGNING_SECRET;
  if (!signingSecret) return { ok: false, reason: 'SLACK_SIGNING_SECRET not configured' };

  const timestamp = req.headers['x-slack-request-timestamp'];
  const signature = req.headers['x-slack-signature'];
  if (!timestamp || !signature) return { ok: false, reason: 'missing signature headers' };

  // Reject replay attacks — timestamp must be within 5 minutes of now.
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - Number(timestamp)) > 60 * 5) {
    return { ok: false, reason: 'timestamp too old (replay protection)' };
  }

  const raw = req.rawBody?.toString('utf8') || '';
  const base = `v0:${timestamp}:${raw}`;
  const expected = 'v0=' + crypto.createHmac('sha256', signingSecret).update(base).digest('hex');

  // Length check first — `timingSafeEqual` throws on mismatched lengths.
  if (expected.length !== signature.length) return { ok: false, reason: 'signature length mismatch' };
  const ok = crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
  return { ok, reason: ok ? null : 'signature mismatch' };
}

router.post('/slack', async (req, res) => {
  const { type, challenge, event } = req.body || {};

  // Slack's URL-verification handshake: it sends a one-off `url_verification`
  // challenge BEFORE you've added the signing secret, so we let it through
  // when we're explicitly asked to verify the URL.
  if (type === 'url_verification') return res.send(challenge);

  // Every other request must be signed.
  const sig = verifySlackSignature(req);
  if (!sig.ok) {
    console.warn(
      '[slack-webhook] rejected:', sig.reason,
      'rawBodyLen=', req.rawBody?.length ?? 0,
      'hasSecret=', !!process.env.SLACK_SIGNING_SECRET,
    );
    return res.status(401).json({ error: 'invalid signature' });
  }

  if (type === 'event_callback' && event) {
    const { user, text, channel, type: eventType, bot_id } = event;
    if (bot_id) return res.sendStatus(200);  // ignore bot loops

    if (eventType === 'app_mention' || eventType === 'message') {
      // Ack within 3s — required by Slack. Process in background after.
      res.sendStatus(200);
      try {
        const reply = await BotService.processMessage('slack', user, text);
        const internalUserId = await BotService.getInternalUserId('slack', user);
        if (internalUserId) {
          const conn = await ConnectionsService.getDecryptedSecrets(internalUserId, 'slack');
          if (conn?.secrets?.botToken) {
            await axios.post('https://slack.com/api/chat.postMessage', {
              channel, text: reply,
            }, { headers: { Authorization: `Bearer ${conn.secrets.botToken}` } });
          }
        }
      } catch (e) {
        console.error('[slack-webhook] processing failed:', e.message);
      }
      return;
    }
  }
  res.sendStatus(200);
});


// ---------------------------------------------------------------------------
// DISCORD INTERACTIONS WEBHOOK
// ---------------------------------------------------------------------------
//
// Verifies Discord's Ed25519 signature per
// https://discord.com/developers/docs/interactions/receiving-and-responding#security-and-authorization
//
// Discord WILL reject your application if you do not validate. Without this
// any third party could send fake interactions to your endpoint.
function verifyDiscordSignature(req) {
  const publicKey = process.env.DISCORD_PUBLIC_KEY;
  if (!publicKey) return { ok: false, reason: 'DISCORD_PUBLIC_KEY not configured' };

  const signature = req.headers['x-signature-ed25519'];
  const timestamp = req.headers['x-signature-timestamp'];
  if (!signature || !timestamp) return { ok: false, reason: 'missing signature headers' };

  const raw = req.rawBody?.toString('utf8') || '';
  try {
    const ok = nacl.sign.detached.verify(
      Buffer.from(timestamp + raw),
      Buffer.from(signature, 'hex'),
      Buffer.from(publicKey, 'hex'),
    );
    return { ok, reason: ok ? null : 'signature mismatch' };
  } catch (e) {
    return { ok: false, reason: `verification error: ${e.message}` };
  }
}

router.post('/discord', async (req, res) => {
  const sig = verifyDiscordSignature(req);
  if (!sig.ok) {
    console.warn('[discord-webhook] rejected:', sig.reason);
    return res.status(401).send('invalid request signature');
  }

  const { type, data, member, user, token, application_id } = req.body || {};

  if (type === 1) return res.json({ type: 1 });  // PING heartbeat

  if (type === 2) {  // APPLICATION_COMMAND
    const userId = user?.id || member?.user?.id;
    const content = data?.options?.[0]?.value || '';

    // Ack with a deferred response; Discord gives us 15 min to follow up.
    res.json({ type: 4, data: { content: 'Processing your request…' } });

    try {
      const reply = await BotService.processMessage('discord', userId, content);
      await axios.post(
        `https://discord.com/api/v10/webhooks/${application_id}/${token}`,
        { content: reply },
      );
    } catch (e) {
      console.error('[discord-webhook] processing failed:', e.message);
    }
    return;
  }
  res.sendStatus(200);
});

export default router;
