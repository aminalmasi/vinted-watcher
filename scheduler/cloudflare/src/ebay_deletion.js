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

export async function handleEbayDeletion(request) {
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
