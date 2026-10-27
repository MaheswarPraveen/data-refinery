// Coordinator: hands out jobs, collects signed votes, runs consensus, calls the judge.
// Runs on Cloudflare Workers; state lives in the D1 database bound as env.DB.

import dashboard from "./dashboard.html";
import newsPage from "./news.html";
import homePage from "./home.html";
import nodeScript from "../../node/node.py"; // served at /node.py so notebooks can just download it
import kaggleScript from "../../node/kaggle.sh"; // one-line Kaggle launcher, served at /kaggle.sh

// ---------- database: Supabase Postgres behind a D1-shaped adapter ----------
// The rest of this file was written for D1 (prepare/bind/first/all/run/batch). The adapter keeps that
// API and sends each statement (or a whole batch, as one transaction) to the rq() SQL function on
// Supabase, guarded by a secret key. Moved off D1 because its free plan caps rows read per day.
function supabaseDB(env) {
  // D1 placeholders -> Postgres: "?3" keeps its number, a bare "?" takes the next one.
  const toPg = (sql) => {
    let n = 0;
    return sql.replace(/\?(\d+)?/g, (_, d) => "$" + (d ?? ++n));
  };
  const send = async (stmts) => {
    const r = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/rq`, {
      method: "POST",
      headers: { "content-type": "application/json", apikey: env.SUPABASE_ANON },
      body: JSON.stringify({ k: env.SUPABASE_RQ_KEY, stmts }),
    });
    if (!r.ok) throw new Error(`database error ${r.status}: ${(await r.text()).slice(0, 300)}`);
    return r.json();
  };
  const statement = (sql, params = []) => ({
    sql: toPg(sql),
    params,
    bind: (...args) => statement(sql, args.map((a) => (a === undefined ? null : a))),
    async first() { return (await send([{ sql: toPg(sql), params }]))[0].results[0] ?? null; },
    async all() { return { results: (await send([{ sql: toPg(sql), params }]))[0].results }; },
    async run() {
      const [r] = await send([{ sql: toPg(sql), params }]);
      return { meta: { changes: r.changes, last_row_id: r.results[0]?.id } };
    },
  });
  return {
    prepare: (sql) => statement(sql),
    async batch(stmts) {
      const out = await send(stmts.map((s) => ({ sql: s.sql, params: s.params })));
      return out.map((r) => ({ results: r.results, meta: { changes: r.changes } }));
    },
  };
}

export default {
  // Cron (wrangler.toml): every minute, clear part of the judge backlog.
  async scheduled(event, env, ctx) {
    env = { ...env, DB: supabaseDB(env) };
    // Hints for 2-vs-1 splits first (current method), then any old GSM8K items still 'disputed'.
    ctx.waitUntil((async () => {
      // Hints cost ~3-5 subrequests each (of 50 per run), so 8 a minute fits.
      const n = await hintBacklog(env, 8).catch(() => 0);
      if (n < 8) await rejudge(env, 1).catch(() => {});
    })());
  },

  async fetch(req, env, ctx) {
    const { pathname } = new URL(req.url);
    env = { ...env, DB: supabaseDB(env) };
    try {
      if (req.method === "POST" && pathname === "/api/register") return await register(req, env);
      if (req.method === "POST" && pathname === "/api/job") return await getJob(req, env);
      if (req.method === "POST" && pathname === "/api/submit") return await submit(req, env, ctx);
      if (req.method === "GET" && pathname === "/api/stats") return await stats(env, new URL(req.url).searchParams.get("task"));
      if (req.method === "GET" && pathname === "/api/news") return await newsBoard(env);
      if (req.method === "GET" && pathname === "/news") return new Response(newsPage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/api/export") return await exportAccepted(env);
      // Rejudging spends the free Workers AI allowance, so only someone with the join code may trigger it.
      if (req.method === "POST" && pathname === "/api/rejudge") {
        if (env.JOIN_CODE && req.headers.get("x-join-code") !== env.JOIN_CODE) return json({ error: "wrong join code" }, 403);
        return await rejudge(env);
      }
      if (req.method === "GET" && pathname === "/kaggle.sh") return new Response(kaggleScript, { headers: { "content-type": "text/x-shellscript; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/node.py") return new Response(nodeScript, { headers: { "content-type": "text/x-python; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/") return new Response(homePage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/gsm8k") return new Response(dashboard, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      return json({ error: "not found" }, 404);
    } catch (err) {
      const msg = String(err?.message || err);
      // Fallback: if D1 is unavailable (e.g. the free daily read limit is used up), tell nodes to
      // pause and retry later instead of failing, so a run survives the outage untouched.
      if (/D1|limit|exceeded|SQLITE_|database|fetch failed|network/i.test(msg) && pathname.startsWith("/api/"))
        // A real outage (daily limit) waits 5 minutes; anything passing (e.g. a subrequest burst) 30 s.
        return json({ wait: true, retry_after: /daily|exceeded|limit of/i.test(msg) ? 300 : 30, error: "database unavailable", detail: msg }, 503);
      return json({ error: msg }, 500);
    }
  },
};

// ---------- helpers ----------

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

const num = (env, key) => Number(env[key]);

const hex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");

// "1,200.00" / "$72" / "72.0" all become "72"-style canonical text so votes can be compared.
export function normalize(raw) {
  const s = String(raw ?? "").trim().replace(/[,$\s]/g, "").replace(/\.$/, "");
  const n = Number(s);
  if (s !== "" && Number.isFinite(n)) return Number.isInteger(n) ? String(n) : String(Math.round(n * 1e4) / 1e4);
  return s.toLowerCase();
}

// News answers are "sentiment|category" from fixed lists; anything else is rejected (null).
const NEWS_FIELDS = [
  ["positive", "negative", "neutral"],
  ["earnings", "analyst", "price_move", "deal_order", "corporate_action", "regulatory_legal", "management", "listicle_promo", "other"],
];
export function normalizeNews(raw) {
  const parts = String(raw ?? "").toLowerCase().split("|").map((s) => s.trim().replace(/[\s-]+/g, "_"));
  if (parts.length !== NEWS_FIELDS.length || parts.some((p, i) => !NEWS_FIELDS[i].includes(p))) return null;
  return parts.join("|");
}

// Fastest believable time for a model: ~250 ms per billion parameters. Faster = suspicious.
function minMs(model) {
  const m = /(\d+(?:\.\d+)?)b/i.exec(model);
  return Math.max(250, (m ? Number(m[1]) : 1) * 250);
}

async function hmacHex(secret, msg) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(msg))));
}

// Loads the node and marks it as seen. Returns null for unknown nodes.
async function touchNode(env, nodeId) {
  const node = await env.DB.prepare("SELECT * FROM nodes WHERE id = ?").bind(nodeId ?? "").first();
  if (node) await env.DB.prepare("UPDATE nodes SET last_seen = ? WHERE id = ?").bind(Date.now(), nodeId).run();
  return node;
}

// ---------- register ----------

async function register(req, env) {
  const body = await req.json();
  if (env.JOIN_CODE && body.join_code !== env.JOIN_CODE) return json({ error: "wrong join code" }, 403);
  const id = crypto.randomUUID();
  const secret = hex(crypto.getRandomValues(new Uint8Array(32)));
  const t = Date.now();
  await env.DB.prepare("INSERT INTO nodes (id, name, secret, created_at, last_seen) VALUES (?, ?, ?, ?, ?)")
    .bind(id, String(body.name || "node").slice(0, 40), secret, t, t)
    .run();
  return json({ node_id: id, secret });
}

// ---------- job ----------

// "taken" = valid votes + live leases, counting only trusted (non-quarantined) nodes.
// allowSame = fallback when too few nodes are online: a node may vote again on an item.
// window = look at the first N pending items (by id) that this node may still take. The "mine"
// filter runs BEFORE the limit, so the window never fills up with items this node already did.
// D1 bills rows read (5M/day free). Every per-item check below is a single lookup on a composite
// index (migration 0004), and quarantined nodes are excluded via a tiny list instead of a JOIN:
// the JOIN made SQLite scan all of a node's votes per candidate (~27k rows per job).
// -1 means no limit, used as a fallback when the window has nothing for this node.
// Votes needed come from the item (3, or 5 once a hint round starts). In a hint round a node that
// voted in round 1 may vote again (with a fresh model/style and the hint): "different node" only
// applies within a round, otherwise a 4-node network could never finish a 5-vote item.
function pickItem(env, nodeId, cutoff, need, allowSame, window, tasks = "gsm8k") {
  const sameRound = "v.hinted = (CASE WHEN i.hint IS NULL THEN 0 ELSE 1 END)";
  const mine = allowSame
    ? "AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.item_id = i.id AND a.status = 'open' AND a.assigned_at > ?1 AND a.node_id = ?3)"
    : `AND NOT EXISTS (SELECT 1 FROM votes v WHERE v.item_id = i.id AND v.node_id = ?3 AND ${sameRound})
       AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.item_id = i.id AND a.status = 'open' AND a.assigned_at > ?1 AND a.node_id = ?3)`;
  const bad = "(SELECT id FROM nodes WHERE quarantined = 1)";
  const sql = `
    SELECT id, question, task, meta, hint, taken FROM (
      SELECT i.id, i.question, i.task, i.meta, i.hint, i.need,
        (SELECT COUNT(*) FROM assignments a
           WHERE a.item_id = i.id AND a.status = 'open' AND a.assigned_at > ?1 AND a.node_id NOT IN ${bad})
      + (SELECT COUNT(*) FROM votes v
           WHERE v.item_id = i.id AND v.too_fast = 0 AND v.node_id NOT IN ${bad}) AS taken
      FROM (SELECT id, question, task, meta, hint, need FROM items i WHERE status = 'pending' AND task = ANY(string_to_array(?5, ',')) ${mine} ORDER BY id LIMIT ?4) i
    ) x
    WHERE taken < need AND ?2 > 0
    ORDER BY taken DESC, RANDOM()
    LIMIT 1`;
  return env.DB.prepare(sql).bind(cutoff, need, nodeId, window < 0 ? 1_000_000 : window, tasks).first();
}

async function getJob(req, env) {
  const { node_id, tasks: canDo } = await req.json();
  // A node lists the tasks its script supports; older scripts send nothing and only get GSM8K jobs.
  const tasks = (Array.isArray(canDo) && canDo.length ? canDo : ["gsm8k"]).map(String).join(",");
  const node = await touchNode(env, node_id);
  if (!node) return json({ error: "unknown node" }, 401);

  const t = Date.now();
  const cutoff = t - num(env, "LEASE_SECONDS") * 1000;
  const need = num(env, "VOTES_PER_ITEM");
  let item = null;
  let sameNode = 0;

  if (node.quarantined) {
    // Quarantined nodes only ever see honeypots: they can't pollute the dataset, but can earn their way back.
    item = await env.DB.prepare(
      "SELECT id, question, task, meta, hint FROM items i WHERE is_honeypot = 1 AND NOT EXISTS (SELECT 1 FROM votes v WHERE v.item_id = i.id AND v.node_id = ?) ORDER BY RANDOM() LIMIT 1"
    ).bind(node_id).first();
  } else {
    item = (await pickItem(env, node_id, cutoff, need, false, 30, tasks)) || (await pickItem(env, node_id, cutoff, need, false, 300, tasks));
    if (!item) {
      const online = await env.DB.prepare("SELECT COUNT(*) AS c FROM nodes WHERE quarantined = 0 AND last_seen > ?")
        .bind(t - num(env, "ONLINE_SECONDS") * 1000).first();
      if (online.c < need) {
        item = await pickItem(env, node_id, cutoff, need, true, -1, tasks);
        sameNode = item ? 1 : 0;
      }
    }
  }
  if (!item) return json({ wait: true, retry_after: 10 });

  // Tell the node which (model, harness) pairs already answered this item, so its random pick avoids them.
  const used = await env.DB.prepare("SELECT DISTINCT model, harness FROM votes WHERE item_id = ?").bind(item.id).all();
  const res = await env.DB.prepare("INSERT INTO assignments (item_id, node_id, assigned_at, same_node) VALUES (?, ?, ?, ?) RETURNING id")
    .bind(item.id, node_id, t, sameNode).run();

  const meta = item.meta || {};
  return json({
    job_id: res.meta.last_row_id,
    task: item.task || "gsm8k",
    question: item.question,
    // News: who the headline is about. Prices and returns are never sent to nodes.
    context: item.task === "news" ? { company: meta.company, industry: meta.industry } : undefined,
    hint: item.hint || undefined,
    used_pairs: used.results.map((r) => [r.model, r.harness]),
  });
}

// ---------- submit ----------

async function submit(req, env, ctx) {
  const b = await req.json();
  const node = await touchNode(env, b.node_id);
  if (!node) return json({ error: "unknown node" }, 401);

  const answer = String(b.answer ?? "");
  const expected = await hmacHex(node.secret, `${b.job_id}|${b.model}|${b.harness}|${answer}`);
  if (b.sig !== expected) return json({ error: "bad signature" }, 401);
  const a = await env.DB.prepare("SELECT * FROM assignments WHERE id = ? AND node_id = ?").bind(b.job_id, b.node_id).first();
  if (!a || a.status !== "open") return json({ error: "unknown or finished job" }, 409);

  const item = await env.DB.prepare("SELECT id, question, gold, is_honeypot, task, need, hint, hint_model, split_fields, final_answer FROM items WHERE id = ?").bind(a.item_id).first();
  // A vote must be a well-formed answer for its task; anything else is a broken node, not an opinion.
  const norm = item.task === "news" ? normalizeNews(answer) : normalize(answer);
  if (norm === null || (item.task !== "news" && !/^-?\d+(\.\d+)?$/.test(norm)))
    return json({ error: item.task === "news" ? "answer must be sentiment|category" : "answer must be a number" }, 422);
  const t = Date.now();
  const serverMs = t - a.assigned_at; // measured here, so a node can't lie about how long it took
  const tooFast = serverMs < minMs(b.model) ? 1 : 0;
  const honeypotOk = item.is_honeypot ? (norm === item.gold ? 1 : 0) : null;

  await env.DB.batch([
    env.DB.prepare("UPDATE assignments SET status = 'done' WHERE id = ?").bind(a.id),
    env.DB.prepare(
      `INSERT INTO votes (item_id, node_id, assignment_id, model, harness, answer, reasoning, server_ms, too_fast, honeypot_ok, created_at, hinted)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).bind(item.id, b.node_id, a.id, String(b.model), String(b.harness), norm, String(b.reasoning ?? "").slice(0, 4000), serverMs, tooFast, honeypotOk, t, item.hint ? 1 : 0),
    // Running totals for the dashboard (cheap to read later).
    env.DB.prepare(
      "UPDATE nodes SET votes_n = votes_n + 1, hp_n = hp_n + ?, hp_ok = hp_ok + ?, too_fast_n = too_fast_n + ? WHERE id = ?"
    ).bind(honeypotOk === null ? 0 : 1, honeypotOk ?? 0, tooFast, b.node_id),
    env.DB.prepare(
      `INSERT INTO pair_stats (model, harness, votes, correct) VALUES (?, ?, 1, ?)
       ON CONFLICT (model, harness) DO UPDATE SET votes = pair_stats.votes + 1, correct = pair_stats.correct + excluded.correct`
    ).bind(String(b.model), String(b.harness), norm === item.gold ? 1 : 0),
    env.DB.prepare(
      `INSERT INTO node_models (node_id, model, votes) VALUES (?, ?, 1)
       ON CONFLICT (node_id, model) DO UPDATE SET votes = node_models.votes + 1`
    ).bind(b.node_id, String(b.model)),
  ]);

  if (item.is_honeypot) await updateQuarantine(env, b.node_id);
  await maybeResolve(env, ctx, item);

  // Deliberately say nothing about correctness: the node must never learn which items are honeypots.
  return json({ ok: true });
}

