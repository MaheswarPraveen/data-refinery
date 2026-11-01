// Data search agent: a customer describes the data they need; an LLM agent decides which tools to call
// (our refined data, open datasets, the web, a scraper), collects a sample, sizes the job and quotes it.
// The browser drives the loop one step at a time (POST /api/agent/step), so no request runs long.

// The agent's brain is Meta Llama on Cloudflare Workers AI (free daily allowance, no keys): 70B first,
// then Llama 4 Scout and Llama 3.1 8B if the allowance runs short.
const MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
const FALLBACKS = ["@cf/meta/llama-4-scout-17b-16e-instruct", "@cf/meta/llama-3.1-8b-instruct-fast"];
const MAX_MESSAGES = 40;
const MAX_CHARS = 90_000;
const UA = { "user-agent": "DataRefineryAgent/1.0 (hackathon demo)" };

// Prices per accepted item (INR) and network figures measured in our runs.
const TIERS = { standard: 2, reasoning: 6, expert: 30 };
const VOTES_PER_ITEM = 5.6, ACCEPT_RATE = 0.84, KEPT_ACCURACY = 0.986, SEC_PER_VOTE = { standard: 14, reasoning: 28, expert: 70 };

const SYSTEM = `You are the Data Refinery's data search agent. A customer describes the dataset they need.
Your job: find where that data can come from, collect a small real sample, decide how much data they need, and quote it.

Work in steps and choose tools yourself:
- search_refined: our already-refined data (always check first).
- search_datasets: open datasets on Hugging Face; then dataset_info for size, licence and sample rows of the best ones.
- search_web: Wikipedia search for sources and pages.
- read_page: read one web page as clean text (fast).
- crawl: scrape several pages at once with a real browser (for bigger or JavaScript-heavy sites).
- estimate: price, time and payouts for refining N items. Call it before you finish.

Rules: prefer open licences; never invent sources, numbers or sample items (only use what tools returned);
keep each tool query short; use at most 8 tool calls. Recommended sizes: evaluation set 300-1,000 items;
fine-tuning 1,000-20,000; pre-training 1,000,000+. Pick a tier: standard (simple labels), reasoning (multi-step
maths, code, logic), expert (science, medicine, law).

How to act: every reply is ONLY one JSON object, no prose, no code fence.
To use a tool:  {"tool": "<tool name>", "args": {...}}   (one tool per reply; you will get its result back)
Tools and their args: search_refined {"query"}, search_datasets {"query"}, dataset_info {"id"}, search_web {"query"},
read_page {"url"}, crawl {"urls": "comma-separated"}, estimate {"items", "tier", "nodes"}.

When done, reply with {"final": REPORT} where REPORT is:
{"summary": "...", "schema": ["field", ...], "sources": [{"name": "...", "url": "...", "items": number or null, "licence": "..."}],
 "sample": [{...up to 5 items in the schema...}], "recommended_size": number, "size_reason": "...", "tier": "standard|reasoning|expert",
 "estimate": {copy of the estimate tool result}}`;

const clip = (s, n = 4000) => (s.length > n ? s.slice(0, n) + "…" : s);
const getJSON = async (url, headers = {}) => { const r = await fetch(url, { headers: { ...UA, ...headers } }); if (!r.ok) throw new Error(`${r.status} from ${new URL(url).host}`); return r.json(); };

