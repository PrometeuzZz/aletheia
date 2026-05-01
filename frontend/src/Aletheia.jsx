import { useState, useCallback, useRef, useEffect } from "react";

const OECD = new Set(["AU","AT","BE","CA","CL","CO","CR","CZ","DK","EE","FI","FR","DE","GR","HU","IS","IE","IL","IT","JP","KR","LV","LT","LU","MX","NL","NZ","NO","PL","PT","SK","SI","ES","SE","CH","TR","GB","US"]);
const OECD_PERIPHERAL = new Set(["CO","CL","MX","CR","TR","HU","LV","LT","EE","SK"]);
const LANG_TIERS = { en:0, es:0.7, pt:0.7, fr:0.7, de:0.7, it:0.7, zh:0.9, ar:0.9, ru:0.9, ja:0.9, ko:0.9, hi:0.9, tr:0.9, fa:0.9, id:0.9, sw:0.9, vi:0.9, bn:0.9, th:0.9 };
const LANG_NAMES = { en:"English", es:"Spanish", pt:"Portuguese", fr:"French", de:"German", it:"Italian", zh:"Chinese", ar:"Arabic", ru:"Russian", ja:"Japanese", ko:"Korean", hi:"Hindi", tr:"Turkish", fa:"Persian", id:"Indonesian", sw:"Swahili", vi:"Vietnamese", bn:"Bengali", th:"Thai", ms:"Malay", uk:"Ukrainian", pl:"Polish", nl:"Dutch", ca:"Catalan", cs:"Czech", ro:"Romanian", hu:"Hungarian", sv:"Swedish", da:"Danish", no:"Norwegian", fi:"Finnish", el:"Greek", he:"Hebrew", sr:"Serbian", hr:"Croatian", sk:"Slovak", sl:"Slovenian", bg:"Bulgarian", lt:"Lithuanian", lv:"Latvian", et:"Estonian" };
const COUNTRY_NAMES = { US:"United States", GB:"United Kingdom", DE:"Germany", FR:"France", CA:"Canada", AU:"Australia", NL:"Netherlands", IT:"Italy", ES:"Spain", BR:"Brazil", CN:"China", JP:"Japan", KR:"South Korea", IN:"India", MX:"Mexico", CO:"Colombia", CL:"Chile", AR:"Argentina", ZA:"South Africa", NG:"Nigeria", KE:"Kenya", EG:"Egypt", TR:"Turkey", RU:"Russia", SE:"Sweden", NO:"Norway", DK:"Denmark", FI:"Finland", BE:"Belgium", CH:"Switzerland", AT:"Austria", PT:"Portugal", IE:"Ireland", NZ:"New Zealand", IL:"Israel", PL:"Poland", CZ:"Czechia", GR:"Greece", HU:"Hungary", RO:"Romania", PE:"Peru", EC:"Ecuador", VE:"Venezuela", UY:"Uruguay", CR:"Costa Rica", PA:"Panama", DO:"Dominican Republic", GT:"Guatemala", CU:"Cuba", BO:"Bolivia", PY:"Paraguay", HN:"Honduras", SV:"El Salvador", NI:"Nicaragua", TW:"Taiwan", TH:"Thailand", MY:"Malaysia", SG:"Singapore", ID:"Indonesia", PH:"Philippines", VN:"Vietnam", PK:"Pakistan", BD:"Bangladesh", LK:"Sri Lanka", MM:"Myanmar", KH:"Cambodia" };

