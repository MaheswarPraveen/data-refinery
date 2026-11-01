"""Animated, theme-aware SVGs for the README (GitHub plays SMIL animation in <img>, but runs no JavaScript).

Writes assets/readme/{hero,result,architecture,agreement,compare}.svg. Numbers are the measured results.
Usage:  python scripts/make_readme_svgs.py
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "readme"
OUT.mkdir(parents=True, exist_ok=True)

STYLE = """<style>
  svg { --bg:#fbfaf7; --ink:#141413; --ink2:#4d4c48; --mut:#8a8880; --rule:#e2e0d8; --track:#ebe9e2;
        --acc:#6d4fd8; --acc2:#b3a2f0; --good:#1d7a3e; --bad:#c0392b; --warn:#d97b1a; }
  @media (prefers-color-scheme: dark) {
    svg { --bg:#161b22; --ink:#ecebe6; --ink2:#b8b6ae; --mut:#8b949e; --rule:#30363d; --track:#21262d;
          --acc:#9a82f2; --acc2:#5b43bf; --good:#5cbf7c; --bad:#e57368; --warn:#e8973f; } }
  .card { fill: var(--bg); stroke: var(--rule); }
  .sans { font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif; }
  .mono { font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace; }
  .ink { fill: var(--ink); } .ink2 { fill: var(--ink2); } .mut { fill: var(--mut); } .acc { fill: var(--acc); }
  .good { fill: var(--good); } .bad { fill: var(--bad); } .warn { fill: var(--warn); }
  .cap { font-size: 11px; letter-spacing: .12em; fill: var(--mut); }
</style>"""


def svg(w, h, body, label):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{label}">\n'
            f'{STYLE}\n<rect class="card" x="1" y="1" width="{w-2}" height="{h-2}" rx="14"/>\n{body}\n</svg>\n')


# ---------- hero: noisy items flow through five voters; clean ones stack up, bad ones drop out ----------
def hero():
    W, H = 960, 300
    b = []
    b.append('<text x="44" y="70" class="sans cap">DATA REFINERY · META GLOBAL AI HACKATHON</text>')
    b.append('<text x="44" y="128" class="sans ink" font-size="46" font-weight="700" letter-spacing="-1.2">Raw data in.</text>')
    b.append('<text x="44" y="182" class="sans acc" font-size="46" font-weight="700" letter-spacing="-1.2">Refined data out.</text>')
    b.append('<text x="44" y="222" class="sans ink2" font-size="16">Five small open models check every item, blind.</text>')
    b.append('<text x="44" y="246" class="sans ink2" font-size="16">Only what they agree on survives.</text>')
    # pipeline on the right
    x0, cx, x1 = 520, 700, 880
    ys = [70, 110, 150, 190, 230]
    for y in ys:
        b.append(f'<path d="M{x0},150 C{x0+70},150 {cx-80},{y} {cx-14},{y}" fill="none" stroke="var(--rule)" stroke-width="1.5"/>')
        b.append(f'<path d="M{cx+14},{y} C{cx+80},{y} {x1-70},150 {x1-30},150" fill="none" stroke="var(--rule)" stroke-width="1.5"/>')
        b.append(f'<circle cx="{cx}" cy="{y}" r="13" fill="var(--bg)" stroke="var(--ink2)" stroke-width="1.5"/>')
        b.append(f'<circle cx="{cx}" cy="{y}" r="4" class="acc"><animate attributeName="opacity" values=".25;1;.25" dur="2.4s" begin="{ys.index(y)*0.3}s" repeatCount="indefinite"/></circle>')
    b.append(f'<text x="{cx}" y="268" text-anchor="middle" class="sans cap">5 VOTERS</text>')
    b.append(f'<text x="{x0-6}" y="268" text-anchor="middle" class="sans cap">RAW</text>')
    b.append(f'<text x="{x1-10}" y="268" text-anchor="middle" class="sans cap">CLEAN</text>')
    # clean stack
    for i in range(6):
        b.append(f'<rect x="{x1-34}" y="{170 - i*8}" width="44" height="6" rx="2" fill="var(--acc)" opacity="0">'
                 f'<animate attributeName="opacity" values="0;0;1;1;0" keyTimes="0;{0.12+i*0.13:.2f};{0.16+i*0.13:.2f};.96;1" dur="8s" repeatCount="indefinite"/></rect>')
    # raw items flowing in; every third one is red and drops out after the voters
    for i in range(9):
        y = ys[i % 5]
        bad = i % 3 == 2
        col = "var(--bad)" if bad else "var(--acc2)"
        path_in = f"M{x0},150 C{x0+70},150 {cx-80},{y} {cx-14},{y}"
        b.append(f'<rect width="10" height="10" rx="2" x="-5" y="-5" fill="{col}"><animateMotion dur="1.6s" begin="{i*0.9:.1f}s;{i*0.9:.1f}s+8.1s" path="{path_in}" fill="freeze"/>'
                 f'<animate attributeName="opacity" values="1;1;0" keyTimes="0;.9;1" dur="1.7s" begin="{i*0.9:.1f}s;{i*0.9:.1f}s+8.1s" fill="freeze"/></rect>')
        if bad:
            b.append(f'<rect width="10" height="10" rx="2" x="-5" y="-5" fill="var(--bad)" opacity="0"><animateMotion dur="1.2s" begin="{i*0.9+1.6:.1f}s;{i*0.9+1.6:.1f}s+8.1s" path="M{cx+14},{y} Q{cx+50},{y-30} {cx+40},300" fill="freeze"/>'
                     f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.05;.8;1" dur="1.2s" begin="{i*0.9+1.6:.1f}s;{i*0.9+1.6:.1f}s+8.1s" fill="freeze"/></rect>')
        else:
            path_out = f"M{cx+14},{y} C{cx+80},{y} {x1-70},150 {x1-30},150"
            b.append(f'<rect width="10" height="10" rx="2" x="-5" y="-5" fill="var(--acc)" opacity="0"><animateMotion dur="1.4s" begin="{i*0.9+1.6:.1f}s;{i*0.9+1.6:.1f}s+8.1s" path="{path_out}" fill="freeze"/>'
                     f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.05;.9;1" dur="1.4s" begin="{i*0.9+1.6:.1f}s;{i*0.9+1.6:.1f}s+8.1s" fill="freeze"/></rect>')
    return svg(W, H, "\n".join(b), "Raw data in, refined data out: five small open models check every item and only agreed answers survive.")


# ---------- main result: 20% -> 2.4%, waffle of all 50 labels, before/after bars ----------
def result():
    # final numbers of the Error Detection Test (both planted-error runs, decided items only)
    N, PLANTED, FIXED, OK, REVIEW, CHANGED, KEPT, WRONG_AFTER = 262, 93, 78, 142, 39, 3, 223, 3
    before, after = round(100 * PLANTED / N, 1), round(100 * WRONG_AFTER / KEPT, 1)
    W, H = 960, 330
    b = ['<text x="44" y="56" class="sans cap">MAIN RESULT · ERROR DETECTION ON A LABELLED DATASET</text>']
    b.append(f'<text x="44" y="138" class="mono bad" font-size="64" font-weight="600" letter-spacing="-3">{before:g}%</text>')
    b.append('<text x="232" y="130" class="sans mut" font-size="40">→</text>')
    b.append(f'<text x="282" y="138" class="mono good" font-size="64" font-weight="600" letter-spacing="-3">{after:g}%</text>')
    b.append('<text x="44" y="172" class="sans ink2" font-size="16">wrong labels in the dataset, before and after</text>')
    b.append(f'<text x="44" y="214" class="sans ink" font-size="15">{PLANTED} wrong answers hidden among {N} problems.</text>')
    b.append(f'<text x="44" y="238" class="sans ink" font-size="15">The models never saw them, and fixed <tspan font-weight="700" class="acc">{FIXED} of {PLANTED}</tspan>.</text>')
    b.append(f'<text x="44" y="278" class="sans mut" font-size="13">A {round(before / after)}× cleaner dataset, with no human labelling.</text>')
    # outcome bar: every label, split by what happened to it
    x0, y0, w = 470, 96, 300
    b.append(f'<text x="{x0}" y="78" class="sans cap">WHAT HAPPENED TO EACH LABEL</text>')
    parts = [(FIXED, "var(--acc)", f"corrected {FIXED}"), (OK, "var(--acc2)", f"confirmed {OK}"), (REVIEW, "var(--track)", f"sent to review {REVIEW}"), (CHANGED, "var(--bad)", f"changed in error {CHANGED}")]
    x = x0
    for k, (n, col, lab) in enumerate(parts):
        ww = w * n / N
        b.append(f'<rect x="{x:.1f}" y="{y0}" width="0" height="26" fill="{col}"><animate attributeName="width" from="0" to="{max(ww - 2, 2):.1f}" dur=".8s" begin="{0.2 + k * 0.2:.1f}s" fill="freeze"/></rect>')
        x += ww
        b.append(f'<rect x="{x0}" y="{y0 + 48 + k * 22}" width="10" height="10" rx="2" fill="{col}" stroke="var(--mut)" stroke-width="{1 if k == 2 else 0}"/>'
                 f'<text x="{x0 + 16}" y="{y0 + 57 + k * 22}" class="sans ink2" font-size="12.5">{lab}</text>')
    # before / after bars
    bx, base, hmax = 840, 260, 170
    b.append(f'<text x="{bx+20}" y="70" class="sans cap" text-anchor="middle">ERROR RATE</text>')
    for j, (v, col, lab) in enumerate([(before, "var(--bad)", "before"), (after, "var(--good)", "after")]):
        x = bx - 28 + j * 70
        h = max(4, hmax * v / before)
        b.append(f'<rect x="{x}" y="{base-hmax}" width="44" height="{hmax}" rx="8" fill="var(--track)"/>')
        b.append(f'<rect x="{x}" y="{base}" width="44" height="0" rx="8" fill="{col}"><animate attributeName="height" from="0" to="{h}" dur="1s" begin=".4s" fill="freeze" calcMode="spline" keySplines=".2 .7 .2 1"/>'
                 f'<animate attributeName="y" from="{base}" to="{base-h}" dur="1s" begin=".4s" fill="freeze" calcMode="spline" keySplines=".2 .7 .2 1"/></rect>')
        b.append(f'<text x="{x+22}" y="{base+24}" text-anchor="middle" class="mono ink" font-size="16" font-weight="600">{v:g}%</text>')
        b.append(f'<text x="{x+22}" y="{base+42}" text-anchor="middle" class="sans mut" font-size="11">{lab}</text>')
    return svg(W, H, "\n".join(b), f"Main result: wrong labels fell from {before:g}% to {after:g}%; {FIXED} of {PLANTED} hidden errors were fixed.")


# ---------- architecture: one item cycles assign -> vote -> count -> hint -> accept ----------
def architecture():
    W, H, T = 960, 430, 8  # T = loop length in seconds
    b = ['<text x="34" y="40" class="sans cap">ARCHITECTURE · ONE ITEM, START TO FINISH</text>']
    pairs = [("Llama 3.2 3B", "Phi-4 mini"), ("Gemma 4 E4B", "Granite 3.3 2B"), ("Ministral 3 3B", "Nemotron 3 Nano 4B"),
             ("SmolLM3 3B", "Apriel 5B"), ("GLM-Edge 4B", "Qwen 3.5 4B")]
    core = (480, 215)
    wires = []
    for i, (a, c) in enumerate(pairs):
        y = 80 + i * 66
        wires.append(f"M{core[0]-100},{core[1]-10+i*5} C{core[0]-170},{core[1]-10+i*5} 330,{y+24} 270,{y+24}")
        b.append(f'<path d="{wires[-1]}" fill="none" stroke="var(--rule)" stroke-width="1.6"/>')
        b.append(f'<rect x="34" y="{y}" width="236" height="48" rx="9" fill="var(--bg)" stroke="var(--rule)" stroke-width="1.4"/>')
        b.append(f'<circle cx="58" cy="{y+24}" r="6" class="acc"/>')
        b.append(f'<text x="74" y="{y+20}" class="sans ink" font-size="14" font-weight="600">Node {i+1}</text>')
        # the node's two models take turns, one per item
        for k, m in enumerate((a, c)):
            vals = "1;1;0;0;1" if k == 0 else "0;0;1;1;0"
            b.append(f'<text x="74" y="{y+37}" class="sans mut" font-size="11.5">{m}<animate attributeName="opacity" values="{vals}" keyTimes="0;.48;.5;.98;1" dur="{2*T}s" repeatCount="indefinite"/></text>')
        ans = "40" if i == 4 else "42"
        b.append(f'<text x="256" y="{y+29}" text-anchor="end" class="mono {"mut" if i == 4 else "acc"}" font-size="15" font-weight="600" opacity="0">{ans}'
                 f'<animate attributeName="opacity" values="0;0;1;1;0" keyTimes="0;.18;.2;.9;1" dur="{T}s" repeatCount="indefinite"/></text>')
    b.append('<text x="34" y="66" class="sans cap">VOLUNTEER NODES</text>')
    # coordinator
    b.append(f'<rect x="{core[0]-100}" y="{core[1]-62}" width="200" height="124" rx="12" fill="var(--bg)" stroke="var(--ink)" stroke-width="1.8"/>')
    b.append(f'<text x="{core[0]}" y="{core[1]-34}" text-anchor="middle" class="sans ink" font-size="15" font-weight="600">Coordinator</text>')
    b.append(f'<text x="{core[0]}" y="{core[1]-16}" text-anchor="middle" class="sans mut" font-size="11.5">Cloudflare Worker</text>')
    for j in range(5):
        col = "var(--mut)" if j == 4 else "var(--acc)"
        b.append(f'<rect x="{core[0]-72+j*29}" y="{core[1]}" width="24" height="24" rx="5" fill="{col}" opacity="0">'
                 f'<animate attributeName="opacity" values="0;0;1;1;0" keyTimes="0;{0.3+j*0.02:.2f};{0.32+j*0.02:.2f};.92;1" dur="{T}s" repeatCount="indefinite"/></rect>')
    b.append(f'<text x="{core[0]}" y="{core[1]+46}" text-anchor="middle" class="sans acc" font-size="12" font-weight="600" opacity="0">4 of 5 agree<animate attributeName="opacity" values="0;0;1;1;0" keyTimes="0;.42;.44;.92;1" dur="{T}s" repeatCount="indefinite"/></text>')
    # ledger
    b.append(f'<path id="aw-l" d="M{core[0]},{core[1]+62} L{core[0]},{core[1]+108}" stroke="var(--rule)" stroke-width="1.6"/>')
    b.append(f'<rect x="{core[0]-75}" y="{core[1]+110}" width="150" height="54" rx="9" fill="var(--bg)" stroke="var(--rule)" stroke-width="1.4"/>')
    b.append(f'<text x="{core[0]}" y="{core[1]+133}" text-anchor="middle" class="sans ink" font-size="13.5" font-weight="600">Vote ledger</text>')
    b.append(f'<text x="{core[0]}" y="{core[1]+151}" text-anchor="middle" class="sans mut" font-size="11">every vote, right or wrong</text>')
    # judge + dataset
    jw = f"M{core[0]+100},{core[1]-30} C{core[0]+170},{core[1]-30} 650,110 720,110"
    dw = f"M{core[0]+100},{core[1]+30} C{core[0]+170},{core[1]+30} 650,320 720,320"
    b.append(f'<path d="{jw}" fill="none" stroke="var(--rule)" stroke-width="1.6" stroke-dasharray="5 5"/>')
    b.append(f'<path d="{dw}" fill="none" stroke="var(--rule)" stroke-width="1.6"/>')
    b.append('<text x="720" y="66" class="sans cap">JUDGE</text>')
    b.append('<rect x="720" y="76" width="210" height="70" rx="11" fill="var(--bg)" stroke="var(--warn)" stroke-width="1.5"/>')
    b.append('<text x="738" y="104" class="sans ink" font-size="14" font-weight="600">Meta Muse Glimmer 30B</text>')
    b.append('<text x="738" y="124" class="sans mut" font-size="11.5">hints on 3-of-5 splits, never answers</text>')
    b.append('<text x="720" y="276" class="sans cap">OUTPUT</text>')
    b.append('<rect x="720" y="286" width="210" height="70" rx="11" fill="var(--bg)" stroke="var(--good)" stroke-width="1.5"/>')
    b.append('<text x="738" y="314" class="sans ink" font-size="14" font-weight="600">Refined dataset</text>')
    b.append('<text x="738" y="334" class="sans mut" font-size="11.5">answer + full vote record</text>')
    # packets: questions out, answers back, bad vote to ledger, hint loop, accepted item
    for i, w in enumerate(wires):
        b.append(f'<circle r="5.5" fill="var(--acc)" opacity="0"><animateMotion dur="{T}s" repeatCount="indefinite" keyPoints="0;0;1;1" keyTimes="0;.02;.14;1" calcMode="linear" path="{w}"/>'
                 f'<animate attributeName="opacity" values="0;1;1;0;0" keyTimes="0;.02;.13;.14;1" dur="{T}s" repeatCount="indefinite"/></circle>')
        col = "var(--mut)" if i == 4 else "var(--acc)"
        b.append(f'<circle r="5.5" fill="{col}" opacity="0"><animateMotion dur="{T}s" repeatCount="indefinite" keyPoints="1;1;0;0" keyTimes="0;.2;.3;1" calcMode="linear" path="{w}"/>'
                 f'<animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="0;.19;.2;.29;.3;1" dur="{T}s" repeatCount="indefinite"/></circle>')
    lp = f"M{core[0]},{core[1]+62} L{core[0]},{core[1]+108}"
    b.append(f'<circle r="5" fill="var(--bad)" opacity="0"><animateMotion dur="{T}s" repeatCount="indefinite" keyPoints="0;0;1;1" keyTimes="0;.44;.52;1" calcMode="linear" path="{lp}"/>'
             f'<animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="0;.43;.44;.51;.52;1" dur="{T}s" repeatCount="indefinite"/></circle>')
    b.append(f'<circle r="6" fill="var(--warn)" opacity="0"><animateMotion dur="{T}s" repeatCount="indefinite" keyPoints="0;0;1;0;0" keyTimes="0;.52;.62;.72;1" calcMode="linear" path="{jw}"/>'
             f'<animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="0;.51;.52;.71;.72;1" dur="{T}s" repeatCount="indefinite"/></circle>')
    b.append(f'<circle r="7" fill="var(--good)" opacity="0"><animateMotion dur="{T}s" repeatCount="indefinite" keyPoints="0;0;1;1" keyTimes="0;.76;.88;1" calcMode="linear" path="{dw}"/>'
             f'<animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="0;.75;.76;.88;.89;1" dur="{T}s" repeatCount="indefinite"/></circle>')
    # step strip
    steps = ["1 Assign", "2 Vote", "3 Count", "4 Hint", "5 Accept"]
    spans = [(0, .16), (.16, .32), (.32, .52), (.52, .74), (.74, .96)]
    for j, (lab, (a, z)) in enumerate(zip(steps, spans)):
        x = 34 + j * 112
        b.append(f'<rect x="{x}" y="394" width="104" height="24" rx="12" fill="var(--acc)" opacity=".12">'
                 f'<animate attributeName="opacity" values=".12;.12;1;1;.12;.12" keyTimes="0;{a:.2f};{a+.01:.2f};{z:.2f};{z+.01:.2f};1" dur="{T}s" repeatCount="indefinite"/></rect>')
        b.append(f'<text x="{x+52}" y="410" text-anchor="middle" class="sans ink" font-size="12" font-weight="600">{lab}</text>')
    return svg(W, H, "\n".join(b), "Architecture: the coordinator sends an item to five nodes, collects signed answers, logs every vote, asks the judge for a hint on splits, and adds agreed items to the dataset.")


# ---------- agreement vs accuracy line chart ----------
def agreement():
    W, H = 960, 340
    pts = [(2, 80.0, "24/30"), (3, 90.3, "56/62"), (4, 98.7, "150/152"), (5, 99.6, "244/245")]
    L, R, T, B = 90, 60, 70, 70
    x = lambda k: L + (k - 2) / 3 * (W - L - R)
    y = lambda v: T + (1 - (v - 60) / 40) * (H - T - B)
    b = ['<text x="44" y="42" class="sans cap">MORE AGREEMENT, MORE ACCURACY · 500 MATHS PROBLEMS</text>']
    for v in (60, 70, 80, 90, 100):
        b.append(f'<line x1="{L}" x2="{W-R}" y1="{y(v)}" y2="{y(v)}" stroke="var(--rule)"/><text x="{L-12}" y="{y(v)+4}" text-anchor="end" class="mono mut" font-size="12">{v}%</text>')
    for k, _, _ in pts:
        b.append(f'<text x="{x(k)}" y="{H-B+28}" text-anchor="middle" class="sans ink2" font-size="13">{k} of 5 agree</text>')
    d = " ".join(f"{'M' if i == 0 else 'L'}{x(k)},{y(v)}" for i, (k, v, _) in enumerate(pts))
    area = d + f" L{x(5)},{y(60)} L{x(2)},{y(60)} Z"
    b.append(f'<path d="{area}" fill="var(--acc)" opacity=".08"/>')
    b.append(f'<path d="{d}" fill="none" stroke="var(--acc)" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="900" stroke-dashoffset="900">'
             f'<animate attributeName="stroke-dashoffset" from="900" to="0" dur="1.6s" begin=".2s" fill="freeze"/></path>')
    for i, (k, v, n) in enumerate(pts):
        b.append(f'<circle cx="{x(k)}" cy="{y(v)}" r="6" fill="var(--bg)" stroke="var(--acc)" stroke-width="3" opacity="0"><animate attributeName="opacity" from="0" to="1" dur=".3s" begin="{0.4+i*0.4:.1f}s" fill="freeze"/></circle>')
        b.append(f'<text x="{x(k)}" y="{y(v)-16}" text-anchor="middle" class="mono acc" font-size="16" font-weight="600" opacity="0">{v:g}%<animate attributeName="opacity" from="0" to="1" dur=".3s" begin="{0.5+i*0.4:.1f}s" fill="freeze"/></text>')
        b.append(f'<text x="{x(k)}" y="{y(v)+26}" text-anchor="middle" class="mono mut" font-size="11" opacity="0">{n}<animate attributeName="opacity" from="0" to="1" dur=".3s" begin="{0.5+i*0.4:.1f}s" fill="freeze"/></text>')
    b.append(f'<text x="{W-R}" y="{H-14}" text-anchor="end" class="sans ink2" font-size="13">4 or 5 of 5 agree → right about 99% of the time. That is the acceptance rule.</text>')
    return svg(W, H, "\n".join(b), "Accuracy by agreement: 80% at 2 of 5, 90% at 3 of 5, 98.7% at 4 of 5, 99.6% at 5 of 5.")


# ---------- one model vs refinery, paired bars ----------
def compare():
    W, H = 960, 300
    rows = [("Error detection", 77.7, 97.6), ("Maths, ten model families", 81.0, 98.6), ("Maths, Llama only", 73.7, 98.7)]
    b = ['<text x="44" y="42" class="sans cap">ONE MODEL ALONE vs THE REFINERY · SHARE OF CORRECT ANSWERS</text>']
    L, Wb = 300, 520
    for i, (name, a, r) in enumerate(rows):
        y = 74 + i * 72
        b.append(f'<text x="44" y="{y+18}" class="sans ink" font-size="15" font-weight="600">{name}</text>')
        for j, (v, col, lab) in enumerate([(a, "var(--mut)", "one model"), (r, "var(--acc)", "refinery")]):
            yy = y + j * 22
            b.append(f'<text x="{L-12}" y="{yy+12}" text-anchor="end" class="sans mut" font-size="11.5">{lab}</text>')
            b.append(f'<rect x="{L}" y="{yy+2}" width="{Wb}" height="12" rx="6" fill="var(--track)"/>')
            b.append(f'<rect x="{L}" y="{yy+2}" width="0" height="12" rx="6" fill="{col}"><animate attributeName="width" from="0" to="{Wb*v/100:.1f}" dur="1s" begin="{0.2+i*0.25:.2f}s" fill="freeze" calcMode="spline" keySplines=".2 .7 .2 1"/></rect>')
            b.append(f'<text x="{L+Wb+14}" y="{yy+13}" class="mono {"acc" if j else "ink2"}" font-size="14" font-weight="600">{v:g}%</text>')
    b.append('<text x="44" y="282" class="sans mut" font-size="12">Checked afterwards against official answers the nodes never saw.</text>')
    return svg(W, H, "\n".join(b), "One model alone vs refinery: error detection 78% to 97.6%, maths ten families 81% to 98.6%, maths Llama only 74% to 98.7%.")


# ---------- robot at the desk: picks each paper, scans it, keeps good ones, bins bad ones ----------
def robot():
    W, H, T = 960, 300, 8
    SL, SR = (150, 106), (210, 106)
    RL, RR, TRAY, MID, PILE, WIND, BIN = (140, 150), (220, 150), (95, 137), (180, 126), (258, 150), (226, 112), (321, 200)

    def arm(S, Hd, side):
        cx, cy = (S[0] + Hd[0]) / 2 + (-14 if side == "L" else 14), (S[1] + Hd[1]) / 2 + 16
        return f"M{S[0]},{S[1]} Q{cx},{cy} {Hd[0]},{Hd[1]}"

    def track(frames, side):  # frames: [(time fraction, point)]
        S = SL if side == "L" else SR
        kt = ";".join(f"{t:g}" for t, _ in frames)
        d = ";".join(arm(S, p, side) for _, p in frames)
        cx = ";".join(str(p[0]) for _, p in frames)
        cy = ";".join(str(p[1]) for _, p in frames)
        return (f'<path fill="none" stroke="var(--ink2)" stroke-width="5" stroke-linecap="round"><animate attributeName="d" values="{d}" keyTimes="{kt}" dur="{T}s" repeatCount="indefinite"/></path>'
                f'<circle r="5.5" fill="var(--bg)" stroke="var(--ink2)" stroke-width="2"><animate attributeName="cx" values="{cx}" keyTimes="{kt}" dur="{T}s" repeatCount="indefinite"/>'
                f'<animate attributeName="cy" values="{cy}" keyTimes="{kt}" dur="{T}s" repeatCount="indefinite"/></circle>')

    def paper(frames, rot, vis):
        kt = ";".join(f"{t:g}" for t, _ in frames)
        tr = ";".join(f"{p[0]},{p[1] - 6}" for _, p in frames)
        rv = ";".join(str(r) for r in rot)
        return (f'<g opacity="0"><animate attributeName="opacity" values="{vis[1]}" keyTimes="{vis[0]}" dur="{T}s" repeatCount="indefinite" calcMode="discrete"/>'
                f'<animateTransform attributeName="transform" type="translate" values="{tr}" keyTimes="{kt}" dur="{T}s" repeatCount="indefinite"/>'
                f'<g><animateTransform attributeName="transform" type="rotate" values="{rv}" keyTimes="{kt}" dur="{T}s" repeatCount="indefinite"/>'
                f'<rect x="-15" y="-19" width="30" height="38" rx="2" fill="#fff" stroke="var(--rule)" stroke-width="1.2"/>'
                + "".join(f'<line x1="-9" y1="{y}" x2="{x2}" y2="{y}" stroke="var(--mut)" stroke-width="1.4" stroke-linecap="round"/>' for y, x2 in ((-10, 9), (-3, 7), (4, 9), (11, 2)))
                + '</g></g>')

    b = []
    ox = 300  # centre the scene in the card
    b.append(f'<g transform="translate({ox},10)">')
    # robot body
    b.append('<line x1="180" y1="44" x2="180" y2="30" stroke="var(--ink2)" stroke-width="2"/>')
    b.append(f'<circle cx="180" cy="27" r="4" fill="var(--acc)"><animate attributeName="fill" values="var(--acc);var(--good);var(--acc);var(--bad);var(--acc)" keyTimes="0;.3;.36;.8;.86" dur="{T}s" repeatCount="indefinite" calcMode="discrete"/></circle>')
    b.append('<rect x="152" y="44" width="56" height="42" rx="12" fill="var(--bg)" stroke="var(--ink2)" stroke-width="2"/>')
    b.append('<rect x="160" y="55" width="40" height="18" rx="9" fill="var(--bg)" stroke="var(--ink2)" stroke-width="1.5"/>')
    for ex in (172, 188):
        b.append(f'<circle cx="{ex}" cy="64" r="3.6" fill="var(--acc)"><animate attributeName="cx" values="{ex};{ex};{ex-2};{ex+2};{ex};{ex};{ex-2};{ex+2};{ex}" keyTimes="0;.17;.21;.25;.29;.67;.71;.75;.79" dur="{T}s" repeatCount="indefinite"/></circle>')
    b.append('<rect x="146" y="92" width="68" height="62" rx="14" fill="var(--bg)" stroke="var(--ink2)" stroke-width="2"/>')
    b.append('<circle cx="180" cy="122" r="7" fill="none" stroke="var(--acc)" stroke-width="2"/>')
    # desk, tray, pile, bin
    b.append('<rect x="40" y="162" width="250" height="5" rx="2.5" fill="var(--ink2)" opacity=".85"/>')
    b.append('<path d="M72,162 L76,152 L114,152 L118,162" fill="none" stroke="var(--mut)" stroke-width="1.5"/>')
    b.append(f'<rect x="242" y="154" width="32" height="8" rx="1.5" fill="#fff" stroke="var(--good)" stroke-width="1.2"/>')
    b.append(f'<rect x="243" y="146" width="32" height="8" rx="1.5" fill="#fff" stroke="var(--good)" stroke-width="1.2" opacity="0"><animate attributeName="opacity" values="0;1;1" keyTimes="0;.47;1" dur="{T}s" repeatCount="indefinite" calcMode="discrete"/></rect>')
    b.append('<rect x="300" y="196" width="42" height="50" rx="4" fill="var(--bg)" stroke="var(--bad)" stroke-width="2"/>')
    b.append(f'<rect x="296" y="190" width="50" height="6" rx="2" fill="var(--bad)"><animate attributeName="y" values="190;184;190" keyTimes="0;.955;.98" dur="{T}s" repeatCount="indefinite"/></rect>')
    for lx in (313, 321, 329):
        b.append(f'<line x1="{lx}" y1="206" x2="{lx}" y2="238" stroke="var(--bad)" stroke-opacity=".4"/>')
    # papers: one good (first half), one bad (second half)
    b.append(paper([(0, TRAY), (.06, TRAY), (.16, MID), (.36, MID), (.46, PILE), (1, PILE)], [0] * 6, ("0;.46", "1;0")))
    b.append(paper([(0, TRAY), (.56, TRAY), (.66, MID), (.86, MID), (.89, WIND), (.95, BIN), (1, BIN)], [0, 0, 0, 0, 0, 260, 260], ("0;.5;.95", "0;1;0")))
    # arms on top so the hands hold the paper
    b.append(track([(0, RL), (.06, TRAY), (.16, MID), (.30, MID), (.36, RL), (.50, RL), (.56, TRAY), (.66, MID), (.80, MID), (.86, RL), (1, RL)], "L"))
    b.append(track([(0, RR), (.30, RR), (.36, MID), (.46, PILE), (.52, RR), (.80, RR), (.86, MID), (.89, WIND), (.95, RR), (1, RR)], "R"))
    # scan line and verdict marks
    b.append(f'<line x1="164" x2="196" y1="103" y2="103" stroke="var(--acc)" stroke-width="2" opacity="0"><animate attributeName="opacity" values="0;.9;0;.9;0" keyTimes="0;.17;.29;.67;.79" dur="{T}s" repeatCount="indefinite" calcMode="discrete"/>'
             f'<animate attributeName="y1" values="103;103;139;103;139;103;103;139;103;139;103" keyTimes="0;.17;.2;.23;.26;.29;.67;.7;.73;.76;1" dur="{T}s" repeatCount="indefinite"/>'
             f'<animate attributeName="y2" values="103;103;139;103;139;103;103;139;103;139;103" keyTimes="0;.17;.2;.23;.26;.29;.67;.7;.73;.76;1" dur="{T}s" repeatCount="indefinite"/></line>')
    b.append(f'<text x="180" y="100" text-anchor="middle" class="sans good" font-size="22" font-weight="700" opacity="0">✓<animate attributeName="opacity" values="0;1;0" keyTimes="0;.3;.36" dur="{T}s" repeatCount="indefinite" calcMode="discrete"/></text>')
    b.append(f'<text x="180" y="100" text-anchor="middle" class="sans bad" font-size="22" font-weight="700" opacity="0">✗<animate attributeName="opacity" values="0;1;0" keyTimes="0;.8;.86" dur="{T}s" repeatCount="indefinite" calcMode="discrete"/></text>')
    b.append('</g>')
    return svg(W, H, "\n".join(b), "A robot picks up each item, checks it, keeps the good ones and throws the bad ones in the bin.")


# ---------- fine-tuned example: base vs fine-tuned Llama 3.2 1B, vertical bars growing in ----------
def finetune():
    W, H = 960, 360
    rows = [("News sentiment", 3.7, 50.3), ("Financial news (new set)", 26.3, 69.0), ("Negation (hard)", 79.0, 85.5), ("Maths", 33.7, 35.0)]
    b = ['<text x="44" y="40" class="sans cap">LLAMA 3.2 1B · BASE vs FINE-TUNED ON REFINED DATA · UNSEEN TESTS</text>',
         '<text x="44" y="64" class="sans ink2" font-size="13">Trained on 1,331 refined headline labels + 592 refined maths answers · about 4 minutes, 1 epoch</text>']
    base_y, top, colw = 280, 100, 210
    for i, (name, a, f) in enumerate(rows):
        x0 = 70 + i * colw
        mx = max(a, f) * 1.12
        for j, (v, col) in enumerate([(a, "var(--mut)"), (f, "var(--acc)")]):
            h = max(3, (base_y - top) * v / mx); x = x0 + j * 62
            b.append(f'<rect x="{x}" y="{base_y}" width="50" height="0" rx="5" fill="{col}">'
                     f'<animate attributeName="height" from="0" to="{h:.1f}" dur="1s" begin="{0.2 + i * 0.2:.2f}s" fill="freeze" calcMode="spline" keySplines=".2 .7 .2 1"/>'
                     f'<animate attributeName="y" from="{base_y}" to="{base_y - h:.1f}" dur="1s" begin="{0.2 + i * 0.2:.2f}s" fill="freeze" calcMode="spline" keySplines=".2 .7 .2 1"/></rect>')
            b.append(f'<text x="{x + 25}" y="{base_y - h - 7:.1f}" text-anchor="middle" class="mono {"acc" if j else "ink2"}" font-size="13" font-weight="600">{v:g}</text>')
        b.append(f'<line x1="{x0 - 8}" x2="{x0 + 120}" y1="{base_y}" y2="{base_y}" stroke="var(--rule)"/>')
        b.append(f'<text x="{x0 + 56}" y="{base_y + 24}" text-anchor="middle" class="mono good" font-size="14" font-weight="700">+{f - a:.1f}</text>')
        b.append(f'<text x="{x0 + 56}" y="{base_y + 44}" text-anchor="middle" class="sans ink2" font-size="12.5">{name}</text>')
    b.append('<rect x="44" y="328" width="10" height="10" rx="2" fill="var(--mut)"/><text x="60" y="337" class="sans mut" font-size="12">base model</text>')
    b.append('<rect x="150" y="328" width="10" height="10" rx="2" fill="var(--acc)"/><text x="166" y="337" class="sans mut" font-size="12">fine-tuned on refined data</text>')
    return svg(W, H, "\n".join(b), "Llama 3.2 1B base vs fine-tuned on refined data: news 3.7 to 50.3, financial news 26.3 to 69.0, negation 79.0 to 85.5, maths 33.7 to 35.0.")


for name, fn in [("hero", hero), ("result", result), ("architecture", architecture), ("agreement", agreement), ("compare", compare), ("robot", robot), ("finetune", finetune)]:
    (OUT / f"{name}.svg").write_text(fn(), encoding="utf-8")
    print("wrote", name)
