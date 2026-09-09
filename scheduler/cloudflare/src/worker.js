/**
 * Cron trigger for the vinted-watcher GitHub Actions workflow.
 *
 * The scraping itself stays on GitHub Actions — only that host can reach the
 * DataImpulse proxy (the university network blocks it, and this Worker cannot
 * hold a residential proxy session anyway). All this does is say "run now"
 * every 20 minutes, because the two schedulers we tried were unusable:
 *   - GitHub's own `schedule:` fired once in 3.5 hours instead of ~10 times.
 *   - User cron on the cluster login node installs but never executes.
 *
 * Secrets (set with `wrangler secret put`, never committed):
 *   GITHUB_TOKEN  fine-grained PAT, this repo only, Actions: Read and write
 *   TRIGGER_KEY   any random string; guards the manual-test URL
 */

const OWNER = "aminalmasi";
const REPO = "vinted-watcher";
const WORKFLOW = "watch.yml";
const REF = "main";

async function dispatch(env) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      // GitHub rejects API calls without a User-Agent.
      "User-Agent": "vinted-watcher-cron",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: REF }),
  });

  // 204 No Content is success for this endpoint.
  if (res.status === 204) {
    console.log("dispatched watch.yml");
    return { ok: true, status: 204 };
  }
  const body = await res.text();
  console.log(`dispatch FAILED: ${res.status} ${body}`);
  return { ok: false, status: res.status, body };
}

// eBay marketplace account-deletion endpoint (GDPR compliance).
//
// eBay disables a production keyset until it can deliver account-closure
// notices somewhere. Two things are required of this URL:
//
//   GET  ?challenge_code=X  -> respond {"challengeResponse": sha256hex(
//                              challenge_code + verificationToken + endpointUrl)}
//        The hash covers the endpoint URL as well, so ENDPOINT below must match
//        exactly what is registered in the portal, scheme and path included.
//   POST                    -> a deletion notice; acknowledge with 200.
//
// We keep no eBay user data, so there is nothing to erase on POST. It is still
// acknowledged, because eBay disables keysets that fail to respond.

const VERIFICATION_TOKEN = "dbdecda14e7d0d60cd3ca3ce3a2c743ca620d772c098187d";
const ENDPOINT = "https://vinted-watcher-cron.aminalmasi1998.workers.dev/ebay-deletion";

async function sha256hex(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

async function handleEbayDeletion(request) {
  const url = new URL(request.url);

  if (request.method === "GET") {
    const code = url.searchParams.get("challenge_code");
    if (!code) return new Response("missing challenge_code", { status: 400 });
    const challengeResponse = await sha256hex(code + VERIFICATION_TOKEN + ENDPOINT);
    return new Response(JSON.stringify({ challengeResponse }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }

  if (request.method === "POST") {
    // Acknowledge fast and unconditionally: eBay retries and then disables the
    // keyset on repeated failures, and we hold no user records to delete.
    try { await request.json(); } catch (_) {}
    return new Response(null, { status: 200 });
  }

  return new Response("method not allowed", { status: 405 });
}


export default {
  // Fired by the cron trigger in wrangler.toml.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },

  // Manual test: curl "https://<worker>.workers.dev/?key=<TRIGGER_KEY>"
  // Key-gated so a stray crawler cannot spam your Actions minutes.
  async fetch(request, env) {
    // eBay's account-deletion endpoint must answer unauthenticated: they send
    // no key, and a keyset stays DISABLED until this URL verifies. It is
    // therefore matched before the TRIGGER_KEY gate, and only for its own path.
    if (new URL(request.url).pathname === "/ebay-deletion") {
      return handleEbayDeletion(request);
    }
    const key = new URL(request.url).searchParams.get("key");
    if (!env.TRIGGER_KEY || key !== env.TRIGGER_KEY) {
      return new Response("not found\n", { status: 404 });
    }
    const result = await dispatch(env);
    return new Response(JSON.stringify(result) + "\n", {
      status: result.ok ? 200 : 502,
      headers: { "Content-Type": "application/json" },
    });
  },
};