function parseWork(r) {
  const auths = (r.authorships||[]);
  const names = auths.map(a => a.author?.display_name).filter(Boolean);
  const countries = [], instNames = [], instTypes = [];
  auths.forEach(a => (a.institutions||[]).forEach(i => {
    if (i.country_code) countries.push(i.country_code);
    if (i.display_name) instNames.push(i.display_name);
    if (i.type) instTypes.push(i.type);
  }));
  const src = r.primary_location?.source || {};
  let abstract = null;
  if (r.abstract_inverted_index) {
    const pairs = [];
    for (const [w, pos] of Object.entries(r.abstract_inverted_index))
      pos.forEach(p => pairs.push([p, w]));
    pairs.sort((a,b) => a[0]-b[0]);
    abstract = pairs.map(p=>p[1]).join(" ");
  }
  const concepts = (r.topics||[]).slice(0,5).map(t=>t.display_name).filter(Boolean);
  return {
    id: r.id, title: r.title||"Untitled", year: r.publication_year,
    citations: r.cited_by_count||0, relevance: r.relevance_score||0,
    authors: names, countries: [...new Set(countries)], instNames: [...new Set(instNames)],
    instTypes, sourceName: src.display_name, sourceOa: src.is_oa||false,
    lang: r.language, doi: r.doi, abstract, concepts,
    oaUrl: r.open_access?.oa_url, canonicalScore: 0, peripheryScore: 0, breakdown: {}
  };
}

function rerank(works, weights) {
  if (!works.length) return { canonical: [], periphery: [] };
  const maxRel = Math.max(...works.map(w=>w.relevance))||1;
  const minRel = Math.min(...works.map(w=>w.relevance));
  const relRange = maxRel - minRel || 1;
  const maxCit = Math.max(...works.map(w=>w.citations))||1;
  const yr = new Date().getFullYear();

  works.forEach(w => {
    const normRel = (w.relevance - minRel) / relRange;
    const logCit = Math.log2(Math.max(w.citations,1)+1);
    const maxLog = Math.log2(maxCit+1)||1;
    let invCit = 1 - logCit/maxLog;
    if (w.year) {
      const age = yr - w.year;
      if (age >= 0 && age <= 10) invCit = Math.min(1, invCit + (1 - age/10)*0.1);
    }
    let instScore = 0.5;
    if (w.countries.length) {
      const t = w.countries.length;
      const nonOecd = w.countries.filter(c=>!OECD.has(c)).length;
      const oecdPerif = w.countries.filter(c=>OECD_PERIPHERAL.has(c)).length;
      const core = t - nonOecd - oecdPerif;
      instScore = (nonOecd*1 + oecdPerif*0.5 + core*0) / t;
      if (w.instTypes.some(t=>t&&!["education","university","college"].includes(t.toLowerCase())))
        instScore = Math.min(1, instScore + 0.1);
    }
    const langScore = w.lang ? (LANG_TIERS[w.lang] ?? 0.8) : 0.3;
    const ps = weights.relevance*normRel + weights.citation*invCit + weights.institutional*instScore + weights.language*langScore;
    w.canonicalScore = normRel;
    w.peripheryScore = Math.round(ps*1e4)/1e4;
    w.breakdown = { relevance: normRel, inverseCitation: invCit, institutional: instScore, language: langScore };
  });

  const canonical = [...works].sort((a,b)=>b.canonicalScore-a.canonicalScore);
  const periphery = [...works].sort((a,b)=>b.peripheryScore-a.peripheryScore);
  return { canonical, periphery };
}

const PRESETS = [
  "transitional justice land restitution Colombia",
  "forced displacement post-conflict reconstruction",
  "epistemic injustice knowledge production Global South",
  "desplazamiento forzado justicia transicional",
  "indigenous knowledge decolonial methodology",
];

function ScoreBar({ value, color, label }) {
  return (
    <div style={{ display:"flex", alignItems:"center", gap:6, fontSize:11 }}>
      <span style={{ width:62, color:"#8a8a7e", flexShrink:0 }}>{label}</span>
      <div style={{ flex:1, height:4, background:"rgba(128,128,120,0.12)", borderRadius:2, overflow:"hidden" }}>
        <div style={{ width:`${Math.max(2, value*100)}%`, height:"100%", background:color, borderRadius:2, transition:"width 0.4s ease" }} />
      </div>
      <span style={{ width:32, textAlign:"right", color:"#8a8a7e", fontFamily:"'DM Mono', monospace", fontSize:10 }}>{(value*100).toFixed(0)}%</span>
    </div>
  );
}

