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

export default {
  // Fired by the cron trigger in wrangler.toml.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },

  // Manual test: curl "https://<worker>.workers.dev/?key=<TRIGGER_KEY>"
  // Key-gated so a stray crawler cannot spam your Actions minutes.
  async fetch(request, env) {
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
