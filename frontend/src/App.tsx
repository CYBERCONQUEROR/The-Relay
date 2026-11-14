import { useEffect, useRef, useState } from 'react'
import { Activity, ArrowUp, BookOpen, Check, ChevronRight, Circle, Clock3, Download, FlaskConical, Layers3, ListFilter, Menu, MessageSquare, Plus, Radio, RefreshCw, Search, Settings2, ShieldCheck, Square, X, Zap } from 'lucide-react'
import { api, Data, download, number, operatorToken, setToken, statusLabel, time } from './api'

type Page = 'Research workspace' | 'Live monitoring' | 'Queue management' | 'Load testing' | 'Evaluation results' | 'Settings'
const papers = [
  { title: 'Attention Is All You Need', year: '2017', label: 'Transformer', questions: ['How does multi-head attention work?', 'Why does the Transformer use positional encoding?'] },
  { title: 'Moshi', year: '2024', label: 'Speech & text', questions: ['How does Moshi support real-time speech interaction?', 'What role does the Mimi audio codec play?'] },
  { title: 'Dynamic Chunking', year: '2025', label: 'Sequence modeling', questions: ['How does dynamic chunking differ from fixed-size chunking?', 'What are the benefits of dynamic chunking?'] },
]
const stages = [['request_received','Request received'],['job_created','Job created'],['query_analysis','Query analysis'],['retrieval','Document retrieval'],['evidence_check','Evidence check'],['generation','Groq generation'],['citation_verification','Citation verification'],['verifier_correction','Verifier correction'],['final_answer','Final answer'],['terminal_status','Final status']]
const terminal = (job?: Data | null) => job && ['completed','failed','cancelled'].includes(job.status)
const eligible = (job: Data) => ['pending','waiting_for_quota','retry_scheduled'].includes(job.status)
export function Badge({status}: {status: string}) { return <span className={`badge ${status}`}><i/>{statusLabel(status)}</span> }
function Stat({label,value,hint}: {label:string,value:unknown,hint?:string}) {return <div className="stat"><span>{label}</span><strong>{typeof value === 'number' ? number(value) : value as string || 'N/A'}</strong>{hint && <small>{hint}</small>}</div>}
function Empty({children}: {children:React.ReactNode}) { return <div className="empty"><Layers3 size={25}/><p>{children}</p></div> }
function latencyHistogram(observations: Data[]) {
  const bins = [100,500,1000,2000,5000,Infinity]
  const counts = bins.map(()=>0)
  observations.forEach(o=>{if(typeof o.latency_ms==='number') counts[bins.findIndex(limit=>o.latency_ms<=limit)]++})
  return counts
}
function Chart({title, values, labels}: {title:string, values:number[],labels?:string[]}) {
  const max = Math.max(1,...values)
  return <section className="panel chart"><h3>{title}</h3>{values.length ? <><svg role="img" aria-label={title} viewBox="0 0 500 140" preserveAspectRatio="none"><line x1="0" y1="130" x2="500" y2="130" stroke="#293341"/>{values.slice(-40).map((v,i,arr)=><rect key={i} x={i*500/arr.length+2} y={130-v/max*115} width={Math.max(2,500/arr.length-5)} height={v/max*115} rx="2"><title>{labels?.[i] || i+1}: {number(v)}</title></rect>)}</svg><div className="chart-caption"><span>Observed samples · {values.length}</span><span>Max {number(max)}</span></div>{labels && <div className="chart-labels">{labels.map((label,i)=><span key={label}>{label}: {values[i]}</span>)}</div>}</> : <Empty>No observations yet</Empty>}</section>
}