function WorkCard({ work, rank, stream }) {
  const [open, setOpen] = useState(false);
  const isPeriphery = stream === "periphery";
  const accent = isPeriphery ? "#1d7a5f" : "#5a5a52";
  const bg = isPeriphery ? "rgba(29,122,95,0.04)" : "transparent";
  const moved = (() => {
    if (!work._canonicalRank || !work._peripheryRank) return null;
    const diff = work._canonicalRank - work._peripheryRank;
    if (diff > 2) return { dir: "up", n: diff };
    if (diff < -2) return { dir: "down", n: Math.abs(diff) };
    return null;
  })();

  return (
    <div style={{ padding:"14px 16px", borderBottom:"1px solid rgba(128,128,120,0.1)", background:bg, cursor:"pointer", transition:"background 0.2s" }} onClick={()=>setOpen(!open)}>
      <div style={{ display:"flex", gap:10, alignItems:"flex-start" }}>
        <span style={{ fontFamily:"'DM Mono', monospace", fontSize:11, color:"#8a8a7e", minWidth:20, paddingTop:2 }}>{rank}</span>
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{ display:"flex", alignItems:"center", gap:8, flexWrap:"wrap" }}>
            <h4 style={{ margin:0, fontSize:13.5, fontWeight:500, lineHeight:1.35, color:"#2a2a26", fontFamily:"'Source Serif 4', Georgia, serif" }}>{work.title}</h4>
            {isPeriphery && moved?.dir === "up" && <span style={{ fontSize:10, padding:"1px 6px", borderRadius:8, background:"rgba(29,122,95,0.1)", color:"#1d7a5f", fontWeight:500, whiteSpace:"nowrap" }}>+{moved.n} positions</span>}
          </div>
          <div style={{ display:"flex", flexWrap:"wrap", gap:4, marginTop:5, fontSize:11.5, color:"#6a6a62", lineHeight:1.5 }}>
            <span>{work.authors.slice(0,3).join(", ")}{work.authors.length>3 ? ` +${work.authors.length-3}` : ""}</span>
            {work.year && <span style={{ color:"#9a9a92" }}>· {work.year}</span>}
            {work.sourceName && <span style={{ color:"#9a9a92" }}>· {work.sourceName}</span>}
          </div>
          <div style={{ display:"flex", flexWrap:"wrap", gap:6, marginTop:6 }}>
            <span style={{ fontSize:10, padding:"2px 7px", borderRadius:6, background:"rgba(128,128,120,0.08)", color:"#6a6a62" }}>
              {work.citations.toLocaleString()} cit.
            </span>
            {work.lang && work.lang !== "en" && (
              <span style={{ fontSize:10, padding:"2px 7px", borderRadius:6, background:"rgba(29,122,95,0.08)", color:"#1d7a5f" }}>
                {LANG_NAMES[work.lang] || work.lang}
              </span>
            )}
            {work.countries.filter(c=>!OECD.has(c)||OECD_PERIPHERAL.has(c)).map(c => (
              <span key={c} style={{ fontSize:10, padding:"2px 7px", borderRadius:6, background:"rgba(166,120,50,0.08)", color:"#8a6a2a" }}>
                {COUNTRY_NAMES[c]||c}
              </span>
            ))}
            {work.sourceOa && <span style={{ fontSize:10, padding:"2px 7px", borderRadius:6, background:"rgba(29,95,122,0.08)", color:"#1d5f7a" }}>Open Access</span>}
          </div>
        </div>
      </div>
      {open && (
        <div style={{ marginTop:12, marginLeft:30, paddingTop:10, borderTop:"1px dashed rgba(128,128,120,0.15)" }}>
          {work.abstract && <p style={{ fontSize:12, lineHeight:1.6, color:"#4a4a44", margin:"0 0 10px" }}>{work.abstract.slice(0,350)}{work.abstract.length>350?"...":""}</p>}
          <div style={{ display:"flex", flexDirection:"column", gap:3, marginBottom:8 }}>
            <ScoreBar value={work.breakdown.relevance} color="#5a5a52" label="Relevance" />
            <ScoreBar value={work.breakdown.inverseCitation} color="#a06830" label="Inv. citation" />
            <ScoreBar value={work.breakdown.institutional} color="#1d7a5f" label="Institutional" />
            <ScoreBar value={work.breakdown.language} color="#5a3a8a" label="Language" />
          </div>
          {work.concepts.length>0 && <div style={{ fontSize:10.5, color:"#8a8a7e", marginTop:6 }}>Topics: {work.concepts.join(" · ")}</div>}
          <div style={{ display:"flex", gap:10, marginTop:8 }}>
            {work.doi && <a href={work.doi} target="_blank" rel="noopener" style={{ fontSize:11, color:"#1d5f7a" }} onClick={e=>e.stopPropagation()}>DOI</a>}
            {work.oaUrl && <a href={work.oaUrl} target="_blank" rel="noopener" style={{ fontSize:11, color:"#1d7a5f" }} onClick={e=>e.stopPropagation()}>Open Access PDF</a>}
            {work.id && <a href={work.id.replace("https://openalex.org/","https://openalex.org/works/")} target="_blank" rel="noopener" style={{ fontSize:11, color:"#8a6a2a" }} onClick={e=>e.stopPropagation()}>OpenAlex</a>}
          </div>
        </div>
      )}
    </div>
  );
}

