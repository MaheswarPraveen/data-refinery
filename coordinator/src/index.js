// Coordinator: hands out jobs, collects signed votes, runs consensus, calls the judge.
// Runs on Cloudflare Workers; state lives in the D1 database bound as env.DB.

import dashboard from "./dashboard.html";
import newsPage from "./news.html";
import homePage from "./home.html";
import tradePage from "./trade.html";
import futurePage from "./future.html";
import hardPage from "./hard.html";
import dataPage from "./data.html";
import trainingPage from "./training.html";
import agentTools from "./agent_tools.json";
import livePage from "./live.html";
import { agentStep } from "./agent.js";
import { pickWorking } from "./steps.js";
import nodeScript from "../../node/node.py"; // served at /node.py so notebooks can just download it
import kaggleScript from "../../node/kaggle.sh"; // one-line Kaggle launcher, served at /kaggle.sh
import colabScript from "../../node/colab.sh"; // Colab A100 launcher: nodes 1-3 of the mixed run, served at /colab.sh
import kaggleMixedScript from "../../node/kaggle_mixed.sh"; // Kaggle T4x2 launcher: nodes 4-5 of the mixed run
import kaggleJudgeScript from "../../node/kaggle_judge.sh"; // Kaggle T4x2: Muse Glimmer judge node
import judgeScript from "../../node/judge.py"; // the judge node itself, served at /judge.py

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
      await gpqaSwitch(env).catch(() => {});
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
      if (req.method === "POST" && pathname === "/api/hint/job") return await hintJob(req, env);
      if (req.method === "POST" && pathname === "/api/hint/submit") return await hintSubmit(req, env);
      if (req.method === "GET" && pathname === "/api/stats") return await stats(env, new URL(req.url).searchParams.get("task"));
      if (req.method === "GET" && pathname === "/api/news") return await newsBoard(env);
      if (req.method === "GET" && pathname === "/api/trade") return await tradeBoard(env);
      if (req.method === "GET" && pathname === "/api/audit/live") return await auditLive(env);
      if (req.method === "GET" && pathname === "/api/audit") return await auditBoard(env, new URL(req.url).searchParams.get("run"));
      if (req.method === "POST" && pathname === "/api/agent/step") return await agentStep(req, env);
      if (req.method === "GET" && pathname === "/data") return new Response(dataPage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/api/hard") return await hardBoard(env);
      if (req.method === "GET" && pathname === "/hard") return new Response(hardPage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/api/gpqa") return await gpqaBoard(env);
      if (req.method === "GET" && pathname === "/api/ft/data") return await ftData(env);
      if (req.method === "GET" && pathname === "/api/ft/news") return await ftNews(env, new URL(req.url).searchParams.get("all") === "1");
      if (req.method === "POST" && pathname === "/api/ft/telemetry") return await ftTelemetryPost(req, env);
      if (req.method === "GET" && pathname === "/api/ft/telemetry") return await ftTelemetryGet(env);
      if (req.method === "GET" && pathname === "/agent_tools.json") return json(agentTools);
      if (req.method === "GET" && pathname === "/live") return new Response(livePage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/training") return new Response(trainingPage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/api/ft/pairs") return await ftPairs(env);
      if (req.method === "GET" && pathname === "/api/ft/results") return await ftResults(env);
      if (req.method === "POST" && pathname === "/api/ft/result") return await ftSubmit(req, env);
      if (req.method === "GET" && pathname === "/api/agreement") return await agreementCurve(env, new URL(req.url).searchParams.get("task") || "gsm8k_mixed");
      if (req.method === "GET" && pathname === "/future") return new Response(futurePage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/trade") return new Response(tradePage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/api/scoreboard") return await scoreboard(env, new URL(req.url).searchParams.get("task") || "gsm8k");
      if (req.method === "GET" && pathname === "/news") return new Response(newsPage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/api/export") return await exportAccepted(env);
      // Rejudging spends the free Workers AI allowance, so only someone with the join code may trigger it.
      if (req.method === "POST" && pathname === "/api/rejudge") {
        if (env.JOIN_CODE && req.headers.get("x-join-code") !== env.JOIN_CODE) return json({ error: "wrong join code" }, 403);
        return await rejudge(env);
      }
      if (req.method === "GET" && pathname === "/kaggle_judge.sh") return new Response(kaggleJudgeScript, { headers: { "content-type": "text/x-shellscript; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/judge.py") return new Response(judgeScript, { headers: { "content-type": "text/x-python; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/kaggle_mixed.sh") return new Response(kaggleMixedScript, { headers: { "content-type": "text/x-shellscript; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/colab.sh") return new Response(colabScript, { headers: { "content-type": "text/x-shellscript; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/kaggle.sh") return new Response(kaggleScript, { headers: { "content-type": "text/x-shellscript; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/node.py") return new Response(nodeScript, { headers: { "content-type": "text/x-python; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && pathname === "/") return new Response(homePage, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
      if (req.method === "GET" && (pathname === "/gsm8k" || pathname === "/gsm8k-mixed" || pathname === "/gpqa" || pathname === "/audit")) return new Response(dashboard, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" } });
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
// Trade answers: one decision per overnight headline.
export function normalizeTrade(raw) {
  const s = String(raw ?? "").trim().toUpperCase();
  return ["BUY", "SELL", "SKIP"].includes(s) ? s : null;
}

export function normalizeNews(raw) {
  const parts = String(raw ?? "").toLowerCase().split("|").map((s) => s.trim().replace(/[\s-]+/g, "_"));
  if (parts.length !== NEWS_FIELDS.length || parts.some((p, i) => !NEWS_FIELDS[i].includes(p))) return null;
  return parts.join("|");
}

// Agent answers are one tool call {"tool", "args"}. Canonical form: known tool name, args sorted, string values
// trimmed and lower-cased, so "Close Gmail" and "close gmail" count as the same vote. Unknown tools are rejected.
const AGENT_TOOLS = new Set(agentTools.tools.map((t) => t.name));
export function normalizeAgent(raw) {
  let c; try { c = typeof raw === "string" ? JSON.parse(raw) : raw; } catch { return null; }
  if (!c || !AGENT_TOOLS.has(c.tool)) return null;
  const args = c.args && typeof c.args === "object" ? c.args : {};
  const clean = {};
  for (const k of Object.keys(args).sort()) {
    const v = args[k];
    clean[k] = typeof v === "string" ? v.trim().toLowerCase() : v;
  }
  return JSON.stringify({ tool: c.tool, args: clean });
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
  const list = (Array.isArray(canDo) && canDo.length ? canDo : ["gsm8k"]).map(String);
  if (list.includes("gpqa") && !list.includes("audit")) list.push("audit"); // GPQA nodes roll over to the audit run
  if (list.includes("gsm8k_mixed") && !list.includes("audit")) list.push("audit"); // ten-model nodes roll over to the 1,000-problem audit
  const tasks = list.join(",");
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
    context: item.task === "news" || item.task === "trade" ? { company: meta.company, industry: meta.industry } : undefined,
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
  const kind = item.task === "news" ? "news" : item.task === "trade" ? "trade" : item.task === "gpqa" ? "letter" : item.task === "agent" ? "agent" : "number";
  const norm = kind === "agent" ? normalizeAgent(answer) : kind === "news" ? normalizeNews(answer) : kind === "trade" ? normalizeTrade(answer)
    : kind === "letter" ? (/^[A-D]$/i.test(String(answer).trim()) ? String(answer).trim().toUpperCase() : null) : normalize(answer);
  if (norm === null || (kind === "number" && !/^-?\d+(\.\d+)?$/.test(norm)))
    return json({ error: { news: "answer must be sentiment|category", trade: "answer must be BUY, SELL or SKIP", letter: "answer must be A, B, C or D", agent: "answer must be one known tool call", number: "answer must be a number" }[kind] }, 422);
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

  // Votes per round: 3 (news, first maths run) or 5 (mixed run: one vote per node, 3 models per node).
  // 3 votes (news, first maths run): all 3 = keep, otherwise hint; round 2 needs all 3.
  // 5+ votes (one per node): about 80% agree = keep (5 of 5: 4+, 6: 5+, 10: 8+), about 65% = one hint
  // (5: 3, 6: 4, 10: 7), less = thrown out. Round 2 keeps only on the 80% bar.
  const R = item.hint ? need / 2 : need;
  const decide = (vals, round) => {
    const tally = {};
    for (const x of vals) tally[x] = (tally[x] || 0) + 1;
    const [top, count] = Object.entries(tally).sort((a, b) => b[1] - a[1])[0];
    if (R >= 5) {
      if (count >= Math.ceil(0.8 * R)) return ["keep", top];
      return round === 1 && count >= Math.round(0.65 * R) ? ["hint", top] : ["drop"];
    }
    return count === R ? ["keep", top] : round === 1 ? ["hint", top] : ["drop"];
  };

  if (!item.hint) {
    // ---- round 1 ----
    const first = votes.filter((v) => !v.hinted).slice(0, R).map((v) => split(v.answer));
    const final = [], disagreed = [];
    let dropped = false;
    for (let f = 0; f < nFields; f++) {
      const [kind, top] = decide(first.map((v) => v[f]), 1);
      if (kind === "keep") final.push(top);
      else if (kind === "hint") { final.push("?"); disagreed.push(f); }
      else { final.push("unclear"); if (!isNews) dropped = true; }
    }
    const answer = final.join("|");
    if (dropped || final.every((x) => x === "unclear")) {
      await claim(env, item.id, "pending", "dropped", null, null, "no-majority");
    } else if (disagreed.length) {
      // The per-minute cron writes the hint (judging here would share this request's subrequest budget).
      await env.DB.prepare("UPDATE items SET status = 'hinting', final_answer = ?, split_fields = ?, hint_wait_since = ? WHERE id = ? AND status = 'pending'")
        .bind(answer, disagreed.join(","), Date.now(), item.id).run();
    } else {
      if (await claim(env, item.id, "pending", "accepted", answer, 1.0, "unanimous")) await creditAgreement(env, item.id, answer);
    }
    return;
  }

  // ---- round 2: fresh votes made with the hint (3 must all agree; of 6, 5+ must agree) ----
  const rechecks = votes.filter((v) => v.hinted).slice(0, R).map((v) => split(v.answer));
  if (rechecks.length < R) return;
  const final = split(item.final_answer);
  let anyKept = false, anyLost = false;
  for (const f of String(item.split_fields || "").split(",").filter(Boolean).map(Number)) {
    const [kind, top] = decide(rechecks.map((r) => r[f]), 2);
    if (kind === "keep") { final[f] = top; anyKept = true; }
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

// Judge node API: a registered node running Muse Glimmer leases an item waiting for a hint, gets the
// prompt (built here, so leak rules stay server-side), and sends back a signed hint.
async function hintJob(req, env) {
  const { node_id } = await req.json();
  const node = await touchNode(env, node_id);
  if (!node) return json({ error: "unknown node" }, 401);
  const t = Date.now();
  const item = await env.DB.prepare(
    `UPDATE items SET hint_lease = ? WHERE id = (SELECT id FROM items WHERE status = 'hinting' AND COALESCE(hint_lease, 0) < ? ORDER BY id LIMIT 1)
     AND status = 'hinting' RETURNING id, question, task, meta, split_fields`
  ).bind(t + 300_000, t).first();
  if (!item) return json({ wait: true, retry_after: 15 });
  const { prompt } = await hintPrompt(env, item);
  return json({ item_id: item.id, prompt });
}

async function hintSubmit(req, env) {
  const b = await req.json();
  const node = await touchNode(env, b.node_id);
  if (!node) return json({ error: "unknown node" }, 401);
  const hint = String(b.hint ?? "");
  if (b.sig !== (await hmacHex(node.secret, `${b.item_id}|${hint}`))) return json({ error: "bad signature" }, 401);
  if (hint.trim().length < 10) return json({ error: "empty hint" }, 422);
  const item = await env.DB.prepare("SELECT id, question, task, meta, split_fields FROM items WHERE id = ? AND status = 'hinting'").bind(b.item_id).first();
  if (!item) return json({ error: "item no longer waiting for a hint" }, 409);
  const { leaks } = await hintPrompt(env, item);
  const ok = await saveHint(env, item, hint, String(b.model || "judge-node").slice(0, 80), leaks);
  return json({ ok });
}

// For a 2-vs-1 split the judge writes a HINT, never the answer: what to double-check. Anything
// that could leak the answer (a candidate number, a candidate label) is blanked out before nodes see it.
// The hint prompt and the words it must never contain (the candidate answers / labels).
async function hintPrompt(env, item) {
  const { results: votes } = await env.DB.prepare(
    "SELECT answer FROM votes WHERE item_id = ? AND hinted = 0 AND too_fast = 0 ORDER BY id LIMIT 6"
  ).bind(item.id).all();
  const isNews = item.task === "news";
  const fields = String(item.split_fields || "0").split(",").map(Number);
  let prompt, leaks;
  if (item.task === "gpqa") {
    // Multiple choice: the hint must not name or point at an option letter.
    prompt =
      `A graduate-level science question with options A-D:
${item.question}

` +
      `Expert solvers did not agree on the answer. Do NOT say which option is correct, do NOT mention option ` +
      `letters, and do not restate any option. In 2-4 sentences, name the key principle, formula or common trap ` +
      `that decides this question.`;
    leaks = ["LETTER"];
  } else if (item.task === "trade") {
    const meta = item.meta || {};
    prompt =
      `An overnight headline about the NSE-listed company ${meta.company || ""}:
"${item.question}"

` +
      `Five independent traders did not agree on what to do at the next market open. Do NOT recommend an action ` +
      `and do not use the words buy, sell, skip, long, short, bullish or bearish. In 1-3 sentences, only point out ` +
      `what a careful trader should weigh: is this genuinely new, is it material to this company, is it confirmed ` +
      `or speculative, and could it already be known to the market.`;
    leaks = ["buy", "sell", "skip", "long", "short", "bullish", "bearish", "upside", "downside"];
  } else if (item.task === "agent") {
    prompt =
      `A user said to an AI assistant that controls their Android phone:
"${item.question}"

` +
      `Assistants chose different tool calls. Do NOT name a tool and do not write any tool call. In 1-3 sentences, ` +
      `point out what the user actually wants and any detail that is easy to miss (which app, which setting, ` +
      `whether a question back to the user is needed first).`;
    leaks = [...AGENT_TOOLS];
  } else if (isNews) {
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
      `Math word problem:\n${item.question}\n\nThe solvers did not agree; their answers were ${cands.join(" and ")}. ` +
      `Do NOT give the final answer and do not repeat those numbers. In 2-4 sentences, point out the one step ` +
      `or detail a solver should double-check (a quantity that is easy to misread, a unit, which operation applies).`;
    leaks = cands;
  }
  return { prompt, leaks };
}

// Store a hint: strip thinking and answer lines, blank out leaks, and open the re-check round.
async function saveHint(env, item, raw, model, leaks) {
  let hint = String(raw).replace(/<think>[\s\S]*?<\/think>/g, "").replace(/ANSWER:[^\n]*/gi, "").trim();
  hint = scrubLeaks(hint, leaks).slice(0, 700);
  // The hint round doubles the votes needed: 3 -> 6, 5 -> 10.
  const r = await env.DB.prepare("UPDATE items SET hint = ?, hint_model = ?, need = need * 2, status = 'pending', hint_lease = NULL WHERE id = ? AND status = 'hinting'")
    .bind(hint, model, item.id).run();
  return r.meta.changes > 0;
}

// Fallback judge (cron): API models, used when no judge node has picked the item up.
async function writeHint(env, item) {
  const { prompt, leaks } = await hintPrompt(env, item);
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
    await saveHint(env, item, hint, model, leaks);
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
    if (s === "LETTER") {
      // Multiple choice: blank any reference to an option letter ("option B", "(C)", "answer is D", "B)").
      out = out.replace(/\b(option|choice|answer(?: is)?|letter|pick|select)\s*[:(]?\s*\(?[A-D]\)?(?![a-z])/gi, "…")
        .replace(/\(([A-D])\)/g, "(…)").replace(/(^|\s)([A-D])[).:](?=\s)/g, "$1…");
      continue;
    }
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
  // Muse Glimmer (the judge node) is the primary judge. While it's online, the API judges only rescue
  // hints that have waited 10+ minutes; when it's offline they take anything that waited 4+ minutes.
  const judge = await env.DB.prepare("SELECT MAX(last_seen) AS seen FROM nodes WHERE name LIKE 'judge%'").first();
  const judgeOnline = judge?.seen && Date.now() - judge.seen < 120_000;
  const waitMs = judgeOnline ? 600_000 : 240_000;
  const { results } = await env.DB.prepare(
    // A judge node (Muse Glimmer on Kaggle) gets first claim; the API fallback only takes items that
    // have waited 4+ minutes and aren't held by a judge node.
    "SELECT id, question, task, meta, split_fields FROM items WHERE status = 'hinting' AND COALESCE(hint_wait_since, 0) < ? AND (COALESCE(hint_lease, 0) < ? OR COALESCE(hint_wait_since, 0) < ?) ORDER BY id LIMIT ?"
  ).bind(Date.now() - waitMs, Date.now(), Date.now() - 600_000, n).all();  // a judge-node lease can't block an item past 10 min
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
  const [nodes, pairs, recent, activity, nodeModels, singles] = await env.DB.batch([
    // Per-node running totals: a handful of rows, not every vote.
    env.DB.prepare(
      "SELECT id, name, created_at, last_seen, quarantined, votes_n, hp_n, hp_ok, too_fast_n, agreed_n FROM nodes ORDER BY created_at"
    ),
    // Per run when ?task= is given (a run must not show another run's models), else the global totals.
    wanted
      ? env.DB.prepare("SELECT v.model, v.harness, COUNT(*) AS votes, SUM(CASE WHEN v.answer = i.gold THEN 1 ELSE 0 END) AS correct FROM votes v JOIN items i ON i.id = v.item_id WHERE i.task = ? GROUP BY 1, 2 ORDER BY 1, 2").bind(wanted)
      : env.DB.prepare("SELECT model, harness, votes, correct FROM pair_stats ORDER BY model, harness"),
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
    wanted
      ? env.DB.prepare(`SELECT v.node_id, v.model, COUNT(*) AS votes, SUM(CASE WHEN i.status = 'accepted' AND v.answer = i.final_answer THEN 1 ELSE 0 END) AS agreed,
                SUM(i.is_honeypot) AS hp, SUM(CASE WHEN i.is_honeypot = 1 AND v.answer = i.gold THEN 1 ELSE 0 END) AS hp_ok,
                SUM(v.too_fast) AS too_fast
         FROM votes v JOIN items i ON i.id = v.item_id WHERE i.task = ? GROUP BY 1, 2`).bind(wanted)
      : env.DB.prepare("SELECT node_id, model, votes FROM node_models"),
    // How often a single vote alone is right, per run (the baseline consensus is compared against).
    env.DB.prepare("SELECT i.task, COUNT(*) AS n, SUM(CASE WHEN v.answer = i.gold THEN 1 ELSE 0 END) AS ok FROM votes v JOIN items i ON i.id = v.item_id WHERE i.is_honeypot = 0 AND i.gold <> '' GROUP BY i.task"),
  ]);
  const modelsByNode = {};
  const runByNode = {}; // with ?task=: this run's per-node totals (the nodes table is lifetime)
  for (const r of nodeModels.results) {
    (modelsByNode[r.node_id] ??= {})[r.model] = r.votes;
    const n = (runByNode[r.node_id] ??= { votes: 0, agreed: 0, hp: 0, hp_ok: 0, too_fast: 0 });
    for (const k in n) n[k] += Number(r[k] || 0);
  }

  // One tally per task. "Waiting for judge" includes items waiting for a hint or for their re-checks;
  // "kept by judge" covers both the old judge-picks-the-answer rulings and hint-confirmed ones.
  const byTask = {};
  for (const r of items.results) {
    const task = r.task || "gsm8k";
    const it = (byTask[task] ??= {
      total: 0, honeypots: 0, honeypots_done: 0, pending: 0, unanimous: 0, judge_accepted: 0, accepted_correct: 0,
      disputed: 0, rechecking: 0, no_majority: 0, judge_rejected: 0, judges: {},
    });
    if (r.status === "parked") continue; // set aside (e.g. a run cut short): not part of this run
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
  for (const r of singles.results) if (byTask[r.task]) Object.assign(byTask[r.task], { single_votes: r.n, single_ok: r.ok });
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
    // With ?task=, vote counts are this run's only (nodes table holds lifetime totals across all runs).
    activity: { ...activity.results[0], votes_total: wanted ? Object.values(modelsByNode).reduce((s, m) => s + Object.values(m).reduce((a, b) => a + b, 0), 0) : nodes.results.reduce((s, n) => s + n.votes_n, 0) },
    // With ?task=, only nodes that voted in that run (or are online now); old sessions drop out.
    // Judge nodes write hints, they never vote, so they are left out of the voter table.
    nodes: nodes.results.filter((n) => !/^judge/.test(n.name) && (!wanted || modelsByNode[n.id] || n.last_seen > online)).map((n) => {
      const R = wanted ? runByNode[n.id] || { votes: 0, agreed: 0, hp: 0, hp_ok: 0, too_fast: 0 } : null;
      return {
      id: n.id.slice(0, 8),
      name: n.name,
      joined: n.created_at,
      last_seen: n.last_seen,
      online: n.last_seen > online,
      quarantined: Boolean(n.quarantined),
      votes: R ? R.votes : n.votes_n,
      honeypots: R ? R.hp : n.hp_n,
      honeypots_ok: R ? R.hp_ok : n.hp_ok,
      too_fast: R ? R.too_fast : n.too_fast_n,
      agreed: R ? R.agreed : n.agreed_n,
      credits: R ? R.agreed + R.hp_ok : n.agreed_n + n.hp_ok,
      models: modelsByNode[n.id] || {},
    }; }),
    pairs: pairs.results,
    recent: recent.results,
  };
  statsCache[wanted || ""] = { at: t, body };
  return json(body);
}

// ---------- model scoreboard ----------

// Per model: accuracy against the answer key, speed, how often it sided with the kept answer, how many
// of its wrong votes the network overruled, and how often it was right but outvoted. Plus a dataset
// summary: how many wrong votes were cast and how many of them still reached the kept data.
let scoreCache = {};
async function scoreboard(env, task) {
  const t = Date.now();
  const c = scoreCache[task];
  if (c && t - c.at < 15_000) return json(c.body);
  const base = "FROM votes v JOIN items i ON i.id = v.item_id WHERE i.task = ? AND i.is_honeypot = 0";
  const [models, totals] = await env.DB.batch([
    env.DB.prepare(
      `SELECT v.model, COUNT(*) AS votes,
              SUM(CASE WHEN v.answer = i.gold THEN 1 ELSE 0 END) AS correct,
              ROUND(AVG(v.server_ms)) AS avg_ms,
              SUM(CASE WHEN i.status = 'accepted' THEN 1 ELSE 0 END) AS on_settled,
              SUM(CASE WHEN i.status = 'accepted' AND v.answer = i.final_answer THEN 1 ELSE 0 END) AS sided_with_kept,
              SUM(CASE WHEN v.answer <> i.gold AND i.status = 'accepted' AND i.final_answer = i.gold THEN 1 ELSE 0 END) AS wrong_overruled,
              SUM(CASE WHEN v.answer <> i.gold AND i.status = 'dropped' THEN 1 ELSE 0 END) AS wrong_dropped,
              SUM(CASE WHEN v.answer = i.gold AND i.status = 'accepted' AND i.final_answer <> i.gold THEN 1 ELSE 0 END) AS right_outvoted,
              SUM(CASE WHEN v.hinted = 1 THEN 1 ELSE 0 END) AS hinted_votes,
              SUM(CASE WHEN v.hinted = 1 AND v.answer = i.gold THEN 1 ELSE 0 END) AS hinted_correct
       ${base} GROUP BY v.model ORDER BY 3 DESC`
    ).bind(task),
    env.DB.prepare(
      `SELECT COUNT(*) AS votes, SUM(CASE WHEN v.answer = i.gold THEN 1 ELSE 0 END) AS correct,
              SUM(CASE WHEN v.answer <> i.gold THEN 1 ELSE 0 END) AS wrong,
              SUM(CASE WHEN v.answer <> i.gold AND i.status = 'accepted' AND v.answer = i.final_answer THEN 1 ELSE 0 END) AS wrong_kept,
              (SELECT COUNT(*) FROM items WHERE task = ? AND is_honeypot = 0 AND status = 'accepted') AS kept,
              (SELECT COUNT(*) FROM items WHERE task = ? AND is_honeypot = 0 AND status = 'accepted' AND final_answer = gold) AS kept_correct
       ${base}`
    ).bind(task, task, task),
  ]);
  const body = { task, now: t, models: models.results, totals: totals.results[0] };
  scoreCache[task] = { at: t, body };
  return json(body);
}

// ---------- trading board ----------

// Every settled trade decision with its later price moves (server-side meta, revealed only after the
// decision is final), plus each model's own votes so single models can be compared with the consensus.
let tradeCache = { at: 0, body: null };
async function tradeBoard(env) {
  const t = Date.now();
  if (tradeCache.body && t - tradeCache.at < 5_000) return json(tradeCache.body);
  const [items, votes, counts, vt] = await env.DB.batch([
    env.DB.prepare(
      `SELECT id, question, status, final_answer, resolved_by, meta->>'company' AS company, meta->>'symbol' AS symbol,
              meta->>'date' AS date, (meta->>'r20')::float AS r20, (meta->>'ex1')::float AS ex1,
              (meta->>'ex3')::float AS ex3, (meta->>'ex7')::float AS ex7
       FROM items WHERE task = 'trade' AND status IN ('accepted', 'dropped') ORDER BY meta->>'date', id`
    ),
    env.DB.prepare(
      `SELECT v.item_id, v.model, v.harness, v.answer FROM votes v JOIN items i ON i.id = v.item_id
       WHERE i.task = 'trade' AND i.status IN ('accepted', 'dropped') AND v.hinted = 0`
    ),
    env.DB.prepare(`SELECT status, COUNT(*) AS n FROM items WHERE task = 'trade' GROUP BY 1`),
    env.DB.prepare(
      `SELECT COUNT(*) AS votes, SUM(v.hinted) AS hinted_votes, COUNT(DISTINCT CASE WHEN v.hinted = 1 THEN v.item_id END) AS hinted_items,
              COUNT(DISTINCT CASE WHEN v.hinted = 1 AND i.status = 'accepted' THEN v.item_id END) AS hint_saved
       FROM votes v JOIN items i ON i.id = v.item_id WHERE i.task = 'trade' AND i.status IN ('accepted', 'dropped')`
    ),
  ]);
  const c = {};
  for (const r of counts.results) c[r.status] = r.n;
  tradeCache = { at: t, body: { now: t, counts: c, totals: vt.results[0], items: items.results, votes: votes.results } };
  return json(tradeCache.body);
}

// ---------- fine-tuning: training data out, scores back in ----------

// One row per unique maths problem the network accepted: the refined answer (a vote that matched the
// consensus) and a raw answer (one random first-round vote, right or wrong), for training side by side.
async function ftData(env) {
  const { results } = await env.DB.prepare(
    `WITH it AS (SELECT DISTINCT ON (question) id, question, final_answer FROM items
                 WHERE task IN ('gsm8k', 'gsm8k_mixed') AND status = 'accepted' AND is_honeypot = 0 ORDER BY question, id)
     SELECT it.id, it.question, it.final_answer,
            (SELECT json_agg(v.reasoning ORDER BY v.id) FROM votes v WHERE v.item_id = it.id AND v.answer = it.final_answer) AS agreeing,
            r.reasoning AS raw, r.answer AS raw_answer
     FROM it LEFT JOIN LATERAL (SELECT reasoning, answer FROM votes v WHERE v.item_id = it.id AND v.hinted = 0
                                ORDER BY md5(v.id::text) LIMIT 1) r ON true
     ORDER BY it.id`
  ).all();
  // the working that goes into the dataset is step-checked: of the votes that agreed with the consensus, keep one
  // whose every calculation recomputes correctly (items with no clean working are left out)
  const rows = results.map(({ agreeing, ...r }) => ({ ...r, refined: pickWorking(typeof agreeing === "string" ? JSON.parse(agreeing) : agreeing) }))
    .filter((r) => r.refined);
  const body = rows.map((r) => JSON.stringify(r)).join("\n") + "\n";
  return new Response(body, { headers: { "content-type": "application/x-ndjson", "cache-control": "no-store" } });
}

// Preference pairs for DPO (as in Self-Consistency Preference Optimization): for each accepted maths problem,
// "chosen" = a vote that agreed with the consensus, "rejected" = an outvoted wrong answer (up to 2 per problem).
// GSM8K test problems (the audit runs) are left out, so the GSM-Symbolic test stays unseen.
async function ftPairs(env) {
  const { results } = await env.DB.prepare(
    `WITH it AS (SELECT DISTINCT ON (question) id, question, final_answer FROM items
                 WHERE task IN ('gsm8k', 'gsm8k_mixed') AND status = 'accepted' AND is_honeypot = 0 ORDER BY question, id)
     SELECT it.id, it.question, it.final_answer,
            (SELECT json_agg(v.reasoning ORDER BY v.hinted, v.id) FROM votes v WHERE v.item_id = it.id AND v.answer = it.final_answer AND v.reasoning <> '') AS agreeing,
            w.reasoning AS rejected, w.answer AS rejected_answer
     FROM it JOIN LATERAL (SELECT DISTINCT ON (answer) reasoning, answer FROM votes v
                           WHERE v.item_id = it.id AND v.answer IS DISTINCT FROM it.final_answer AND v.answer IS NOT NULL AND v.reasoning <> ''
                           ORDER BY answer, v.id LIMIT 2) w ON true
     ORDER BY it.id`
  ).all();
  // "chosen" must have step-checked working (see ftData); the rejected side is a wrong answer anyway
  const rows = results.map(({ agreeing, ...r }) => ({ ...r, chosen: pickWorking(typeof agreeing === "string" ? JSON.parse(agreeing) : agreeing) }))
    .filter((r) => r.chosen);
  const body = rows.map((r) => JSON.stringify(r)).join("\n") + "\n";
  return new Response(body, { headers: { "content-type": "application/x-ndjson", "cache-control": "no-store" } });
}

// Refined headline labels for fine-tuning: the network's accepted sentiment for each headline.
async function ftNews(env, all = false) {
  const { results } = await env.DB.prepare(
    "SELECT id, question AS headline, split_part(final_answer, '|', 1) AS sentiment FROM items WHERE task = 'news' AND status = 'accepted' AND is_honeypot = 0 ORDER BY id"
  ).all();
  // Balanced by default: the accepted headlines lean positive (884 / 203 / 244), and a model trained on that learns
  // to rarely say "negative" or "neutral". Each label is cut to the size of the rarest one, picked by a fixed
  // shuffle so the export is repeatable. ?all=1 returns everything.
  let rows = results.filter((r) => ["positive", "negative", "neutral"].includes(r.sentiment));
  if (!all) {
    const by = { positive: [], negative: [], neutral: [] };
    for (const r of rows) by[r.sentiment].push(r);
    const n = Math.min(...Object.values(by).map((v) => v.length));
    const mix = (id) => (Math.imul(Number(id) ^ 0x9e3779b9, 2654435761) >>> 0);
    rows = Object.values(by).flatMap((v) => v.sort((a, b) => mix(a.id) - mix(b.id)).slice(0, n)).sort((a, b) => a.id - b.id);
  }
  return new Response(rows.map(({ id, ...r }) => JSON.stringify(r)).join("\n") + "\n", { headers: { "content-type": "application/x-ndjson", "cache-control": "no-store" } });
}

// Live training telemetry: the notebook posts a snapshot every 30 s (stage, GPU, loss curve, scores so far);
// the /training page polls the latest one. One row per run, overwritten each time.
async function ftTelemetryPost(req, env) {
  const b = await req.json().catch(() => ({}));
  if (!env.JOIN_CODE || b.join_code !== env.JOIN_CODE) return json({ error: "bad join code" }, 403);
  const run = String(b.run || "run").slice(0, 40), data = JSON.stringify(b.data || {});
  if (data.length > 200_000) return json({ error: "too big" }, 413);
  await env.DB.prepare("INSERT INTO ft_telemetry (run, data, updated) VALUES (?, ?::jsonb, ?) ON CONFLICT (run) DO UPDATE SET data = EXCLUDED.data, updated = EXCLUDED.updated")
    .bind(run, data, Date.now()).run();
  return json({ ok: true });
}
async function ftTelemetryGet(env) {
  const { results } = await env.DB.prepare("SELECT run, data, updated FROM ft_telemetry ORDER BY updated DESC LIMIT 1").all();
  return json({ now: Date.now(), latest: results[0] || null });
}
async function ftSubmit(req, env) {
  const b = await req.json().catch(() => ({}));
  if (!env.JOIN_CODE || b.join_code !== env.JOIN_CODE) return json({ error: "bad join code" }, 403);
  const variant = String(b.variant || "").slice(0, 40), correct = Number(b.correct), total = Number(b.total);
  if (!variant || !Number.isFinite(correct) || !Number.isFinite(total) || total <= 0) return json({ error: "bad result" }, 400);
  await env.DB.prepare("INSERT INTO ft_results (variant, correct, total, train_n, meta, created_at) VALUES (?, ?, ?, ?, ?::jsonb, ?)")
    .bind(variant, correct, total, Number(b.train_n) || null, JSON.stringify(b.meta || {}), Date.now()).run();
  return json({ ok: true });
}

async function ftResults(env) {
  const { results } = await env.DB.prepare(
    "SELECT DISTINCT ON (variant) variant, correct, total, train_n, meta, created_at FROM ft_results ORDER BY variant, created_at DESC"
  ).all();
  return json({ results });
}

// ---------- agreement vs accuracy ----------

// For items with exactly 5 first-round votes: how often the most common answer is right, by how many
// of the 5 gave it. This is the whole idea in one curve: more agreement, more often right.
const agreeCache = {};
async function agreementCurve(env, task) {
  const t = Date.now();
  if (agreeCache[task] && t - agreeCache[task].at < 60_000) return json(agreeCache[task].body);
  const { results } = await env.DB.prepare(
    `WITH c AS (SELECT v.item_id, v.answer, COUNT(*) AS n FROM votes v JOIN items i ON i.id = v.item_id
                WHERE i.task = ? AND v.hinted = 0 AND v.too_fast = 0 AND i.is_honeypot = 0 GROUP BY 1, 2),
          t AS (SELECT item_id, SUM(n) AS tot, MAX(n) AS top FROM c GROUP BY 1),
          w AS (SELECT DISTINCT ON (c.item_id) c.item_id, c.answer, t.top, t.tot FROM c JOIN t USING (item_id)
                WHERE c.n = t.top ORDER BY c.item_id, c.answer)
     SELECT w.top AS agree, COUNT(*) AS items, SUM(CASE WHEN w.answer = i.gold THEN 1 ELSE 0 END) AS correct
     FROM w JOIN items i ON i.id = w.item_id WHERE w.tot = 5 GROUP BY 1 ORDER BY 1 DESC`
  ).bind(task).all();
  const body = { task, now: t, buckets: results };
  agreeCache[task] = { at: t, body };
  return json(body);
}

// ---------- GPQA per-question breakdown ----------

// Every decided GPQA question with its vote split, the agreed answer and the official one.
// Question text is never returned: GPQA asks that its questions stay out of public pages.
async function gpqaBoard(env) {
  const [items, votes] = await env.DB.batch([
    env.DB.prepare("SELECT id, status, final_answer, gold, hint IS NOT NULL AS hinted FROM items WHERE task = 'gpqa' AND status IN ('accepted', 'dropped') ORDER BY id"),
    env.DB.prepare("SELECT v.item_id, v.model, v.answer, v.hinted FROM votes v JOIN items i ON i.id = v.item_id WHERE i.task = 'gpqa' AND i.status IN ('accepted', 'dropped') ORDER BY v.id"),
  ]);
  return json({ now: Date.now(), items: items.results, votes: votes.results });
}

// ---------- planted-errors audit ----------

// GPQA stops at 50 decided questions: untouched ones are parked (ones already voted on finish),
// and the planted-errors problems open up. Nodes started with "gpqa" are also served "audit" (see job).
async function gpqaSwitch(env) {
  const r = await env.DB.prepare("SELECT COUNT(*) AS n FROM items WHERE task = 'gpqa' AND status IN ('accepted', 'dropped')").first();
  if (Number(r?.n || 0) < 50) return;
  await env.DB.batch([
    env.DB.prepare("UPDATE items SET status = 'parked' WHERE task = 'gpqa' AND status = 'pending' AND id NOT IN (SELECT item_id FROM votes)"),
    env.DB.prepare("UPDATE items SET status = 'pending' WHERE task = 'audit' AND status = 'parked'"),
  ]);
}

// Scores the network as a label checker: a label is flagged when the agreed answer differs from it;
// items with no agreement are "sent to review" (not kept, so a bad label can't slip into the data either).
// The live batch on /audit: planted-error problems still open, or decided in the last 6 hours, with their votes so far.
async function auditLive(env) {
  const since = Date.now() - 6 * 3600_000;
  const { results } = await env.DB.prepare(
    `SELECT i.id, i.question, i.gold, i.meta->>'label' AS label, (i.meta->>'planted')::bool AS planted, i.status, i.final_answer,
            (SELECT json_agg(v.answer ORDER BY v.id) FROM votes v WHERE v.item_id = i.id) AS answers,
            (SELECT MAX(v.created_at) FROM votes v WHERE v.item_id = i.id) AS last_vote
     FROM items i WHERE i.task = 'audit' AND (i.status IN ('pending', 'hinting')
           OR (i.status IN ('accepted', 'dropped') AND (SELECT MAX(v.created_at) FROM votes v WHERE v.item_id = i.id) > ?))
     ORDER BY i.id`
  ).bind(since).all();
  return json({ now: Date.now(), items: results.map((r) => ({ ...r, answers: typeof r.answers === "string" ? JSON.parse(r.answers) : r.answers || [] })) });
}

const auditCache = {};
async function auditBoard(env, big) {
  const hit = auditCache[big || ""]; if (hit && Date.now() - hit.at < 15_000) return json(hit.body);
  // Both planted-errors runs (50, then 300) count together; only decided items are shown, so the totals grow live.
  // ?run=1000 shows the second run alone, ?run=50 the first.
  const run = (big === "1000" ? "id >= 700001" : big === "50" ? "id < 700001" : "id > 0") + " AND status IN ('accepted', 'dropped')";
  const { results } = await env.DB.prepare(
    `SELECT (meta->>'planted')::bool AS planted,
            CASE WHEN status = 'accepted' AND final_answer <> meta->>'label' THEN 'flagged'
                 WHEN status = 'accepted' THEN 'confirmed'
                 WHEN status = 'dropped' THEN 'review'
                 ELSE 'open' END AS outcome,
            SUM(CASE WHEN status = 'accepted' AND final_answer = gold THEN 1 ELSE 0 END) AS fixed_right,
            COUNT(*) AS n
     FROM items WHERE task = 'audit' AND status <> 'parked' AND ${run} GROUP BY 1, 2`
  ).all();
  // Per-item detail for the walkthrough (GSM8K test problems are public, so the text can be shown).
  const [items, votes] = await env.DB.batch([
    env.DB.prepare(`SELECT id, question, gold, meta->>'label' AS label, (meta->>'planted')::bool AS planted, status, final_answer
                    FROM items WHERE task = 'audit' AND status <> 'parked' AND ${run} ORDER BY id`),
    env.DB.prepare(`SELECT v.item_id, v.answer, v.hinted FROM votes v JOIN items i ON i.id = v.item_id
                    WHERE i.task = 'audit' AND i.status <> 'parked' AND i.${run.replace("status", "i.status")} ORDER BY v.id`),
  ]);
  const body = { now: Date.now(), rows: results, items: items.results, votes: votes.results };
  auditCache[big || ""] = { at: Date.now(), body };
  return json(body);
}

// ---------- hard 500 run ----------

// The /hard page: the 100 hardest GSM8K train problems (5+ steps), refined by the 10 model families.
async function hardBoard(env) {
  const run = "i.id >= 600001 AND i.task = 'gsm8k_mixed' AND i.status <> 'parked'";
  const [sum, pairs, recent, heat] = await env.DB.batch([
    env.DB.prepare(`SELECT i.status, COUNT(*) AS n, SUM(CASE WHEN i.final_answer = i.gold THEN 1 ELSE 0 END) AS right_n
                    FROM items i WHERE ${run} GROUP BY 1`),
    env.DB.prepare(`SELECT COUNT(DISTINCT i.id) AS items, COUNT(*) AS wrong_votes FROM items i JOIN votes v ON v.item_id = i.id
                    WHERE ${run} AND i.status = 'accepted' AND v.answer IS DISTINCT FROM i.final_answer`),
    env.DB.prepare(`SELECT i.id, i.question, i.gold, i.final_answer, i.status, (i.meta->>'steps')::int AS steps,
                           (SELECT COUNT(*) FROM votes v WHERE v.item_id = i.id) AS votes,
                           (SELECT COUNT(*) FROM votes v WHERE v.item_id = i.id AND v.answer = i.final_answer) AS agree
                    FROM items i WHERE ${run} AND i.status IN ('accepted', 'dropped') ORDER BY (SELECT MAX(v.created_at) FROM votes v WHERE v.item_id = i.id) DESC NULLS LAST LIMIT 25`),
    env.DB.prepare(`SELECT v.model, v.harness, COUNT(*) AS votes, SUM(CASE WHEN v.answer = i.gold THEN 1 ELSE 0 END) AS correct
                    FROM votes v JOIN items i ON i.id = v.item_id WHERE ${run} GROUP BY 1, 2`),
  ]);
  return json({ now: Date.now(), status: sum.results, pairs: pairs.results[0], recent: recent.results, heat: heat.results });
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