async function updateQuarantine(env, nodeId) {
  const r = await env.DB.prepare("SELECT hp_n, hp_ok FROM nodes WHERE id = ?").bind(nodeId).first();
  if (r.hp_n < 5) return;
  const quarantined = r.hp_ok / r.hp_n < num(env, "QUARANTINE_BELOW") ? 1 : 0;
  await env.DB.prepare("UPDATE nodes SET quarantined = ? WHERE id = ?").bind(quarantined, nodeId).run();
}

// ---------- consensus ----------

// Only one request may move an item out of 'pending' (or 'disputed'); the WHERE clause guarantees it.
async function claim(env, id, from, status, finalAnswer, score, resolvedBy) {
  const r = await env.DB.prepare(
    "UPDATE items SET status = ?, final_answer = ?, score = ?, resolved_by = ? WHERE id = ? AND status = ?"
  ).bind(status, finalAnswer, score, resolvedBy, id, from).run();
  return r.meta.changes > 0;
}

// Every node whose vote matched an accepted answer earns one "agreed" credit.
async function creditAgreement(env, itemId, answer) {
  await env.DB.prepare(
    "UPDATE nodes SET agreed_n = agreed_n + 1 WHERE id IN (SELECT node_id FROM votes WHERE item_id = ? AND answer = ?)"
  ).bind(itemId, answer).run();
}

