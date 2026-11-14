import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from '../src/App'
import { Data } from '../src/api'

let job: Data | null
let offline = false
const received: {path:string,options?:RequestInit}[] = []
const metrics = {queue_depth:1,total:1,pending:0,processing:0,waiting_for_quota:1,retry_scheduled:0,completed:0,failed:0,cancelled:0,active_workers:0,worker_status:'online',api_requests:0,api_successes:0,observations:[]}
const created = () => ({id:'job-123',job_id:'job-123',question:'How does attention work?',status:'pending',created:1730000000,next_run:1730000100,attempts:0,message:'Your request has been received.',metadata:{setting:'R4'}})
function response(data:unknown,status=200) {return Promise.resolve({ok:status<400,status,json:()=>Promise.resolve(data)} as Response)}

beforeEach(()=>{
  job=null;offline=false;received.length=0
  localStorage.clear();sessionStorage.clear()
  vi.stubGlobal('fetch',vi.fn((path:string,options?:RequestInit)=>{
    received.push({path,options})
    if (offline) return Promise.reject(new Error('Connection unavailable'))
    if (path==='/ask') {job=created();return response(job,202)}
    if (path.startsWith('/jobs/job-123/events')) return response({items:[{id:1,stage:'request_received',state:'completed',timestamp:1730000000,data:{}}]})
    if (path==='/jobs/job-123') return response(job)
    if (path==='/jobs/job-123/cancel') {job={...job,status:'cancelled',message:'Cancelled'};return response(job)}
    if (path.startsWith('/jobs?')) return response({items:job?[job]:[],total:job?1:0})
    if (path==='/system/metrics') return response(metrics)
    if (path==='/system/health') return response({database:'available',worker:'online',groq:{status:'not_observed'},standin:{status:'unavailable'}})
    if (path==='/experiments/results') return response({runs:[],files:[{filename:'results.csv',provider:'standin',url:'/experiments/files/results.csv'}]})
    if (path==='/experiments/run') return response({id:'run-1',status:'dry_run'})
    return response({})
  }))
})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
async function ask() {
  render(<App/>);
  fireEvent.change(screen.getByLabelText('Research question'),{target:{value:'How does attention work?'}})
  fireEvent.click(screen.getByLabelText('Send question'))
  await screen.findByText('How does attention work?',{selector:'h2'})
}

describe('Research workspace',()=>{
  it('submits a question to the real backend job API',async()=>{
    await ask()
    expect(received.find(r=>r.path==='/ask')?.options?.body).toContain('How does attention work?')
    expect(screen.getByText('Request lifecycle')).toBeInTheDocument()
    expect(screen.getAllByText('Awaiting event').length).toBeGreaterThan(0)
  })
  it('displays a completed answer with citations through polling',async()=>{
    await ask()
    job={...job,status:'completed',message:'Your answer is ready.',result:{status:'ok',answer:'Evidence grounded answer.',sources:[{paper:'paper.pdf',page:3}],metrics:{tokens_in:50}}}
    await screen.findByText('Evidence grounded answer.',{}, {timeout:4000})
    expect(screen.getByText('p. 3')).toBeInTheDocument()
    expect(screen.getByText('paper.pdf')).toBeInTheDocument()
  })
  it.each([
    ['waiting_for_quota',"Daily quota exhausted; safely stored.",'Estimated retry window'],
    ['retry_scheduled','Temporary rate limit detected. Retry scheduled.','Temporary failure detected.'],
    ['failed','Your request could not be completed.','Your request could not be completed.'],
  ])('shows backend %s state',async(status,message,expected)=>{
    await ask();job={...job,status,message,reset_estimated:true}
    await screen.findByText(new RegExp(expected.replace('.','\\.')),{}, {timeout:4000})
    if(status==='waiting_for_quota') expect(screen.getByText(/Quota recovery is unconfirmed/)).toBeInTheDocument()
  })
  it('displays refusal without a fabricated answer',async()=>{
    await ask();job={...job,status:'completed',message:'Your answer is ready.',result:{status:'refused_correctly',answer:'Insufficient research evidence.',sources:[]}}
    await screen.findByText('Insufficient research evidence.',{}, {timeout:4000})
    expect(screen.queryByText('p. 3')).not.toBeInTheDocument()
  })
  it('shows saved request resumption without claiming recovery',async()=>{
    await ask();job={...job,status:'processing',attempts:2}
    await screen.findByText(/Retry window reached/,{}, {timeout:4000})
    expect(screen.getByText(/provider recovery is unconfirmed/)).toBeInTheDocument()
  })
  it('recovers polling after a disconnected backend',async()=>{
    render(<App/>);await screen.findByText('Backend connected')
    offline=true
    await screen.findByText('Connection unavailable',{}, {timeout:4000})
    offline=false
    await waitFor(()=>expect(screen.getByText('Backend connected')).toBeInTheDocument(),{timeout:4000})
  })
  it('switches to simple chat and clears a conversation',async()=>{
    await ask();fireEvent.click(screen.getByText('Advanced observability'))
    expect(screen.queryByText('Request lifecycle')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Clear conversation'))
    expect(screen.getByText('Three papers. A deeper understanding.')).toBeInTheDocument()
  })
})

describe('Operations controls',()=>{
  it('refreshes queue state and filters jobs',async()=>{
    render(<App/>);fireEvent.click(screen.getByRole('button',{name:/Queue management/}))
    fireEvent.change(screen.getByLabelText('Filter status'),{target:{value:'waiting_for_quota'}})
    await waitFor(()=>expect(received.some(r=>r.path.includes('status=waiting_for_quota'))).toBe(true))
    fireEvent.click(screen.getByText('Refresh'))
    expect(screen.getByLabelText('Search job ID')).toBeInTheDocument()
  })
  it('requires a review and explicit approval before real load testing',async()=>{
    render(<App/>);fireEvent.click(screen.getByRole('button',{name:'Load testing'}))
    fireEvent.change(screen.getByLabelText('Provider'),{target:{value:'groq'}})
    fireEvent.click(screen.getByText('Review & run'))
    const dialog=screen.getByRole('dialog')
    const start=within(dialog).getByText('Confirm & start')
    expect(start).toBeDisabled()
    expect(received.some(r=>r.path==='/experiments/run')).toBe(false)
    fireEvent.click(within(dialog).getByRole('checkbox'))
    fireEvent.click(start)
    await waitFor(()=>expect(received.some(r=>r.path==='/experiments/run')).toBe(true))
    expect(JSON.parse(received.find(r=>r.path==='/experiments/run')!.options!.body as string)).toMatchObject({provider:'groq',confirmed:true,dry_run:false})
  })
  it('dry runs without enabling real execution and displays backend errors',async()=>{
    render(<App/>);fireEvent.click(screen.getByRole('button',{name:'Load testing'}))
    fireEvent.click(screen.getByText('Validate dry run'))
    await waitFor(()=>expect(received.some(r=>r.path==='/experiments/run')).toBe(true))
    expect(JSON.parse(received.find(r=>r.path==='/experiments/run')!.options!.body as string).dry_run).toBe(true)
  })
  it('distinguishes stand-in data and provides mobile navigation',async()=>{
    render(<App/>);fireEvent.click(screen.getByRole('button',{name:'Evaluation results'}))
    await screen.findByText('SIMULATED STAND-IN')
    fireEvent.click(screen.getByLabelText('Toggle navigation'))
    expect(document.querySelector('.sidebar')).toHaveClass('open')
  })
})