export default function App() {
  const [page,setPage] = useState<Page>('Research workspace')
  const [mobile,setMobile] = useState(false)
  const [advanced,setAdvanced] = useState(true)
  const [selected,setSelected] = useState<string | null>(null)
  const [job,setJob] = useState<Data | null>(null)
  const [events,setEvents] = useState<Data[]>([])
  const [jobs,setJobs] = useState<Data[]>([])
  const [metrics,setMetrics] = useState<Data>({})
  const [health,setHealth] = useState<Data>({})
  const [experiments,setExperiments] = useState<Data>({runs:[],files:[]})
  const [error,setError] = useState('')
  const [connected,setConnected] = useState(false)
  const [lastUpdate,setLastUpdate] = useState<number>()
  const [question,setQuestion] = useState('')
  const [sending,setSending] = useState(false)
  const [poll,setPoll] = useState(Math.max(1000,Math.min(30000,Number(localStorage.getItem('relay-poll')) || 2000)))
  const [queueSamples,setQueueSamples] = useState<number[]>([])
  const [filter,setFilter] = useState('')
  const [search,setSearch] = useState('')
  const [offset,setOffset] = useState(0)
  const [total,setTotal] = useState(0)
  const [token,setTokenInput] = useState(operatorToken)
  const [refresh,setRefresh] = useState(0)
  const [form,setForm] = useState({provider:'standin',scenario:'baseline',max_requests:5,concurrency:1,max_tokens:3000,max_runtime_seconds:30})
  const [confirm,setConfirm] = useState(false)
  const [optin,setOptin] = useState(false)
  const [running,setRunning] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    let stopped = false; let timer: ReturnType<typeof setTimeout>
    async function fetchState() {
      try {
        const [m,j,h,x] = await Promise.all([api('/system/metrics'),api(`/jobs?limit=20&offset=${offset}${filter ? '&status='+filter : ''}&search=${encodeURIComponent(search)}`),api('/system/health'),api('/experiments/results')])
        if (stopped) return
        setMetrics(m); setJobs(j.items); setTotal(j.total); setHealth(h); setExperiments(x); setConnected(true); setLastUpdate(Date.now())
        setQueueSamples(v=>[...v,m.queue_depth].slice(-40))
      } catch (e) {if (!stopped) {setConnected(false); setError((e as Error).message)}}
      if (!stopped) timer = setTimeout(fetchState,poll)
    }
    fetchState()
    return () => {stopped = true; clearTimeout(timer)}
  },[poll,offset,filter,search,refresh])

  useEffect(() => {
    if (!selected) {setJob(null);setEvents([]);return}
    let stopped = false; let timer: ReturnType<typeof setTimeout>; let cursor = 0
    setJob(null);setEvents([])
    async function inspect() {
      try {
        const current = await api('/jobs/'+selected)
        let rows: Data[] = []
        while (!stopped) {
          const response = await api(`/jobs/${selected}/events?after=${cursor}`)
          rows = [...rows,...response.items]
          if (response.items.length) cursor = response.items[response.items.length-1].id
          if (response.items.length < 500) break
        }
        if (stopped) return
        setJob(current)
        setEvents(previous=>[...new Map([...previous,...rows].map(row=>[row.id,row])).values()])
      } catch (e) {if (!stopped) setError((e as Error).message)}
      if (!stopped) timer = setTimeout(inspect,poll)
    }
    inspect()
    return () => {stopped = true;clearTimeout(timer)}
  },[selected,poll,refresh])

  function navigate(next: Page) {setPage(next);setMobile(false);setError('')}
  function newConversation() {setSelected(null);setQuestion('');navigate('Research workspace');input.current?.focus()}
  async function submit() {
    if (!question.trim() || sending) return
    setSending(true);setError('')
    try {
      const created = await api('/ask',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({question:question.trim()})})
      setSelected(created.job_id);setJob(created);setQuestion('');setRefresh(v=>v+1)
    } catch (e) {setError((e as Error).message)} finally {setSending(false)}
  }
  async function cancel(item: Data) {
    try {await api(`/jobs/${item.id}/cancel`,{method:'POST'});setRefresh(v=>v+1)} catch(e) {setError((e as Error).message)}
  }
  async function runTest(dryRun: boolean) {
    setRunning(true);setError('')
    try {await api('/experiments/run',{method:'POST',body:JSON.stringify({...form,dry_run:dryRun,confirmed:!dryRun && (form.provider==='standin' || optin)})});setRefresh(v=>v+1);setConfirm(false);setOptin(false)}
    catch(e) {setError((e as Error).message)} finally {setRunning(false)}
  }
  async function save(path:string,name:string) {try {await download(path,name)} catch(e) {setError((e as Error).message)}}
  const lastCall = [...events].reverse().find(e=>e.stage==='groq_request' && e.state!=='running')?.data
  const result = job?.result
  const navItems: [Page,typeof Activity][] = [['Research workspace',MessageSquare],['Live monitoring',Activity],['Queue management',Layers3],['Load testing',FlaskConical],['Evaluation results',ShieldCheck]]

  return <div className="shell">
    <aside className={`sidebar ${mobile?'open':''}`}>
      <button className="brand" onClick={newConversation}><span className="brand-mark"><Radio size={22}/></span><span>the relay<span className="brand-sub">RESEARCH INFRASTRUCTURE</span></span></button>
      <button className="new-chat" onClick={newConversation}><Plus size={16}/> New conversation <kbd>↗</kbd></button>
      <div className="section-label">WORKSPACE</div>
      <nav>{navItems.map(([name,Icon])=><button key={name} className={page===name?'active':''} onClick={()=>navigate(name)}><Icon size={17}/>{name}{name==='Queue management' && <span className="nav-count">{metrics.queue_depth ?? '—'}</span>}</button>)}</nav>
      <div className="section-label">RESEARCH LIBRARY <span>03</span></div>
      {papers.map(p=><button className="paper-link" key={p.title} onClick={()=>{navigate('Research workspace');setQuestion(p.questions[0]);input.current?.focus()}}><BookOpen size={15}/><span>{p.title}<small>{p.label} · {p.year}</small></span></button>)}
      <div className="section-label">RECENT REQUESTS</div>
      <div className="history">{jobs.slice(0,6).map(j=><button key={j.id} onClick={()=>{setSelected(j.id);navigate('Research workspace')}}><span className={`status-dot ${j.status}`}/><span>{j.question}</span></button>)}{!jobs.length && <small>No requests yet</small>}</div>
      <div className="sidebar-bottom"><button onClick={()=>navigate('Settings')}><Settings2 size={17}/> Settings</button><div className="environment"><span className={`status-dot ${connected?'completed':'failed'}`}/><div>{connected?'Backend connected':'Backend disconnected'}<small>Persistent SQLite · Real Groq</small></div></div></div>
    </aside>
    <div className="main-shell">
      <header><div className="breadcrumb"><button className="mobile-menu icon-button" aria-label="Toggle navigation" onClick={()=>setMobile(!mobile)}><Menu size={19}/></button><span>Workspace</span><ChevronRight size={13}/><strong>{page}</strong></div><div className="header-right"><span className="provider-pill"><i/>GROQ <span>REAL PROVIDER</span></span><span className="version">v2.0</span></div></header>
      {error && <div className="error-banner" role="alert"><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={()=>setError('')}><X size={16}/></button></div>}
      {!connected && <div className="connection-note">Waiting for backend connection. Displayed data may be stale. {lastUpdate && `Last updated ${new Date(lastUpdate).toLocaleTimeString()}.`} Retrying automatically.</div>}
      <div className="page-layout">
        <main>
          {page==='Research workspace' && <>
            <div className="page-heading"><div><div className="eyebrow"><span/> EVIDENCE-GROUNDED RESEARCH</div><h1>Research workspace</h1><p>Ask better questions. Follow every step to the evidence.</p></div><button className={`toggle ${advanced?'on':''}`} onClick={()=>setAdvanced(!advanced)}><Activity size={15}/>{advanced?'Advanced observability':'Simple chat view'}</button></div>
            {!job && !selected && <div className="welcome"><div className="welcome-symbol"><Layers3 size={30}/></div><h2>Three papers. A deeper understanding.</h2><p>Explore foundational ideas with answers grounded in research,<br/> verified citations, and a fully observable pipeline.</p><div className="paper-cards">{papers.map((p,i)=><article key={p.title}><span className="paper-number">0{i+1} <BookOpen size={17}/></span><h3>{p.title}</h3><small>{p.label} / {p.year}</small><button onClick={()=>setQuestion(p.questions[0])}>{p.questions[0]} <ChevronRight size={15}/></button></article>)}</div></div>}
            {selected && !job && <Empty>Loading saved request…</Empty>}
            {job && <div className="request-view"><div className="question-card"><span className="avatar">YOU</span><div><small>RESEARCH QUESTION <span>{time(job.created)}</span></small><h2>{job.question}</h2><code>{job.id}</code></div></div>
              <section className="panel answer-card"><div className="panel-title"><span className="relay-avatar"><Radio size={17}/></span><h3>The Relay</h3><Badge status={job.status}/></div><p className="status-message">{job.message}</p>
                {job.status==='waiting_for_quota' && <div className="notice">{job.reset_estimated?'Estimated retry window':'Provider-reported retry window'}: {time(job.next_run)}. Quota recovery is unconfirmed.</div>}
                {job.status==='retry_scheduled' && <div className="notice">Temporary failure detected. Next retry: {time(job.next_run)} {job.reset_estimated?'(estimated)':''}</div>}
                {job.status==='processing' && job.attempts>1 && <div className="notice">Retry window reached. Processing your saved request; provider recovery is unconfirmed until a successful response.</div>}
                {result && terminal(job) && <><div className="answer-text">{result.answer?.split('\n\n').map((p:string,i:number)=><p key={i}>{p}</p>)}</div>{result.status && result.status!=='ok' && <Badge status={result.status}/>}<div className="citations">{result.sources?.map((s:Data,i:number)=><span key={i}><BookOpen size={13}/>{s.paper} <b>p. {s.page}</b></span>)}</div></>}
                {job.error_message && <p className="error-text">{job.error_category}: {job.error_message}</p>}
                {eligible(job) && <button className="text-button" onClick={()=>cancel(job)}><Square size={13}/>Cancel scheduled request</button>}
              </section>
              {advanced && <>
                <section className="panel"><div className="panel-title"><Activity size={17}/><h3>Request lifecycle</h3><span className="muted">Durable backend events</span></div><div className="lifecycle">{stages.map(([key,label])=>{
                  const rows=events.filter(e=>key==='terminal_status' ? ['completed','failed','cancelled','waiting_for_quota','retry_scheduled'].includes(e.stage) : e.stage===key), latest=rows.at(-1)
                  const start=[...rows].reverse().find(e=>e.state==='running')
                  const state=latest?.state || (terminal(job)?'skipped':'pending')
                  const duration=latest?.data.duration_ms ?? (latest?.state==='completed' && start ? (latest.timestamp-start.timestamp)*1000 : undefined)
                  return <div className={`stage ${state}`} key={key}><span className="stage-icon">{state==='completed'?<Check size={12}/>:state==='failed'?<X size={12}/>:<Circle size={10}/>}</span><div><strong>{label}</strong><small>{latest?time(latest.timestamp):state==='skipped'?'No stage event recorded':'Awaiting event'}</small></div><span>{statusLabel(state)}{duration!==undefined && <small>{number(duration,' ms')}</small>}</span></div>
                })}</div></section>
                <section className="panel"><div className="panel-title"><BookOpen size={17}/><h3>Retrieved evidence</h3><span className="muted">{job.metadata?.setting}</span></div>{events.filter(e=>e.stage==='retrieval' && e.state==='completed').at(-1)?.data.passages?.map((p:Data,i:number)=><details key={i}><summary><span>{i+1}. {p.paper} · p. {p.page}</span><span>Score {number(p.score)}</span></summary><p className="evidence-text">{p.text}</p></details>) || <Empty>Evidence will appear after retrieval completes.</Empty>}</section>
                <details className="panel"><summary>Operational event log <span>{events.length} events</span></summary><div className="event-list">{events.map(e=><div key={e.id}><small>#{e.id} · {time(e.timestamp)}</small><b>{statusLabel(e.stage)} / {e.state}</b><pre>{JSON.stringify(e.data,null,2)}</pre></div>)}</div></details>
              </>}
            </div>}
            <div className="composer"><textarea ref={input} aria-label="Research question" value={question} onChange={e=>setQuestion(e.target.value)} maxLength={2000} placeholder="Ask a question about the research papers…" onKeyDown={e=>{if(e.key==='Enter' && !e.shiftKey){e.preventDefault();submit()}}}/><div className="composer-footer"><span><BookOpen size={13}/> 3 research papers <i/> Page-level citations</span><button aria-label="Send question" disabled={sending || !question.trim()} onClick={submit}><ArrowUp size={18}/></button></div></div><div className="composer-note"><ShieldCheck size={12}/> Grounded in retrieved evidence. Verified before delivery. <button onClick={newConversation}>Clear conversation</button></div>
          </>}
          {page==='Queue management' && <><Title eyebrow="DURABLE REQUEST SCHEDULER" title="Queue management" text="Every request has a place. Every transition is recorded."/><div className="stats-grid">{['total','pending','processing','waiting_for_quota','retry_scheduled','completed','failed','cancelled'].map(s=><Stat key={s} label={statusLabel(s)} value={metrics[s]}/>)}</div><div className="toolbar"><div className="search"><Search size={15}/><input aria-label="Search job ID" placeholder="Search job ID" value={search} onChange={e=>{setSearch(e.target.value);setOffset(0)}}/></div><ListFilter size={16}/><select aria-label="Filter status" value={filter} onChange={e=>{setFilter(e.target.value);setOffset(0)}}><option value="">All statuses</option>{['pending','processing','waiting_for_quota','retry_scheduled','completed','failed','cancelled'].map(s=><option key={s}>{s}</option>)}</select><button onClick={()=>setRefresh(v=>v+1)}><RefreshCw size={14}/>Refresh</button></div><div className="panel table-wrap"><table><thead><tr>{['Request / question','Status','Attempts','Created','Next retry','Completed','Last error','Action'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{jobs.map(j=><tr key={j.id}><td><button className="job-link" onClick={()=>{setSelected(j.id);navigate('Research workspace')}}>{j.question}<code>{j.id.slice(0,8)}</code></button></td><td><Badge status={j.status}/></td><td>{j.attempts}</td><td>{time(j.created)}</td><td>{eligible(j)?time(j.next_run):'—'}{j.reset_estimated?' (estimated)':''}</td><td>{time(j.completed)}</td><td>{j.error_message || '—'}</td><td>{eligible(j) && <button onClick={()=>cancel(j)}>Cancel</button>}</td></tr>)}</tbody></table>{!jobs.length && <Empty>No jobs match this view.</Empty>}</div><div className="pagination"><span>{total} jobs · {offset+1}–{Math.min(offset+20,total)}</span><button disabled={offset===0} onClick={()=>setOffset(v=>Math.max(0,v-20))}>Previous</button><button disabled={offset+20>=total} onClick={()=>setOffset(v=>v+20)}>Next</button></div></>}
          {page==='Live monitoring' && <><Title eyebrow="OBSERVED SYSTEM PERFORMANCE" title="Live monitoring" text="Real provider observations. Durable queue state. No synthetic metrics."/><div className="panel health-row"><span><i className={`status-dot ${connected?'completed':'failed'}`}/>SQLite: {health.database || 'N/A'}</span><span>Worker: {health.worker || 'N/A'}</span><span>Groq: {statusLabel(health.groq?.status)}</span></div><div className="stats-grid">{[['API requests',metrics.api_requests],['Successful calls',metrics.api_successes],['Failed calls',metrics.api_failures],['Rate limits',metrics.rate_limit_count],['API retries',metrics.retry_count],['Tokens',metrics.total_tokens],['P50 latency',number(metrics.p50_latency_ms,' ms')],['P95 latency',number(metrics.p95_latency_ms,' ms')]].map(([l,v])=><Stat key={l} label={l} value={v}/>)}</div><div className="chart-grid"><Chart title="Groq success (1) / failure (0) over time" values={(metrics.observations||[]).map((c:Data)=>c.success?1:0)}/><Chart title="Groq latency distribution · request count" values={metrics.observations?.length ? latencyHistogram(metrics.observations) : []} labels={['≤100 ms','100–500 ms','0.5–1 s','1–2 s','2–5 s','>5 s']}/><Chart title="Queue depth over time · this session" values={queueSamples}/><Chart title="Groq token usage over time" values={(metrics.observations||[]).map((c:Data)=>c.total_tokens).filter((v:unknown)=>typeof v==='number')}/><Chart title="Job status distribution" values={['pending','processing','waiting_for_quota','retry_scheduled','completed','failed','cancelled'].map(s=>metrics[s]||0)} labels={['pending','processing','quota wait','retry','completed','failed','cancelled']}/></div><section className="panel simulation"><div className="panel-title"><FlaskConical size={18}/><h3>Local stand-in simulation</h3><Badge status={health.standin?.status || 'unavailable'}/></div><p>Simulated data · separate from real Groq observations</p>{health.standin?.metrics ? <div className="stats-grid">{['total_requests','active_requests','queued_requests','total_failures'].map(s=><Stat key={s} label={statusLabel(s)} value={health.standin.metrics[s]}/>)}</div>:<Empty>Stand-in unavailable. Start the simulator on port 8002.</Empty>}</section></>}
          {page==='Load testing' && <><Title eyebrow="BOUNDED RELIABILITY EXPERIMENTS" title="Load testing" text="Define a budget. Observe the provider. Keep every result."/><section className="panel"><div className="panel-title"><FlaskConical size={17}/><h3>Configure experiment</h3><span className="muted">Non-streaming · TTFT unavailable</span></div><div className="form-grid"><label>Provider<select aria-label="Provider" value={form.provider} onChange={e=>setForm({...form,provider:e.target.value})}><option value="standin">Local stand-in · SIMULATED</option><option value="groq">Real Groq · USES QUOTA</option></select></label><label>Scenario<select aria-label="Scenario" value={form.scenario} onChange={e=>setForm({...form,scenario:e.target.value})}>{['baseline','low_concurrency','moderate_concurrency','rate_limit_observation','recovery'].map(s=><option key={s}>{s}</option>)}</select></label>{[['max_requests','Maximum requests'],['concurrency','Maximum concurrency'],['max_tokens','Token budget'],['max_runtime_seconds','Runtime limit (seconds)']].map(([key,label])=><label key={key}>{label}<input type="number" min="1" value={form[key as keyof typeof form]} onChange={e=>setForm({...form,[key]:Number(e.target.value)})}/></label>)}</div><div className="notice">{form.provider==='groq'?'Real Groq consumes your API quota. The backend must enable live testing. Daily quota exhaustion stops the run; tests never resume automatically.':'Stand-in responses and quota behavior are simulated. These measurements do not demonstrate real Groq recovery.'}</div><div className="actions"><button disabled={running} onClick={()=>runTest(true)}>Validate dry run</button><button className="primary" disabled={running} onClick={()=>setConfirm(true)}><FlaskConical size={15}/>Review & run</button></div></section><h2 className="subheading">Experiment history</h2>{experiments.runs.map((run:Data)=><section className="panel run-card" key={run.id}><div className="panel-title"><h3>{statusLabel(run.config.scenario)}</h3><span className={`provider-tag ${run.config.provider}`}>{run.config.provider==='groq'?'REAL GROQ':'SIMULATED STAND-IN'}</span><Badge status={run.status}/></div><small>{run.id} · {time(run.created)}</small><p>{run.rows.length} / {run.config.max_requests} HTTP attempts · {run.config.max_tokens} token budget · {run.config.max_runtime_seconds}s cap</p><div className="stats-grid"><Stat label="Success" value={run.summary?.success_percent===undefined?'N/A':number(run.summary.success_percent,'%')}/><Stat label="P95 latency" value={number(run.summary?.p95_latency_ms,' ms')}/><Stat label="Observed tokens" value={run.summary?.total_tokens_consumed}/></div>{run.error && <p className="muted">Stop reason: {run.error}</p>}<div className="actions">{run.status==='running' && <button onClick={async()=>{try{await api(`/experiments/${run.id}/stop`,{method:'POST'});setRefresh(v=>v+1)}catch(e){setError((e as Error).message)}}}><Square size={13}/>Stop future requests</button>}<button onClick={()=>save(`/experiments/${run.id}/csv`,`${run.config.provider}-${run.id}.csv`)}><Download size={14}/>Results CSV</button><button onClick={()=>save(`/experiments/${run.id}/csv?summary=true`,`${run.config.provider}-summary.csv`)}>Summary CSV</button></div></section>)}{!experiments.runs.length && <Empty>No experiments yet. Start with a dry run.</Empty>}</>}
          {page==='Evaluation results' && <><Title eyebrow="REPRODUCIBLE RESEARCH" title="Evaluation results" text="Download existing benchmark artifacts. Provider datasets stay separate."/><div className="panel table-wrap"><table><thead><tr><th>Artifact</th><th>Source</th><th>Download</th></tr></thead><tbody>{experiments.files.map((f:Data)=><tr key={f.filename}><td>{f.filename}</td><td><span className={`provider-tag ${f.provider}`}>{f.provider==='standin'?'SIMULATED STAND-IN':f.provider==='groq'?'REAL GROQ':'RAG EVALUATION'}</span></td><td><button onClick={()=>save(f.url,f.filename)} aria-label={`Download ${f.filename}`}><Download size={15}/></button></td></tr>)}</tbody></table></div></>}
          {page==='Settings' && <><Title eyebrow="WORKSPACE CONFIGURATION" title="Settings" text="Connect to your operator workspace and choose how often it refreshes."/><section className="panel settings"><label>Operator access token<input aria-label="Operator access token" type="password" value={token} autoComplete="off" onChange={e=>setTokenInput(e.target.value)}/></label><p>Required when RELAY_ADMIN_TOKEN is configured on the backend. Stored only for this browser session. The Groq key stays on the server.</p><label>Refresh interval (milliseconds)<input aria-label="Refresh interval" type="number" min={1000} max={30000} step={1000} value={poll} onChange={e=>{const n=Math.max(1000,Math.min(30000,Number(e.target.value)||2000));setPoll(n);localStorage.setItem('relay-poll',String(n))}}/></label><button className="primary" onClick={()=>{setToken(token);setRefresh(v=>v+1)}}>Save connection</button><div className="notice">This is a single-operator workspace. Shared deployments require a server access token. Multi-user accounts and ownership are not implemented.</div><h3>Status legend</h3><div className="legend">{['pending','processing','waiting_for_quota','retry_scheduled','completed','failed','cancelled'].map(s=><Badge key={s} status={s}/>)}</div></section></>}
        </main>
        {page==='Research workspace' && advanced && <aside className="inspector"><div className="inspector-heading"><Activity size={15}/><h3>Live inspector</h3><span className="live-dot"/></div><div className="inspector-block"><div className="section-label">REQUEST STATUS</div>{job?<><Badge status={job.status}/><dl><dt>Job ID</dt><dd><code>{job.id.slice(0,8)}</code></dd><dt>Created</dt><dd>{time(job.created)}</dd><dt>Processing attempt</dt><dd>{job.attempts}</dd><dt>Next retry</dt><dd>{eligible(job)?time(job.next_run):'—'}</dd></dl></>:<p className="muted">Submit a question to start tracing.</p>}</div><div className="inspector-block"><div className="section-label">GROQ PROVIDER</div><div className="provider-state"><Zap size={16}/><span>{health.groq?.status==='last_observed_success'?'Last call successful':statusLabel(health.groq?.status)}</span></div><dl><dt>HTTP status</dt><dd>{lastCall?.http_status ?? 'N/A'}</dd><dt>Category</dt><dd>{lastCall?.category || job?.error_category || '—'}</dd><dt>Retry-After</dt><dd>{lastCall?.rate_limit?.retry_after ?? lastCall?.rate_limit?.['retry-after'] ?? 'N/A'}</dd><dt>Reset</dt><dd>{job?.reset_estimated?'Estimated retry':lastCall?.rate_limit?.reset_at ? time(lastCall.rate_limit.reset_at) : 'Reset time unavailable'}</dd></dl></div><div className="inspector-block"><div className="section-label">PIPELINE METRICS</div><dl>{[['Input tokens',result?.metrics?.tokens_in],['Output tokens',result?.metrics?.tokens_out],['API attempts',result?.metrics?.api_attempts],['API retries',result?.metrics?.api_retries],['Correction retries',result?.metrics?.retries],['Total latency',number(result?.metrics?.total_ms,' ms')],['Retrieval',job?.metadata?.setting || 'R4']].map(([l,v])=><div className="dl-row" key={l}><dt>{l}</dt><dd>{v ?? 'N/A'}</dd></div>)}</dl></div><div className="inspector-block"><div className="section-label">PERSISTENT QUEUE</div><dl><dt>Waiting</dt><dd>{metrics.queue_depth ?? 'N/A'}</dd><dt>Active workers</dt><dd>{metrics.active_workers ?? 'N/A'}</dd><dt>Worker status</dt><dd>{metrics.worker_status || 'N/A'}</dd></dl><button className="text-button" onClick={()=>navigate('Queue management')}>View queue <ChevronRight size={13}/></button></div><div className="trust-note"><ShieldCheck size={18}/><strong>Evidence, then answers.</strong><p>Page-level citations and verification protect the integrity of every response.</p></div></aside>}
      </div>
      <footer><span><span className={`status-dot ${connected?'completed':'failed'}`}/>{connected?'Connected':'Reconnecting'} · {lastUpdate?`Updated ${new Date(lastUpdate).toLocaleTimeString()}`:'Awaiting observations'}</span><span>THE RELAY / RESEARCH + RELIABILITY</span></footer>
    </div>
    {confirm && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="confirmation-title"><FlaskConical size={25}/><h2 id="confirmation-title">Confirm {form.provider==='groq'?'real Groq':'simulated'} test</h2><p>Up to {form.max_requests} requests · concurrency {form.concurrency} · {form.max_tokens} reserved tokens · {form.max_runtime_seconds} seconds.</p><p>{form.provider==='groq'?'This sends requests to Groq and consumes real quota. Backend safety caps apply. Already active calls may finish after you stop.':'This sends requests to the local stand-in only. Results are synthetic and clearly labeled.'}</p>{form.provider==='groq' && <label className="checkbox"><input type="checkbox" checked={optin} onChange={e=>setOptin(e.target.checked)}/>I explicitly approve spending this Groq test budget.</label>}<div className="actions"><button onClick={()=>setConfirm(false)}>Cancel</button><button className="primary" disabled={running || (form.provider==='groq'&&!optin)} onClick={()=>runTest(false)}>Confirm & start</button></div></section></div>}
  </div>
}
function Title({eyebrow,title,text}: {eyebrow:string,title:string,text:string}) {return <div className="page-heading"><div><div className="eyebrow"><span/>{eyebrow}</div><h1>{title}</h1><p>{text}</p></div></div>}