// Consensus, field by field (GSM8K has one field, the number; news has sentiment|category).
// Round 1, 3 votes: a field all 3 agree on is kept. If they don't all agree, the judge steps in
// ONCE, and only with a hint (never the answer). Round 2: 3 fresh votes re-solve with the hint; the
// field is kept only if all 3 agree, otherwise it's thrown away (news: marked "unclear").
// So every kept answer comes from the small models: the big model only points at what to re-check.
async function maybeResolve(env, ctx, item) {
  const need = item.need || num(env, "VOTES_PER_ITEM");
  const { results: votes } = await env.DB.prepare(
    "SELECT v.answer, v.hinted FROM votes v WHERE v.item_id = ? AND v.too_fast = 0 AND v.node_id NOT IN (SELECT id FROM nodes WHERE quarantined = 1) ORDER BY v.id"
  ).bind(item.id).all();
  if (votes.length < need) return;

  if (item.is_honeypot) {
    await claim(env, item.id, "pending", "done", item.gold, null, "honeypot");
    return;
  }
  const isNews = item.task === "news";
  const nFields = isNews ? NEWS_FIELDS.length : 1;
  const split = (a) => (isNews ? a.split("|") : [a]);

  if (!item.hint) {
    // ---- round 1 ----
    const first = votes.filter((v) => !v.hinted).slice(0, 3).map((v) => split(v.answer));
    const final = [], disagreed = [];
    for (let f = 0; f < nFields; f++) {
      const vals = new Set(first.map((v) => v[f]));
      if (vals.size === 1) final.push(first[0][f]);
      else { final.push("?"); disagreed.push(f); }
    }
    const answer = final.join("|");
    if (disagreed.length) {
      // The per-minute cron writes the hint (judging here would share this request's subrequest budget).
      await env.DB.prepare("UPDATE items SET status = 'hinting', final_answer = ?, split_fields = ? WHERE id = ? AND status = 'pending'")
        .bind(answer, disagreed.join(","), item.id).run();
    } else {
      if (await claim(env, item.id, "pending", "accepted", answer, 1.0, "unanimous")) await creditAgreement(env, item.id, answer);
    }
    return;
  }

  // ---- round 2: three fresh votes made with the hint; a field survives only if all three agree ----
  const rechecks = votes.filter((v) => v.hinted).slice(0, 3).map((v) => split(v.answer));
  if (rechecks.length < 3) return;
  const final = split(item.final_answer);
  let anyKept = false, anyLost = false;
  for (const f of String(item.split_fields || "").split(",").filter(Boolean).map(Number)) {
    if (new Set(rechecks.map((r) => r[f])).size === 1) { final[f] = rechecks[0][f]; anyKept = true; }
    else { final[f] = "unclear"; anyLost = true; }
  }
  const answer = final.join("|");
  const how = `hint:${item.hint_model || "judge"}`;
  if (!isNews && anyLost) {
    await claim(env, item.id, "pending", "dropped", null, null, "hint-rejected");
  } else if (final.every((x) => x === "unclear")) {
    await claim(env, item.id, "pending", "dropped", null, null, "hint-rejected");
  } else {
    // News: the fields everyone agreed on survive even if the re-check failed the split one.
    if (await claim(env, item.id, "pending", "accepted", answer, anyLost ? 0.5 : 0.67, anyKept ? how : "partial"))
      await creditAgreement(env, item.id, answer);
  }
}