function WeightSlider({ label, value, onChange, color, description }) {
  return (
    <div style={{ marginBottom:10 }}>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:3 }}>
        <span style={{ fontSize:11, color:"#4a4a44", fontWeight:500 }}>{label}</span>
        <span style={{ fontSize:11, fontFamily:"'DM Mono', monospace", color }}>{(value*100).toFixed(0)}%</span>
      </div>
      <input type="range" min="0" max="100" value={Math.round(value*100)} onChange={e=>onChange(parseInt(e.target.value)/100)}
        style={{ width:"100%", height:4, appearance:"none", background:`linear-gradient(to right, ${color} ${value*100}%, rgba(128,128,120,0.12) ${value*100}%)`, borderRadius:2, outline:"none", cursor:"pointer" }} />
      <div style={{ fontSize:10, color:"#8a8a7e", marginTop:2 }}>{description}</div>
    </div>
  );
}

function Stats({ canonical, periphery }) {
  if (!periphery.length) return null;
  const topPerif = periphery.slice(0,10);
  const topCan = canonical.slice(0,10);
  const canIds = new Set(topCan.map(w=>w.id));
  const surfaced = topPerif.filter(w=>!canIds.has(w.id)).length;
  const avgCitCan = topCan.reduce((s,w)=>s+w.citations,0)/topCan.length;
  const avgCitPerif = topPerif.reduce((s,w)=>s+w.citations,0)/topPerif.length;
  const nonEnCan = topCan.filter(w=>w.lang&&w.lang!=="en").length;
  const nonEnPerif = topPerif.filter(w=>w.lang&&w.lang!=="en").length;
  const nonOecdCan = topCan.filter(w=>w.countries.some(c=>!OECD.has(c))).length;
  const nonOecdPerif = topPerif.filter(w=>w.countries.some(c=>!OECD.has(c))).length;

  return (
    <div style={{ display:"grid", gridTemplateColumns:"repeat(4, 1fr)", gap:10, margin:"16px 0", padding:"14px 16px", background:"rgba(128,128,120,0.04)", borderRadius:10 }}>
      {[
        { label:"Newly surfaced", val:`${surfaced}/10`, desc:"Papers in periphery top-10 that aren't in canonical top-10" },
        { label:"Avg. citations", val:`${Math.round(avgCitPerif)} vs ${Math.round(avgCitCan)}`, desc:"Periphery vs canonical top-10" },
        { label:"Non-English", val:`${nonEnPerif} vs ${nonEnCan}`, desc:"Papers in non-English languages" },
        { label:"Non-OECD authors", val:`${nonOecdPerif} vs ${nonOecdCan}`, desc:"Papers with Global South affiliations" },
      ].map(s => (
        <div key={s.label} style={{ textAlign:"center" }}>
          <div style={{ fontSize:18, fontWeight:500, color:"#2a2a26", fontFamily:"'DM Mono', monospace" }}>{s.val}</div>
          <div style={{ fontSize:10, color:"#1d7a5f", fontWeight:500, marginTop:2 }}>{s.label}</div>
          <div style={{ fontSize:9, color:"#8a8a7e", marginTop:1 }}>{s.desc}</div>
        </div>
      ))}
    </div>
  );
}

