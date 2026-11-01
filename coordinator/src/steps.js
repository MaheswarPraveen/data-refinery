// Step check for refined maths working: the vote decides the answer, this checks the working that goes into the
// dataset. Every "expression = number" written in a trace is recomputed; a trace with a wrong step, or one that
// loops on repeated lines, is not used. (Workers can't eval, so expressions go through a tiny arithmetic parser.)

function parse(src) {
  let i = 0;
  const peek = () => src[i], eat = (c) => (src[i] === c ? (i++, true) : false);
  const ws = () => { while (src[i] === " ") i++; };
  function num() {
    ws(); const m = /^\d+(\.\d+)?/.exec(src.slice(i));
    if (m) { i += m[0].length; return parseFloat(m[0]); }
    if (eat("(")) { const v = expr(); ws(); if (!eat(")")) throw 0; return v; }
    if (eat("-")) return -num();
    throw 0;
  }
  function term() {
    let v = num();
    for (;;) { ws(); if (eat("*")) v *= num(); else if (eat("/")) { const d = num(); if (!d) throw 0; v /= d; } else return v; }
  }
  function expr() {
    let v = term();
    for (;;) { ws(); if (eat("+")) v += term(); else if (eat("-")) v -= term(); else return v; }
  }
  const v = expr(); ws();
  if (i !== src.length) throw 0;
  return v;
}

const norm = (s) => s.replace(/[$₹€£,]/g, "").replace(/[×x✕]/g, "*").replace(/÷/g, "/").replace(/[−–]/g, "-")
  .replace(/(\d)\s*%/g, "$1");

export function checkSteps(text) {
  const t = String(text || "");
  const lines = t.split("\n").map((l) => l.trim()).filter((l) => l.length > 3);
  // loops: the same line three times, or twice in a row
  const count = {};
  for (let k = 0; k < lines.length; k++) {
    count[lines[k]] = (count[lines[k]] || 0) + 1;
    if (count[lines[k]] >= 3 || (k && lines[k] === lines[k - 1])) return { ok: false, checked: 0, reason: "repeats" };
  }
  let checked = 0;
  // "Total = 324 + 374 + 128" then "= 826" on the next line is one step: join continuation lines first
  for (const raw of t.replace(/\n\s*=/g, " =").split("\n")) {
    const parts = norm(raw).split("=");
    for (let k = 0; k + 1 < parts.length; k++) {
      const lhs = (parts[k].match(/[\d.()+\-*/ ]+$/) || [""])[0].trim();      // the arithmetic just before "="
      const rhs = (parts[k + 1].match(/^\s*(-?\d+(\.\d+)?)/) || [])[1];      // the number just after it
      if (!lhs || rhs == null || !/\d\s*[+\-*/]\s*[\d(]/.test(lhs)) continue;
      let v; try { v = parse(lhs); } catch { continue; }
      const r = parseFloat(rhs);
      if (Math.abs(v - r) > Math.max(0.011, 0.002 * Math.abs(r))) return { ok: false, checked, reason: `${lhs} = ${rhs} (is ${+v.toFixed(4)})` };
      checked++;
    }
  }
  return { ok: true, checked };
}

// From the votes that agreed with the consensus, the working to keep: every step checks out, at least one step
// could be checked, and it's the shortest such trace. null when no agreeing vote has clean working.
export function pickWorking(traces) {
  const good = (traces || []).filter((t) => t && t.trim()).map((t) => ({ t, c: checkSteps(t) })).filter((x) => x.c.ok && x.c.checked > 0);
  good.sort((a, b) => a.t.length - b.t.length);
  return good.length ? good[0].t : null;
}