// ---------- judge ----------

// The judge is a big Llama running on Cloudflare Workers AI (env.AI): no API key, free daily allowance.
// It writes free text, not JSON: forcing JSON made it skip the actual calculations and guess.

// Last judge failures (per isolate), so a silent outage is visible in /api/rejudge.
const judgeErrors = [];
const judgeSkip = {}; // model -> time until which it's out of allowance or rate-limited

// Meta Llama on Cloudflare first, while its daily allowance lasts. Then the Groq judges in a random
// order, so problems spread across them: Groq rate-limits each model separately, so three judges
// give about three times the rulings per minute. OpenRouter (50 a day) is the last resort.
function judgeOrder(env) {
  const all = env.JUDGE_MODELS.split(",").map((m) => m.trim()).filter((m) => !(judgeSkip[m] > Date.now()));
  const cf = all.filter((m) => m.startsWith("@cf/"));
  const groq = all.filter((m) => m.startsWith("groq:")).sort(() => Math.random() - 0.5);
  const rest = all.filter((m) => !m.startsWith("@cf/") && !m.startsWith("groq:"));
  // Groq first: the Cloudflare allowance (~90 calls) is gone early each day, and every failed try
  // costs one of the 50 subrequests a cron run may make.
  return [...groq, ...cf, ...rest];
}