export default function Aletheia() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [canonical, setCanonical] = useState([]);
  const [periphery, setPeriphery] = useState([]);
  const [error, setError] = useState(null);
  const [weights, setWeights] = useState({ relevance:0.40, citation:0.25, institutional:0.20, language:0.15 });
  const [showConfig, setShowConfig] = useState(false);
  const [searched, setSearched] = useState(false);
  const inputRef = useRef(null);

  const updateWeight = useCallback((key, val) => {
    setWeights(prev => {
      const others = Object.keys(prev).filter(k=>k!==key);
      const remaining = 1 - val;
      const otherSum = others.reduce((s,k)=>s+prev[k],0) || 1;
      const next = { ...prev, [key]: val };
      others.forEach(k => { next[k] = Math.max(0, (prev[k]/otherSum)*remaining); });
      return next;
    });
  }, []);

  const search = useCallback(async (q) => {
    const searchQuery = q || query;
    if (!searchQuery.trim()) return;
    setLoading(true); setError(null); setSearched(true);
    try {
      const params = new URLSearchParams({
        search: searchQuery, per_page: "40",
        select: "id,title,publication_year,cited_by_count,relevance_score,authorships,primary_location,language,doi,open_access,topics,abstract_inverted_index",
        mailto: "aletheia@example.org",
      });
      const res = await fetch(`https://api.openalex.org/works?${params}`);
      if (!res.ok) throw new Error(`OpenAlex returned ${res.status}`);
      const data = await res.json();
      const works = (data.results||[]).map(parseWork);
      const { canonical: c, periphery: p } = rerank(works, weights);
      c.forEach((w,i) => w._canonicalRank = i+1);
      p.forEach((w,i) => w._peripheryRank = i+1);
      c.forEach(w => { const pw = p.find(x=>x.id===w.id); if(pw) w._peripheryRank = pw._peripheryRank; });
      p.forEach(w => { const cw = c.find(x=>x.id===w.id); if(cw) w._canonicalRank = cw._canonicalRank; });
      setCanonical(c); setPeriphery(p);
    } catch(e) { setError(e.message); setCanonical([]); setPeriphery([]); }
    setLoading(false);
  }, [query, weights]);

  useEffect(() => { inputRef.current?.focus(); }, []);

  return (
    <div style={{ fontFamily:"'Source Serif 4', Georgia, serif", maxWidth:1080, margin:"0 auto", padding:"24px 16px", color:"#2a2a26" }}>
      <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,500;0,8..60,600;1,8..60,400&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet" />
      <style>{`
        input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:12px; height:12px; border-radius:50%; background:#2a2a26; cursor:pointer; border:2px solid white; box-shadow:0 1px 3px rgba(0,0,0,0.2); }
        input[type=range]::-moz-range-thumb { width:12px; height:12px; border-radius:50%; background:#2a2a26; cursor:pointer; border:2px solid white; box-shadow:0 1px 3px rgba(0,0,0,0.2); }
        ::selection { background:rgba(29,122,95,0.15); }
        a:hover { opacity:0.7; }
      `}</style>

      {/* Header */}
      <div style={{ textAlign:"center", marginBottom:28 }}>
        <h1 style={{ fontSize:28, fontWeight:600, letterSpacing:1.5, margin:0, color:"#2a2a26" }}>
          ALETHEIA
        </h1>
        <p style={{ fontSize:12, color:"#8a8a7e", marginTop:4, fontFamily:"'DM Mono', monospace", letterSpacing:0.5 }}>
          ἀλήθεια — unconcealment
        </p>
        <p style={{ fontSize:13, color:"#6a6a62", marginTop:8, maxWidth:520, margin:"8px auto 0", lineHeight:1.55 }}>
          Dual-stream academic search. The canonical stream shows what every search engine shows. The periphery stream surfaces what they bury.
        </p>
      </div>

      {/* Search */}
      <div style={{ display:"flex", gap:8, marginBottom:8 }}>
        <input ref={inputRef} type="text" value={query} onChange={e=>setQuery(e.target.value)}
          onKeyDown={e=>e.key==="Enter"&&search()}
          placeholder="Search academic literature..."
          style={{ flex:1, padding:"10px 14px", border:"1px solid rgba(128,128,120,0.2)", borderRadius:8, fontSize:14, fontFamily:"'Source Serif 4', Georgia, serif", outline:"none", background:"rgba(128,128,120,0.03)" }} />
        <button onClick={()=>search()} disabled={loading}
          style={{ padding:"10px 20px", background:"#2a2a26", color:"white", border:"none", borderRadius:8, fontSize:13, fontWeight:500, cursor:loading?"wait":"pointer", fontFamily:"'DM Mono', monospace", opacity:loading?0.6:1 }}>
          {loading ? "Searching..." : "Search"}
        </button>
      </div>

      {/* Preset queries */}
      {!searched && (
        <div style={{ display:"flex", flexWrap:"wrap", gap:6, marginBottom:16 }}>
          {PRESETS.map(p => (
            <button key={p} onClick={()=>{setQuery(p);search(p);}}
              style={{ padding:"5px 10px", fontSize:11, border:"1px solid rgba(128,128,120,0.15)", borderRadius:14, background:"transparent", cursor:"pointer", color:"#6a6a62", fontFamily:"'Source Serif 4', Georgia, serif" }}>
              {p}
            </button>
          ))}
        </div>
      )}

      {/* Config toggle */}
      <div style={{ display:"flex", justifyContent:"flex-end", marginBottom:12 }}>
        <button onClick={()=>setShowConfig(!showConfig)}
          style={{ fontSize:11, color:"#8a8a7e", background:"none", border:"none", cursor:"pointer", fontFamily:"'DM Mono', monospace", textDecoration:"underline", textUnderlineOffset:2 }}>
          {showConfig ? "Hide weights" : "Adjust weights"}
        </button>
      </div>

      {/* Weight sliders */}
      {showConfig && (
        <div style={{ padding:16, background:"rgba(128,128,120,0.04)", borderRadius:10, marginBottom:16 }}>
          <div style={{ fontSize:12, fontWeight:500, marginBottom:10, color:"#4a4a44" }}>Re-ranking weights (must sum to 100%)</div>
          <WeightSlider label="Semantic relevance" value={weights.relevance} onChange={v=>updateWeight("relevance",v)} color="#5a5a52" description="Standard search ranking signal" />
          <WeightSlider label="Inverse citation" value={weights.citation} onChange={v=>updateWeight("citation",v)} color="#a06830" description="Boost low-citation, recent work" />
          <WeightSlider label="Institutional diversity" value={weights.institutional} onChange={v=>updateWeight("institutional",v)} color="#1d7a5f" description="Boost non-OECD and non-university affiliations" />
          <WeightSlider label="Language diversity" value={weights.language} onChange={v=>updateWeight("language",v)} color="#5a3a8a" description="Boost non-English publications" />
          <button onClick={()=>{ if(canonical.length) search(); }}
            style={{ marginTop:8, padding:"6px 14px", fontSize:11, border:"1px solid rgba(128,128,120,0.2)", borderRadius:6, background:"white", cursor:"pointer", fontFamily:"'DM Mono', monospace" }}>
            Re-rank with new weights
          </button>
        </div>
      )}

      {error && <div style={{ padding:12, background:"rgba(200,60,60,0.06)", borderRadius:8, color:"#8a3030", fontSize:12, marginBottom:16 }}>{error}</div>}

      {/* Stats */}
      {canonical.length > 0 && <Stats canonical={canonical} periphery={periphery} />}

      {/* Dual streams */}
      {canonical.length > 0 && (
        <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16 }}>
          {/* Canonical */}
          <div>
            <div style={{ padding:"10px 16px", background:"rgba(90,90,82,0.06)", borderRadius:"10px 10px 0 0", borderBottom:"2px solid rgba(90,90,82,0.15)" }}>
              <h3 style={{ margin:0, fontSize:13, fontWeight:500, color:"#5a5a52", fontFamily:"'DM Mono', monospace" }}>CANONICAL</h3>
              <p style={{ margin:"2px 0 0", fontSize:10.5, color:"#8a8a7e" }}>Standard relevance ranking</p>
            </div>
            <div style={{ border:"1px solid rgba(128,128,120,0.1)", borderTop:"none", borderRadius:"0 0 10px 10px", overflow:"hidden" }}>
              {canonical.slice(0,15).map((w,i) => <WorkCard key={w.id} work={w} rank={i+1} stream="canonical" />)}
            </div>
          </div>
          {/* Periphery */}
          <div>
            <div style={{ padding:"10px 16px", background:"rgba(29,122,95,0.06)", borderRadius:"10px 10px 0 0", borderBottom:"2px solid rgba(29,122,95,0.2)" }}>
              <h3 style={{ margin:0, fontSize:13, fontWeight:500, color:"#1d7a5f", fontFamily:"'DM Mono', monospace" }}>PERIPHERY</h3>
              <p style={{ margin:"2px 0 0", fontSize:10.5, color:"#8a8a7e" }}>Inverse citation × institutional × language diversity</p>
            </div>
            <div style={{ border:"1px solid rgba(29,122,95,0.1)", borderTop:"none", borderRadius:"0 0 10px 10px", overflow:"hidden" }}>
              {periphery.slice(0,15).map((w,i) => <WorkCard key={w.id} work={w} rank={i+1} stream="periphery" />)}
            </div>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!searched && !loading && (
        <div style={{ textAlign:"center", padding:"48px 20px", color:"#8a8a7e" }}>
          <div style={{ fontSize:36, marginBottom:12, opacity:0.3 }}>&#x1F50D;</div>
          <p style={{ fontSize:13, lineHeight:1.6, maxWidth:400, margin:"0 auto" }}>
            Try a query above. The periphery stream will surface scholarship that standard search engines bury — work from the Global South, in non-English languages, with fewer citations but equal rigour.
          </p>
        </div>
      )}

      {/* Footer */}
      <div style={{ textAlign:"center", marginTop:32, padding:"16px 0", borderTop:"1px solid rgba(128,128,120,0.1)", fontSize:10.5, color:"#8a8a7e", fontFamily:"'DM Mono', monospace" }}>
        Aletheia MVP · Data from OpenAlex (260M+ works, fully open) · Re-ranking runs client-side · AGPL-3.0
      </div>
    </div>
  );
}