async function runTool(env, name, a) {
  switch (name) {
    case "search_refined": {
      const q = String(a.query || "").slice(0, 100);
      // any of the words, so a long query still finds partial matches; task names count as words too
      const words = q.toLowerCase().match(/[a-z0-9]+/g) || [];
      const tsq = words.join(" | ") || "none";
      const taskHint = words.some((w) => /news|headline|sentiment|financ|stock/.test(w)) ? "news" : words.some((w) => /math|word|arithmetic|gsm/.test(w)) ? "gsm8k" : "";
      const [counts, rows] = await env.DB.batch([
        env.DB.prepare("SELECT task, COUNT(*) AS n FROM items WHERE status = 'accepted' AND is_honeypot = 0 AND (to_tsvector('english', question) @@ to_tsquery('english', ?) OR task LIKE ?) GROUP BY task").bind(tsq, taskHint ? taskHint + "%" : "-"),
        env.DB.prepare("SELECT task, question, final_answer FROM items WHERE status = 'accepted' AND is_honeypot = 0 AND (to_tsvector('english', question) @@ to_tsquery('english', ?) OR task LIKE ?) LIMIT 3").bind(tsq, taskHint ? taskHint + "%" : "-"),
      ]);
      return { matches_by_task: counts.results, examples: rows.results.map((r) => ({ ...r, question: clip(String(r.question), 200) })) };
    }
    case "search_datasets": {
      // Hugging Face matches names, so long phrases often find nothing: fall back to shorter keywords
      const words = String(a.query || "").toLowerCase().match(/[a-z0-9]+/g) || [];
      let d = [];
      for (const q of [words.join(" "), words.slice(0, 2).join(" "), ...words.filter((w) => w.length > 3).sort((x, y) => y.length - x.length).slice(0, 2)]) {
        if (!q) continue;
        d = await getJSON(`https://huggingface.co/api/datasets?search=${encodeURIComponent(q.slice(0, 80))}&sort=downloads&limit=8`);
        if (d.length) break;
      }
      return d.map((x) => ({ id: x.id, downloads: x.downloads, likes: x.likes, tags: (x.tags || []).filter((t) => /^(license|size_categories|task_categories|language):/.test(t)).slice(0, 8) }));
    }
    case "dataset_info": {
      const id = String(a.id || "").trim();
      const [meta, size, rows] = await Promise.allSettled([
        getJSON(`https://huggingface.co/api/datasets/${id}`),
        getJSON(`https://datasets-server.huggingface.co/size?dataset=${encodeURIComponent(id)}`),
        getJSON(`https://datasets-server.huggingface.co/first-rows?dataset=${encodeURIComponent(id)}&config=default&split=train`),
      ]);
      const tags = meta.value?.tags || [];
      return {
        id, licence: (tags.find((t) => t.startsWith("license:")) || "license:unknown").slice(8), gated: meta.value?.gated || false,
        rows: size.value?.size?.dataset?.num_rows ?? null,
        sample: (rows.value?.rows || []).slice(0, 3).map((r) => Object.fromEntries(Object.entries(r.row).map(([k, v]) => [k, clip(typeof v === "string" ? v : JSON.stringify(v), 300)]))),
      };
    }
    case "search_web": {
      const d = await getJSON(`https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=6&srsearch=${encodeURIComponent(String(a.query || "").slice(0, 100))}`);
      return (d.query?.search || []).map((s) => ({ title: s.title, url: `https://en.wikipedia.org/wiki/${encodeURIComponent(s.title.replace(/ /g, "_"))}`, snippet: s.snippet.replace(/<[^>]+>/g, "") }));
    }
    case "read_page": {
      const url = String(a.url || "");
      if (!/^https:\/\//.test(url)) throw new Error("https URL required");
      const r = await fetch(`https://r.jina.ai/${url}`, { headers: { ...UA, accept: "text/plain" } });
      if (!r.ok) throw new Error(`reader ${r.status}`);
      return { url, text: clip(await r.text(), 6000) };
    }
    case "crawl": {
      const urls = String(a.urls || "").split(",").map((u) => u.trim()).filter((u) => /^https:\/\//.test(u)).slice(0, 5);
      if (!env.CRAWL_URL || !env.CRAWL_TOKEN) return { note: "Crawler node not connected yet; use read_page instead." };
      const r = await fetch(`${env.CRAWL_URL}/crawl`, { method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${env.CRAWL_TOKEN}` }, body: JSON.stringify({ urls, max_chars: 3000 }) });
      if (!r.ok) throw new Error(`crawler ${r.status} (it may be waking up; try again)`);
      return (await r.json()).pages;
    }
    case "estimate": {
      const items = Math.max(1, Math.round(Number(String(a.items).replace(/[^\d.]/g, "")) || 0));
      const tier = TIERS[a.tier] ? a.tier : "standard", nodes = Number(a.nodes) >= 10 ? Number(a.nodes) : 100;   // a network-sized quote
      const toRefine = Math.ceil(items / ACCEPT_RATE);                 // some candidates are rejected, so collect extra
      const votes = toRefine * VOTES_PER_ITEM;
      const hours = (votes * SEC_PER_VOTE[tier]) / 3600 / nodes;       // one vote at a time per node
      const price = items * TIERS[tier];
      return { items_delivered: items, candidates_to_collect: toRefine, votes, tier, price_inr: price, price_per_item_inr: TIERS[tier],
               node_payouts_inr: Math.round(price * 0.7), time_hours: Math.round(hours * 10) / 10, nodes,
               expected_label_accuracy: `${(KEPT_ACCURACY * 100).toFixed(1)}% (measured on our maths runs)` };
    }
  }
  throw new Error(`unknown tool ${name}`);
}

// One reply from Meta Llama. The transcript is plain chat: the agent's JSON actions as assistant turns, tool
// results as user turns, so it works the same on every Workers AI model.
async function llama(env, messages, model) {
  const out = await env.AI.run(model, { messages, temperature: 0.2, max_tokens: 1800 });
  const text = typeof out?.response === "string" ? out.response : JSON.stringify(out?.response ?? "");
  if (!text.trim()) throw new Error(`${model}: empty reply`);
  return text;
}
const parseJSON = (s) => { s = String(s || "").replace(/```(json)?/g, "").trim(); try { return JSON.parse(s.slice(s.indexOf("{"), s.lastIndexOf("}") + 1)); } catch { return null; } };

export async function agentStep(req, env) {
  const b = await req.json().catch(() => ({}));
  let messages = Array.isArray(b.messages) ? b.messages : [];
  if (!messages.length) {
    const ask = String(b.request || "").trim().slice(0, 600);
    if (ask.length < 8) return Response.json({ error: "Describe the data you need." }, { status: 400 });
    messages = [{ role: "system", content: SYSTEM }, { role: "user", content: ask }];
  }
  messages[0] = { role: "system", content: SYSTEM };                    // the client can't rewrite the rules
  if (messages.length > MAX_MESSAGES || JSON.stringify(messages).length > MAX_CHARS)
    messages.push({ role: "user", content: "Tool budget used up. Finish now with the JSON report." });

  const ask = async (msgs) => {
    const errs = [];
    for (const m of [MODEL, ...FALLBACKS]) {
      try { return await llama(env, msgs, m); } catch (e) { errs.push(String(e.message || e).slice(0, 160)); }
    }
    throw new Error(errs.join(" | "));
  };
  let text;
  try { text = await ask(messages); } catch (e) { return Response.json({ error: "The AI model is busy. Try again in a minute.", detail: String(e.message) }, { status: 503 }); }
  let act = parseJSON(text);
  if (!act || (!act.tool && !act.final)) {
    // one repair round: the model sometimes adds prose or cuts the JSON short
    try { act = parseJSON(await ask([...messages, { role: "assistant", content: text }, { role: "user", content: 'Reply with ONLY one JSON object: {"tool": ..., "args": {...}} or {"final": {...}}. Keep samples short.' }])); } catch {}
  }
  if (!act || (!act.tool && !act.final))
    return Response.json({ messages, done: true, report: null, text: "The agent could not finish the report. Try again." });
  messages.push({ role: "assistant", content: JSON.stringify(act) });

  if (act.tool) {
    const name = String(act.tool), args = act.args && typeof act.args === "object" ? act.args : {};
    let result, ok = true;
    try { result = await runTool(env, name, args); } catch (e) { result = { error: String(e.message || e) }; ok = false; }
    messages.push({ role: "user", content: `Tool result for ${name}: ${clip(JSON.stringify(result), 5000)}` });
    return Response.json({ messages, log: [{ tool: name, args, ok, preview: clip(JSON.stringify(result), 400) }], done: false });
  }
  const report = act.final;
  // the quote always comes from the estimate tool itself, never from the model's retelling of it
  const est = [...messages].reverse().find((m) => m.role === "user" && String(m.content).startsWith("Tool result for estimate: "));
  try { if (est) report.estimate = JSON.parse(String(est.content).slice("Tool result for estimate: ".length)); } catch {}
  if (!report.estimate?.price_inr) report.estimate = await runTool(env, "estimate", { items: report.recommended_size || 1000, tier: report.tier || "standard" });
  // if the report left the sample out, show real rows from the last dataset the agent inspected (never invented)
  if (!Array.isArray(report.sample) || !report.sample.length) {
    for (const m of [...messages].reverse()) {
      if (m.role !== "user" || !String(m.content).startsWith("Tool result for dataset_info: ")) continue;
      try { const info = JSON.parse(String(m.content).slice("Tool result for dataset_info: ".length)); if (info.sample?.length) { report.sample = info.sample.slice(0, 3); break; } } catch {}
    }
  }
  return Response.json({ messages, done: true, report, text: null });
}