// One judge call. "@cf/..." = Cloudflare Workers AI (Meta Llama, free daily allowance).
// "groq:<model>" / "openrouter:<model>" = OpenAI-style APIs on their free tiers, used when the
// Cloudflare allowance is spent. Every ruling records which judge made it (resolved_by).
async function askJudge(env, model, prompt) {
  if (model.startsWith("@cf/")) {
    const out = await env.AI.run(model, { messages: [{ role: "user", content: prompt }], temperature: 0, max_tokens: 1024 });
    return String(out.response);
  }
  const [provider, name] = [model.slice(0, model.indexOf(":")), model.slice(model.indexOf(":") + 1)];
  const api = {
    groq: ["https://api.groq.com/openai/v1/chat/completions", env.GROQ_API_KEY],
    openrouter: ["https://openrouter.ai/api/v1/chat/completions", env.OPENROUTER_API_KEY],
  }[provider];
  if (!api || !api[1]) throw new Error(`no key for ${provider}`);
  const body = { model: name, messages: [{ role: "user", content: prompt }], temperature: 0, max_tokens: 900 };
  if (name.includes("gpt-oss")) body.reasoning_effort = "low"; // keeps its hidden reasoning inside the token budget
  const r = await fetch(api[0], {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${api[1]}` },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${provider} ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const content = (await r.json()).choices?.[0]?.message?.content;
  if (!content) throw new Error(`${provider}: empty reply`);
  return String(content);
}

// The judge solves the problem itself (seeing the candidates). If it lands on the majority answer, we keep it.
async function judge(env, itemId, majority) {
  const item = await env.DB.prepare("SELECT question FROM items WHERE id = ?").bind(itemId).first();
  const { results: votes } = await env.DB.prepare(
    // One reasoning per distinct answer (Postgres DISTINCT ON; SQLite allowed a bare GROUP BY here).
    "SELECT DISTINCT ON (answer) answer, reasoning FROM votes WHERE item_id = ? AND too_fast = 0 ORDER BY answer, id"
  ).bind(itemId).all();
  // Reasoning trimmed: the judge only needs the gist, and tokens are what free tiers ration.
  const candidates = votes.map((v, i) => `Candidate ${i + 1} (answer ${v.answer}):\n${String(v.reasoning ?? "").slice(0, 500)}`).join("\n\n");
  const prompt =
    `Math word problem:\n${item.question}\n\n${candidates}\n\n` +
    `The candidates may be wrong. Solve the problem yourself, showing every calculation step by step. ` +
    `Finish with one last line in exactly this form: ANSWER: <number>`;

  // Tried in order: if one model errors (e.g. the free daily allowance is used up), try the next.
  for (const model of judgeOrder(env)) {
    let raw, verdict;
    try {
      raw = await askJudge(env, model, prompt);
      // Take the number on the last "ANSWER:" line; if it forgot the line, the last number it wrote.
      const tail = [...raw.matchAll(/ANSWER:\s*([^\n]*)/gi)].pop()?.[1] ?? raw;
      const nums = tail.match(/-?\d[\d,]*\.?\d*/g);
      if (!nums) continue;
      verdict = normalize(nums[nums.length - 1]);
    } catch (err) {
      const msg = String(err?.message || err);
      // Out of daily allowance: skip this judge until midnight UTC. Rate-limited: skip for a minute.
      if (/4006|daily|per day|TPD|RPD/i.test(msg)) judgeSkip[model] = Date.now() + (86_400_000 - (Date.now() % 86_400_000));
      else if (/429|rate limit|too many/i.test(msg)) judgeSkip[model] = Date.now() + 60_000;
      judgeErrors.unshift({ at: Date.now(), model, error: msg.slice(0, 300) });
      judgeErrors.length = Math.min(judgeErrors.length, 20);
      continue;
    }
    await env.DB.prepare("INSERT INTO judgements (item_id, model, verdict, raw, created_at) VALUES (?, ?, ?, ?, ?)")
      .bind(itemId, model, verdict, raw.slice(0, 4000), Date.now()).run();
    if (verdict === majority) {
      if (await claim(env, itemId, "disputed", "accepted", majority, 0.67, `judge:${model}`)) await creditAgreement(env, itemId, majority);
    } else {
      await claim(env, itemId, "disputed", "dropped", null, null, "judge-rejected");
    }
    return;
  }
}

// ---------- hint judge ----------

// For a 2-vs-1 split the judge writes a HINT, never the answer: what to double-check. Anything
// that could leak the answer (a candidate number, a candidate label) is blanked out before nodes see it.
async function writeHint(env, item) {
  const { results: votes } = await env.DB.prepare(
    "SELECT answer FROM votes WHERE item_id = ? AND hinted = 0 AND too_fast = 0 ORDER BY id LIMIT 3"
  ).bind(item.id).all();
  const isNews = item.task === "news";
  const fields = String(item.split_fields || "0").split(",").map(Number);
  let prompt, leaks;
  if (isNews) {
    const names = ["sentiment", "category"];
    const meta = item.meta || {};
    const which = fields.map((f) => `${names[f]} (options: ${NEWS_FIELDS[f].join(", ")})`).join(" and ");
    prompt =
      `A headline about the Indian listed company ${meta.company || ""}:\n"${item.question}"\n\n` +
      `Three labellers did not agree on its ${which}. Do NOT classify the headline, do NOT say which option is ` +
      `right, and do not use any of the option words. In 1-3 sentences, only point out the facts in the headline ` +
      `a careful reader should weigh: who it is really about, what actually happened, and what is confirmed versus guessed.`;
    // "other" is too common a word to blank out, and naming it gives nothing away.
    leaks = [...fields.flatMap((f) => NEWS_FIELDS[f]), ...fields.flatMap((f) => NEWS_SYNONYMS[f])].filter((w) => w !== "other");
  } else {
    const cands = [...new Set(votes.map((v) => v.answer))];
    prompt =
      `Math word problem:\n${item.question}\n\nThree solvers did not agree; their answers were ${cands.join(" and ")}. ` +
      `Do NOT give the final answer and do not repeat those numbers. In 2-4 sentences, point out the one step ` +
      `or detail a solver should double-check (a quantity that is easy to misread, a unit, which operation applies).`;
    leaks = cands;
  }
  for (const model of judgeOrder(env)) {
    let hint;
    try {
      hint = await askJudge(env, model, prompt);
    } catch (err) {
      const msg = String(err?.message || err);
      if (/4006|daily|per day|TPD|RPD/i.test(msg)) judgeSkip[model] = Date.now() + (86_400_000 - (Date.now() % 86_400_000));
      else if (/429|rate limit|too many/i.test(msg)) judgeSkip[model] = Date.now() + 60_000;
      judgeErrors.unshift({ at: Date.now(), model, error: msg.slice(0, 300) });
      judgeErrors.length = Math.min(judgeErrors.length, 20);
      continue;
    }
    hint = hint.replace(/<think>[\s\S]*?<\/think>/g, "").replace(/ANSWER:[^\n]*/gi, "").trim();
    hint = scrubLeaks(hint, leaks);
    hint = hint.slice(0, 700);
    await env.DB.prepare("UPDATE items SET hint = ?, hint_model = ?, need = 6, status = 'pending' WHERE id = ? AND status = 'hinting'")
      .bind(hint, model, item.id).run();
    return;
  }
}

// Blank out every candidate answer: numbers match with or without thousands commas ("1500" = "1,500"),
// labels as whole words, any case.
export function scrubLeaks(text, leaks) {
  // Models write fancy hyphens (U+2010..2015, e.g. "corporate‑action"); make them plain first.
  let out = text.replace(/[‐-―−]/g, "-");
  for (const w of leaks) {
    const s = String(w);
    if (/^-?\d+(\.\d+)?$/.test(s)) {
      // Normalise numbers in the text, then compare, so "1,500" and "1500.0" are caught too.
      out = out.replace(/-?\d[\d,]*(\.\d+)?/g, (m) => (normalize(m) === normalize(s) ? "…" : m));
    } else {
      // A label matches as a whole word, with space, hyphen or underscore between its parts.
      const pattern = s.split(/[\s_-]+/).map((p) => p.replace(/[.*+?^$()|[\]\\{}]/g, "\\$&")).join("[\\s_-]+");
      out = out.replace(new RegExp("\\b" + pattern + "\\w*", "gi"), "…");
    }
  }
  return out;
}

// Words that give away a news label without naming it (scrubbed from hints along with the labels).
const NEWS_SYNONYMS = [
  ["bullish", "bearish", "optimistic", "pessimistic", "upbeat", "downbeat"],
  ["recap", "price action", "listicle", "promotional", "promotion", "advertorial", "corporate action", "regulatory", "legal"],
];

async function hintBacklog(env, n = 4) {
  const { results } = await env.DB.prepare(
    "SELECT id, question, task, meta, split_fields FROM items WHERE status = 'hinting' ORDER BY id LIMIT ?"
  ).bind(n).all();
  for (const r of results) await writeHint(env, r);
  return results.length;
}

// Rule on a few waiting (disputed) items. Every database query is a web request to Supabase and the
// free Workers plan allows 50 per invocation, so batches stay small (~8 requests per item).
// Called by the per-minute cron and by POST /api/rejudge.
async function rejudge(env, n = 4) {
  const { results } = await env.DB.prepare("SELECT id, final_answer FROM items WHERE status = 'disputed' ORDER BY id LIMIT ?").bind(n).all();
  for (const r of results) await judge(env, r.id, r.final_answer);
  const hinted = results.length < n ? await hintBacklog(env, n - results.length) : 0;
  return json({ rejudged: results.length, hinted, recent_errors: judgeErrors.slice(0, 12) });
}

// ---------- stats & export ----------

// Stats are rebuilt at most every 10 s per Worker instance, however many people watch the dashboard.
// Short caches (per isolate) so many open dashboards don't each hit the database. Supabase has no
// rows-read cap (D1 did, which is why these were 30 s / 5 min before), so the dashboard can be near-live.
let statsCache = {}; // per ?task value
let itemsCache = { at: 0, rows: null };

async function stats(env, wanted) {
  const t = Date.now();
  const cached = statsCache[wanted || ""];
  if (cached && t - cached.at < 4_000) return json(cached.body);

  if (!itemsCache.rows || t - itemsCache.at > 4_000) {
    // One pass over items. Gold is used only to *measure* quality, never to decide anything.
    const r = await env.DB.prepare(
      "SELECT task, status, is_honeypot, resolved_by, COUNT(*) AS c, COALESCE(SUM(CASE WHEN final_answer = gold THEN 1 ELSE 0 END), 0) AS ok FROM items GROUP BY task, status, is_honeypot, resolved_by"
    ).all();
    itemsCache = { at: t, rows: r.results };
  }
  const items = { results: itemsCache.rows };

  const online = t - num(env, "ONLINE_SECONDS") * 1000;
  const [nodes, pairs, recent, activity, nodeModels] = await env.DB.batch([
    // Per-node running totals: a handful of rows, not every vote.
    env.DB.prepare(
      "SELECT id, name, created_at, last_seen, quarantined, votes_n, hp_n, hp_ok, too_fast_n, agreed_n FROM nodes ORDER BY created_at"
    ),
    env.DB.prepare("SELECT model, harness, votes, correct FROM pair_stats ORDER BY model, harness"),
    // Recent activity shows WHO worked, never WHAT they answered: nodes must not see each other's answers.
    env.DB.prepare(
      "SELECT v.created_at, n.name, v.model, v.harness, v.server_ms, v.too_fast FROM votes v JOIN nodes n ON n.id = v.node_id ORDER BY v.id DESC LIMIT 12"
    ),
    env.DB.prepare(
      `SELECT
         (SELECT COUNT(*) FROM votes WHERE created_at > ?1) AS votes_10min,
         (SELECT COUNT(*) FROM assignments WHERE status = 'open' AND assigned_at > ?2) AS leases_open,
         (SELECT COUNT(*) FROM assignments WHERE same_node = 1) AS same_node`
    ).bind(t - 600_000, t - num(env, "LEASE_SECONDS") * 1000),
    env.DB.prepare("SELECT node_id, model, votes FROM node_models"),
  ]);
  const modelsByNode = {};
  for (const r of nodeModels.results) (modelsByNode[r.node_id] ??= {})[r.model] = r.votes;

  // One tally per task. "Waiting for judge" includes items waiting for a hint or for their re-checks;
  // "kept by judge" covers both the old judge-picks-the-answer rulings and hint-confirmed ones.
  const byTask = {};
  for (const r of items.results) {
    const task = r.task || "gsm8k";
    const it = (byTask[task] ??= {
      total: 0, honeypots: 0, honeypots_done: 0, pending: 0, unanimous: 0, judge_accepted: 0, accepted_correct: 0,
      disputed: 0, rechecking: 0, no_majority: 0, judge_rejected: 0, judges: {},
    });
    it.total += r.c;
    if (r.is_honeypot) {
      it.honeypots += r.c;
      if (r.status === "done") it.honeypots_done += r.c;
      continue;
    }
    if (r.status === "pending") it.pending += r.c;
    else if (r.status === "disputed" || r.status === "hinting") it.disputed += r.c;
    else if (r.status === "accepted") {
      it.accepted_correct += r.ok;
      if (r.resolved_by === "unanimous") it.unanimous += r.c;
      else {
        it.judge_accepted += r.c;
        const m = r.resolved_by.replace(/^(judge|hint):/, "");
        it.judges[m] = (it.judges[m] || 0) + r.c;
      }
    } else if (/rejected/.test(r.resolved_by || "")) it.judge_rejected += r.c;
    else it.no_majority += r.c;
  }
  // News has no answer key: its labels are scored against real prices afterwards, not here.
  if (byTask.news) byTask.news.accepted_correct = null;
  // Show the task that is running now (has pending work), else GSM8K.
  // ?task= picks a run; otherwise the one with pending work (the live one), else GSM8K.
  const task = (wanted && byTask[wanted] ? wanted : null) || Object.keys(byTask).find((k) => byTask[k].pending > 0) || "gsm8k";
  const it = byTask[task] || byTask.gsm8k;
  const judges = it.judges;

  const body = {
    now: t,
    config: {
      votes_per_item: num(env, "VOTES_PER_ITEM"),
      lease_seconds: num(env, "LEASE_SECONDS"),
      online_seconds: num(env, "ONLINE_SECONDS"),
      quarantine_below: num(env, "QUARANTINE_BELOW"),
      quarantine_after: 5,
      ms_per_billion: 250,
      pick_window: 100,
      judge_models: env.JUDGE_MODELS.split(",").map((m) => m.trim()),
      join_protected: Boolean(env.JOIN_CODE),
    },
    task,
    by_task: byTask,
    items: it,
    judges,
    activity: { ...activity.results[0], votes_total: nodes.results.reduce((s, n) => s + n.votes_n, 0) },
    nodes: nodes.results.map((n) => ({
      id: n.id.slice(0, 8),
      name: n.name,
      joined: n.created_at,
      last_seen: n.last_seen,
      online: n.last_seen > online,
      quarantined: Boolean(n.quarantined),
      votes: n.votes_n,
      honeypots: n.hp_n,
      honeypots_ok: n.hp_ok,
      too_fast: n.too_fast_n,
      agreed: n.agreed_n,
      credits: n.agreed_n + n.hp_ok,
      models: modelsByNode[n.id] || {},
    })),
    pairs: pairs.results,
    recent: recent.results,
  };
  statsCache[wanted || ""] = { at: t, body };
  return json(body);
}

// ---------- news signal board ----------

// The /news page: consensus labels next to what the stock actually did afterwards (3 trading days,
// vs Nifty). Returns are shown only for headlines that are already settled, never to nodes.
let newsCache = { at: 0, body: null };
async function newsBoard(env) {
  const t = Date.now();
  if (newsCache.body && t - newsCache.at < 5_000) return json(newsCache.body);
  const d3 = "(meta->'excess'->>'d3')::float";
  const base = "FROM items WHERE task = 'news' AND is_honeypot = 0 AND status = 'accepted'";
  const [counts, bySent, byCat, recent, judge, hints, hintEffect] = await env.DB.batch([
    env.DB.prepare(
      `SELECT status, (hint IS NOT NULL) AS hinted, COUNT(*) AS c FROM items WHERE task = 'news' AND is_honeypot = 0 GROUP BY 1, 2`
    ),
    env.DB.prepare(
      `SELECT split_part(final_answer, '|', 1) AS label, COUNT(*) AS n, AVG(${d3}) AS avg_d3,
              AVG(CASE WHEN ${d3} > 1 THEN 1.0 ELSE 0 END) AS up_rate, AVG(CASE WHEN ${d3} < -1 THEN 1.0 ELSE 0 END) AS down_rate
       ${base} GROUP BY 1 ORDER BY 1`
    ),
    env.DB.prepare(
      `SELECT split_part(final_answer, '|', 2) AS label, COUNT(*) AS n, AVG(${d3}) AS avg_d3 ${base} GROUP BY 1 ORDER BY 2 DESC`
    ),
    env.DB.prepare(
      `SELECT id, question, final_answer, resolved_by, meta->>'company' AS company, meta->>'symbol' AS symbol,
              meta->>'date' AS date, ${d3} AS d3 ${base} ORDER BY id DESC LIMIT 30`
    ),
    // How each settled headline was decided, and which judge wrote the hints.
    env.DB.prepare(
      `SELECT status, COALESCE(resolved_by, '') AS how, COUNT(*) AS n,
              SUM(CASE WHEN final_answer LIKE '%unclear%' THEN 1 ELSE 0 END) AS with_unclear
       FROM items WHERE task = 'news' AND is_honeypot = 0 AND status IN ('accepted', 'dropped') GROUP BY 1, 2`
    ),
    env.DB.prepare(
      `SELECT hint_model AS model, COUNT(*) AS n FROM items WHERE task = 'news' AND hint IS NOT NULL GROUP BY 1 ORDER BY 2 DESC`
    ),
    // What the hint round achieved: headlines and individual labels rescued vs lost.
    env.DB.prepare(
      `SELECT COUNT(*) AS finished,
              SUM(CASE WHEN status = 'accepted' AND resolved_by LIKE 'hint%' THEN 1 ELSE 0 END) AS full,
              SUM(CASE WHEN status = 'accepted' AND resolved_by = 'partial' THEN 1 ELSE 0 END) AS part,
              SUM(CASE WHEN status = 'dropped' THEN 1 ELSE 0 END) AS lost,
              COALESCE(SUM(array_length(string_to_array(split_fields, ','), 1)), 0) AS fields,
              COALESCE(SUM((SELECT COUNT(*) FROM unnest(string_to_array(split_fields, ',')) f
                            WHERE split_part(final_answer, '|', f::int + 1) NOT IN ('unclear', '?', ''))), 0) AS fields_settled
       FROM items WHERE task = 'news' AND is_honeypot = 0 AND hint IS NOT NULL AND status IN ('accepted', 'dropped')`
    ),
  ]);
  const c = { total: 0, pending: 0, rechecking: 0, waiting_hint: 0, kept: 0, dropped: 0 };
  for (const r of counts.results) {
    c.total += r.c;
    if (r.status === "pending") r.hinted ? (c.rechecking += r.c) : (c.pending += r.c);
    else if (r.status === "hinting") c.waiting_hint += r.c;
    else if (r.status === "accepted") c.kept += r.c;
    else c.dropped += r.c;
  }
  newsCache = { at: t, body: { now: t, counts: c, by_sentiment: bySent.results, by_category: byCat.results, recent: recent.results, judge: judge.results, hint_models: hints.results, hint_effect: hintEffect.results[0] } };
  return json(newsCache.body);
}

// One JSON line per accepted item, with full provenance. This is the refined dataset (student D).
async function exportAccepted(env) {
  const { results: items } = await env.DB.prepare(
    "SELECT id, question, final_answer, score, resolved_by FROM items WHERE status = 'accepted' AND is_honeypot = 0 ORDER BY id"
  ).all();
  const { results: votes } = await env.DB.prepare(
    `SELECT v.item_id, v.node_id, v.model, v.harness, v.answer, v.reasoning, v.server_ms
     FROM votes v JOIN items i ON i.id = v.item_id WHERE i.status = 'accepted' AND i.is_honeypot = 0 ORDER BY v.id`
  ).all();
  const byItem = {};
  for (const v of votes) (byItem[v.item_id] ||= []).push(v);

  const lines = items.map((it) => {
    const vs = byItem[it.id] || [];
    const winner = vs.find((v) => v.answer === it.final_answer);
    return JSON.stringify({
      id: it.id,
      question: it.question,
      reasoning: winner?.reasoning ?? "",
      answer: it.final_answer,
      score: it.score,
      resolved_by: it.resolved_by,
      provenance: vs.map(({ node_id, model, harness, answer, server_ms }) => ({ node_id, model, harness, answer, server_ms })),
    });
  });
  return new Response(lines.join("\n") + "\n", { headers: { "content-type": "application/x-ndjson" } });
}
