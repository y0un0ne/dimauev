import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api, type CredentialName, type EvaluationDateStatus, type ExtractionDateStatus, type GeminiAccount, type Worker } from './api'
import './App.css'

const today = new Date().toISOString().slice(0, 10)
const month = today.slice(0, 7)

function statusLabel(worker: Worker) {
  const pipeline = worker.status?.status
  if (worker.state === 'failed' || worker.state === 'stopped') return worker.state
  return typeof pipeline === 'string' && worker.state !== 'running' ? pipeline : worker.state
}

function StatusBadge({ state }: { state: string }) {
  return <span className={`status status-${state.replace('_', '-')}`}>{state.replace('_', ' ')}</span>
}

function Shell() {
  const workers = useQuery({ queryKey: ['workers'], queryFn: api.workers, refetchInterval: 2000 })
  const running = workers.data?.filter((worker) => worker.state === 'running').length ?? 0
  return (
    <div className="app-shell">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <span className="brand-mark">DA</span>
          <span><strong>Dimensions</strong><small>Auto Evaluate</small></span>
        </NavLink>
        <nav>
          <NavLink to="/">Overview</NavLink>
          <NavLink to="/extract">Extract</NavLink>
          <NavLink to="/evaluate">Evaluate</NavLink>
          <NavLink to="/reports">Reports</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="run-indicator"><i /> {running} active</div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/extract" element={<ExtractPage />} />
          <Route path="/evaluate" element={<EvaluatePage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/logs/:id" element={<Dashboard />} />
        </Routes>
      </main>
    </div>
  )
}

function PageHeading({ eyebrow, title, text }: { eyebrow: string; title: string; text: string }) {
  return <header className="page-heading"><span>{eyebrow}</span><h1>{title}</h1><p>{text}</p></header>
}

function Dashboard() {
  const workers = useQuery({ queryKey: ['workers'], queryFn: api.workers, refetchInterval: 2000 })
  const providers = useQuery({ queryKey: ['providers'], queryFn: api.providers, refetchInterval: 15000 })
  const hidden = useQuery({ queryKey: ['hidden-history'], queryFn: api.hiddenHistory })
  const client = useQueryClient()
  const restore = useMutation({
    mutationFn: api.restoreHistory,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['workers'] })
      client.invalidateQueries({ queryKey: ['hidden-history'] })
    },
  })
  const [logWorker, setLogWorker] = useState<Worker | null>(null)
  return <>
    <PageHeading eyebrow="Control room" title="Independent workers, one clear view." text="Extraction keeps moving even when AI quotas do not. Start each operation when its data and providers are ready." />
    <section className="metric-strip">
      <Metric label="Workers active" value={workers.data?.filter((x) => x.state === 'running').length ?? 0} />
      <Metric label="Runs recorded" value={workers.data?.length ?? 0} />
      <Metric label="Cloud providers" value={Object.values(providers.data?.configured ?? {}).filter(Boolean).length} />
      <Metric label="Ollama" value={providers.data?.ollama.online ? 'online' : 'offline'} />
    </section>
    <section className="section-head"><div><span>Operations</span><h2>Worker activity</h2></div><div className="history-actions">{(hidden.data?.hidden.length ?? 0) > 0 && <button className="text-button" disabled={restore.isPending} onClick={() => restore.mutate()}>Restore hidden history ({hidden.data?.hidden.length})</button>}<p>Refreshes every two seconds</p></div></section>
    <div className="worker-grid">
      {workers.isLoading && <Empty text="Loading workers..." />}
      {!workers.isLoading && !workers.data?.length && <Empty text="No workers launched in this server session." />}
      {workers.data?.map((worker) => <WorkerCard key={worker.id} worker={worker} onLog={() => setLogWorker(worker)} />)}
    </div>
    <section className="provider-panel">
      <div><span className="section-kicker">Provider readiness</span><h2>Available capacity</h2></div>
      <div className="provider-list">
        {Object.entries(providers.data?.configured ?? {}).map(([name, ready]) => <div key={name}><i className={ready ? 'ready' : ''} /><span>{name}</span><b>{ready ? 'configured' : 'not configured'}</b></div>)}
        <div><i className={providers.data?.ollama.online ? 'ready' : ''} /><span>ollama</span><b>{providers.data?.ollama.online ? `${providers.data.ollama.models.length} models` : 'offline'}</b></div>
      </div>
    </section>
    {logWorker && <LogModal worker={logWorker} onClose={() => setLogWorker(null)} />}
  </>
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div><strong>{value}</strong><span>{label}</span></div>
}

function Empty({ text }: { text: string }) { return <div className="empty">{text}</div> }

function WorkerCard({ worker, onLog }: { worker: Worker; onLog: () => void }) {
  const client = useQueryClient()
  const stop = useMutation({ mutationFn: () => api.stop(worker.id), onSuccess: () => client.invalidateQueries({ queryKey: ['workers'] }) })
  const proceed = useMutation({ mutationFn: () => api.continue(worker.id), onSuccess: () => client.invalidateQueries({ queryKey: ['workers'] }) })
  const hide = useMutation({
    mutationFn: () => api.hideHistory(worker.id),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['workers'] })
      client.invalidateQueries({ queryKey: ['hidden-history'] })
    },
  })
  const waiting = worker.control?.phase === 'waiting_login' || worker.control?.phase === 'waiting_close'
  const continueLabel = worker.control?.phase === 'waiting_login' ? 'Continue after login' : 'Close extraction browser'
  const progress = worker.progress
  const failure = typeof worker.status?.fatal_error === 'string' ? worker.status.fatal_error : ''
  const canHide = worker.state !== 'running'
  const confirmHide = () => {
    if (window.confirm('Hide this worker history from Overview? Logs, results, reports, and pipeline data will remain untouched. You can restore hidden history later.')) {
      hide.mutate()
    }
  }
  return <article className="worker-card">
    <header><div className={`worker-icon icon-${worker.kind}`}>{worker.kind.slice(0, 1).toUpperCase()}</div><div className="card-actions"><StatusBadge state={statusLabel(worker)} />{canHide && <button className="icon-button" disabled={hide.isPending} title="Hide worker history" aria-label="Hide worker history" onClick={confirmHide}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-9 0 1 13h10l1-13M10 11v5m4-5v5" /></svg></button>}</div></header>
    <span className="worker-type">MVP {worker.kind === 'extract' ? '1' : worker.kind === 'evaluate' ? '2' : '3'}</span>
    <h3>{worker.kind}</h3>
    <p className="scope">{worker.scope.replace('_to_', ' to ')}</p>
    {progress && <p className="worker-progress">{progress.current ?? 0} / {progress.total ?? 0} evaluated · {progress.errors ?? 0} errors{progress.phase === 'provider_request' && <span>Waiting for {progress.credential_label || progress.model || 'provider'} · timeout {progress.timeout_seconds ?? '?'}s</span>}</p>}
    {failure && <p className="worker-failure">{failure}</p>}
    {waiting && <div className="handoff"><strong>Action needed</strong><p>{worker.control?.prompt}</p><button className="button attention" disabled={proceed.isPending} onClick={() => proceed.mutate()}>{proceed.isPending ? 'Continuing...' : continueLabel}</button></div>}
    <dl><div><dt>Started</dt><dd>{new Date(worker.started_at).toLocaleString()}</dd></div><div><dt>Exit code</dt><dd>{worker.returncode ?? '—'}</dd></div></dl>
    <footer><button className="button ghost" onClick={onLog}>View log</button>{worker.state === 'running' && <button className="button danger" disabled={stop.isPending} onClick={() => stop.mutate()}>Stop</button>}</footer>
  </article>
}

function DateScopeFields({ range, setRange, date, setDate, start, setStart, end, setEnd }: any) {
  return <>
    <div className="segmented"><button type="button" className={!range ? 'active' : ''} onClick={() => setRange(false)}>Single date</button><button type="button" className={range ? 'active' : ''} onClick={() => setRange(true)}>Date range</button></div>
    {!range ? <label>Date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label> : <div className="field-row"><label>Start date<input type="date" value={start} onChange={(e) => setStart(e.target.value)} required /></label><label>End date<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} required /></label></div>}
  </>
}

function ExtractPage() {
  const dependencies = useQuery({ queryKey: ['dependencies'], queryFn: api.dependencies })
  const [range, setRange] = useState(false), [date, setDate] = useState(today), [start, setStart] = useState(today), [end, setEnd] = useState(today)
  const [chosenMode, setChosenMode] = useState<'normal' | 'rescan' | 'retry_missing'>('normal')
  const [modeTouched, setModeTouched] = useState(false)
  const [continueError, setContinueError] = useState(false)
  const [coverage, setCoverage] = useState(0.9), [missing, setMissing] = useState(25)
  const [ledgerMonth, setLedgerMonth] = useState<string | undefined>(undefined)
  const [ledgerFilter, setLedgerFilter] = useState('all')
  const ledger = useQuery({ queryKey: ['extraction-status', ledgerMonth], queryFn: () => api.extractionStatus(ledgerMonth), refetchInterval: 5000 })
  const selectedStatus = useQuery({ queryKey: ['extraction-date-status', date], queryFn: () => api.extractionDateStatus(date), enabled: !range && !!date, refetchInterval: 5000 })
  const recommendedMode = recommendedScanMode(selectedStatus.data, date)
  const scanMode = range || modeTouched ? chosenMode : recommendedMode
  const launch = useLaunch()
  const submit = (e: FormEvent) => { e.preventDefault(); launch.mutate({ kind: 'extract', payload: { ...(range ? { start_date: start, end_date: end } : { date }), scan_mode: scanMode, continue_on_error: continueError, min_detail_coverage: coverage, max_missing_details: missing } }) }
  const changeDate = (value: string) => { setDate(value); setModeTouched(false); launch.reset() }
  const chooseDate = (item: ExtractionDateStatus) => {
    setRange(false)
    setDate(item.date)
    setChosenMode(item.status === 'accepted_with_gaps' ? 'retry_missing' : item.status === 'complete' ? 'rescan' : 'normal')
    setModeTouched(true)
    launch.reset()
    window.setTimeout(() => document.getElementById('extract-form')?.scrollIntoView({ behavior: 'smooth' }), 0)
  }
  const scanWarning = extractionLaunchWarning(selectedStatus.data, scanMode, range)
  const unavailable = dependencyMessage(dependencies.data?.extract) || scanWarning.blocking
  return <><PageHeading eyebrow="MVP 1" title="Capture the source record." text="Open one browser session, verify each date, and leave the evaluator free to work elsewhere." /><ExtractionLedger data={ledger.data} loading={ledger.isLoading} selectedMonth={ledgerMonth} onMonth={setLedgerMonth} filter={ledgerFilter} onFilter={setLedgerFilter} onChoose={chooseDate} /><div id="extract-form"><FormPanel title="New extraction" note={scanWarning.preview} onSubmit={submit} launch={launch} unavailable={unavailable}><DateScopeFields {...{range,setRange,date,start,setStart,end,setEnd,setDate:changeDate}} /><label>Scan behavior<select value={scanMode} onChange={(event) => { setChosenMode(event.target.value as typeof chosenMode); setModeTouched(true); launch.reset() }}><option value="normal">Normal scan</option><option value="rescan">Rescan current Dimensions snapshot</option><option value="retry_missing">Retry missing details only</option></select></label>{!range && selectedStatus.data && <div className="scan-preview"><strong>{selectedStatus.data.date} · {selectedStatus.data.status.replaceAll('_',' ')}</strong><span>{selectedStatus.data.active_details_fetched} / {selectedStatus.data.active_snapshot_count} details · {selectedStatus.data.missing_details} missing</span><p>{scanWarning.preview}</p></div>}<div className="field-row"><label>Minimum coverage<input type="number" step="0.01" min="0.01" max="1" value={coverage} onChange={(e) => setCoverage(+e.target.value)} /></label><label>Missing detail cap<input type="number" min="-1" value={missing} onChange={(e) => setMissing(+e.target.value)} /></label></div><Checks values={[[continueError,setContinueError,'Continue after date error']]} /></FormPanel></div></>
}

function recommendedScanMode(status: ExtractionDateStatus | undefined, selectedDate: string): 'normal' | 'rescan' | 'retry_missing' {
  if (status?.status === 'accepted_with_gaps') return 'retry_missing'
  if (status?.status === 'complete' && selectedDate === today) return 'rescan'
  return 'normal'
}

function extractionLaunchWarning(status: ExtractionDateStatus | undefined, mode: string, range: boolean) {
  if (range) return { preview: mode === 'rescan' ? 'Every selected date will refresh its live Dimensions snapshot.' : mode === 'retry_missing' ? 'Only dates with unresolved missing details will open in Dimensions.' : 'Accepted dates will be skipped; unfinished dates will be processed.', blocking: '' }
  if (!status) return { preview: 'Checking local MVP 1 status...', blocking: '' }
  if (mode === 'rescan') return { preview: 'Browser will open and refresh the current Dimensions snapshot. Existing conversation details are reused; new IDs are fetched.', blocking: '' }
  if (mode === 'retry_missing') return status.missing_details > 0 ? { preview: `Browser will retry ${status.missing_details} unresolved conversation detail(s).`, blocking: '' } : { preview: 'No unresolved missing details exist for this date.', blocking: 'This date has no missing details. Choose Normal scan or Rescan.' }
  if (status.status === 'complete' || status.status === 'accepted_with_gaps') return { preview: 'No browser will open because this date already has an accepted snapshot.', blocking: 'This date is already complete. Choose Rescan to refresh current Dimensions data.' }
  return { preview: 'Browser will open and create or resume the local snapshot.', blocking: '' }
}

function ExtractionLedger({ data, loading, selectedMonth, onMonth, filter, onFilter, onChoose }: any) {
  const monthValue = selectedMonth ?? data?.month ?? ''
  const visible = (data?.dates ?? []).filter((item: ExtractionDateStatus) => {
    if (filter === 'done') return item.status === 'complete' || item.status === 'accepted_with_gaps'
    if (filter === 'attention') return ['accepted_with_gaps','in_progress','incomplete','damaged'].includes(item.status)
    if (filter === 'not_scanned') return item.status === 'not_scanned'
    return true
  })
  return <section className="scan-ledger">
    <header><div><span>Local output audit</span><h2>{data?.month ? `${data.month} scan ledger` : 'MVP 1 scan ledger'}</h2><p>Latest month with local MVP 1 output is selected automatically.</p></div><div className="month-control"><label>Month<input type="month" value={monthValue} onChange={(event) => onMonth(event.target.value)} /></label><button type="button" onClick={() => onMonth(undefined)}>Latest local</button></div></header>
    <div className="ledger-summary">
      <Metric label="Complete" value={data?.counts.complete ?? 0} />
      <Metric label="With gaps" value={data?.counts.accepted_with_gaps ?? 0} />
      <Metric label="Needs attention" value={(data?.counts.in_progress ?? 0)+(data?.counts.incomplete ?? 0)+(data?.counts.damaged ?? 0)} />
      <Metric label="Not scanned" value={data?.counts.not_scanned ?? 0} />
    </div>
    <div className="ledger-toolbar">{[['all','All'],['attention','Needs attention'],['done','Done'],['not_scanned','Not scanned']].map(([value,label]) => <button type="button" key={value} className={filter===value?'active':''} onClick={() => onFilter(value)}>{label}</button>)}</div>
    <div className="ledger-table"><div className="ledger-head"><span>Date</span><span>Status</span><span>Details</span><span>Issues</span><span /></div>{loading && <Empty text="Reading local MVP 1 output..." />}{visible.map((item: ExtractionDateStatus) => <button type="button" className="ledger-row" key={item.date} onClick={() => onChoose(item)}><span className="ledger-date">{item.date}</span><StatusBadge state={item.status} /><span>{item.output_exists ? `${item.active_details_fetched} / ${item.active_snapshot_count}` : '—'}</span><span>{ledgerIssue(item)}</span><span className="row-action">Use date →</span></button>)}</div>
  </section>
}

function ledgerIssue(item: ExtractionDateStatus) {
  if (item.status === 'not_scanned') return 'No local output'
  if (item.missing_details) return `${item.missing_details} missing`
  if (item.db_errors) return `${item.db_errors} DB errors`
  if (item.click_failures) return `${item.click_failures} click failures`
  return item.status === 'complete' ? 'None' : item.message
}

function EvaluatePage() {
  const dependencies = useQuery({ queryKey: ['dependencies'], queryFn: api.dependencies })
  const dates = useQuery({ queryKey: ['dates'], queryFn: api.dates })
  const [range, setRange] = useState(false), [date, setDate] = useState(dates.data?.[0]?.date ?? today), [start, setStart] = useState(today), [end, setEnd] = useState(today)
  const [mode, setMode] = useState('normal'), [delay, setDelay] = useState(4.2), [limit, setLimit] = useState(0), [requestTimeout, setRequestTimeout] = useState(90), [maxImages, setMaxImages] = useState(12), [thinking, setThinking] = useState('low')
  const [advanced, setAdvanced] = useState(false), [textModels, setTextModels] = useState(''), [visionModels, setVisionModels] = useState('')
  const [ledgerMonth, setLedgerMonth] = useState<string | undefined>(undefined), [ledgerFilter, setLedgerFilter] = useState('all')
  const [selectedStatus, setSelectedStatus] = useState<EvaluationDateStatus | null>(null)
  const ledger = useQuery({ queryKey: ['evaluation-status', ledgerMonth], queryFn: () => api.evaluationStatus(ledgerMonth), refetchInterval: 5000 })
  const launch = useLaunch()
  const submit = (e: FormEvent) => { e.preventDefault(); launch.mutate({ kind: 'evaluate', payload: { ...(range ? { start_date: start, end_date: end } : { date }), mode, delay, limit, request_timeout: requestTimeout, max_images: maxImages, thinking_level: thinking, ...(textModels ? { text_models: textModels } : {}), ...(visionModels ? { vision_models: visionModels } : {}) } }) }
  const chooseDate = (item: EvaluationDateStatus) => { setRange(false); setDate(item.date); setMode(item.errors ? 'retry_errors' : 'normal'); setSelectedStatus(item) }
  return <><PageHeading eyebrow="MVP 2" title="Evaluate without holding extraction." text="Run against any available date. Quota stops here; MVP 1 continues independently." /><EvaluationLedger data={ledger.data} loading={ledger.isLoading} selectedMonth={ledgerMonth} onMonth={setLedgerMonth} filter={ledgerFilter} onFilter={setLedgerFilter} onChoose={chooseDate} />{selectedStatus && <EvaluationDetail item={selectedStatus} onClose={() => setSelectedStatus(null)} />}<FormPanel title="New evaluation" note={`${dates.data?.length ?? 0} extracted dates available`} onSubmit={submit} launch={launch} unavailable={dependencyMessage(dependencies.data?.evaluate)}><DateScopeFields {...{range,setRange,date,setDate,start,setStart,end,setEnd}} /><div className="field-row three"><label>Run mode<select value={mode} onChange={(e) => setMode(e.target.value)}><option value="normal">Normal / catch up</option><option value="retry_errors">Retry errors only</option><option value="force">Force reevaluation</option></select></label><label>Request delay<input type="number" min="0" step="0.1" value={delay} onChange={(e) => setDelay(+e.target.value)} /></label><label>Evaluation limit<input type="number" min="0" value={limit} onChange={(e) => setLimit(+e.target.value)} /><small>0 = all pending</small></label></div><div className="field-row three"><label>Provider timeout<input type="number" min="10" max="600" value={requestTimeout} onChange={(e) => setRequestTimeout(+e.target.value)} /><small>Seconds per account request</small></label><label>Max images<input type="number" min="0" value={maxImages} onChange={(e) => setMaxImages(+e.target.value)} /></label><label>Thinking level<select value={thinking} onChange={(e) => setThinking(e.target.value)}><option>low</option><option>medium</option><option>high</option></select></label></div><button type="button" className="text-button" onClick={() => setAdvanced(!advanced)}>{advanced ? 'Hide' : 'Show'} advanced model routes</button>{advanced && <div className="advanced"><label>Text model route<textarea value={textModels} onChange={(e) => setTextModels(e.target.value)} placeholder="Leave blank for defaults" /></label><label>Vision model route<textarea value={visionModels} onChange={(e) => setVisionModels(e.target.value)} placeholder="Leave blank for defaults" /></label></div>}</FormPanel></>
}

function EvaluationLedger({ data, loading, selectedMonth, onMonth, filter, onFilter, onChoose }: any) {
  const visible = (data?.dates ?? []).filter((item: EvaluationDateStatus) => filter === 'complete' ? item.status === 'complete' : filter === 'attention' ? ['needs_retry','paused_quota','incomplete','partial_input'].includes(item.status) : filter === 'not_started' ? ['not_started','waiting_for_input'].includes(item.status) : true)
  return <section className="scan-ledger evaluation-ledger"><header><div><span>Evaluator progress</span><h2>{data?.month ? `${data.month} evaluation ledger` : 'MVP 2 evaluation ledger'}</h2><p>Latest month with evaluator history is selected automatically.</p></div><div className="month-control"><label>Month<input type="month" value={selectedMonth ?? data?.month ?? ''} onChange={(e) => onMonth(e.target.value)} /></label><button type="button" onClick={() => onMonth(undefined)}>Latest local</button></div></header><div className="ledger-summary"><Metric label="Complete" value={data?.counts.complete ?? 0} /><Metric label="Needs retry" value={data?.counts.needs_retry ?? 0} /><Metric label="Quota paused" value={data?.counts.paused_quota ?? 0} /><Metric label="Incomplete" value={(data?.counts.incomplete ?? 0)+(data?.counts.partial_input ?? 0)} /></div><div className="ledger-toolbar">{[['all','All'],['attention','Needs attention'],['complete','Complete'],['not_started','Not started']].map(([value,label]) => <button type="button" key={value} className={filter===value?'active':''} onClick={() => onFilter(value)}>{label}</button>)}</div><div className="ledger-table"><div className="ledger-head evaluation-head"><span>Date</span><span>Status</span><span>Done</span><span>Pending / errors</span><span /></div>{loading && <Empty text="Reading evaluator state..." />}{visible.map((item: EvaluationDateStatus) => <button type="button" className="ledger-row evaluation-row" key={item.date} onClick={() => onChoose(item)}><span className="ledger-date">{item.date}</span><StatusBadge state={item.status} /><span>{item.completed} / {item.active_conversations}<small>{item.coverage_pct}%</small></span><span>{item.pending} pending · {item.errors} errors</span><span className="row-action">Details →</span></button>)}</div></section>
}

function EvaluationDetail({ item, onClose }: { item: EvaluationDateStatus; onClose: () => void }) {
  const history = useQuery({ queryKey: ['evaluation-history', item.date], queryFn: () => api.evaluationHistory(item.date) })
  const errors = useQuery({ queryKey: ['evaluation-errors', item.date], queryFn: () => api.evaluationErrors(item.date) })
  return <section className="evaluation-detail"><header><div><span>{item.date}</span><h2>Evaluation details</h2></div><button type="button" onClick={onClose}>×</button></header><div className="detail-metrics"><Metric label="SAFE" value={item.safe} /><Metric label="UNSAFE" value={item.unsafe} /><Metric label="Review" value={item.review_required} /><Metric label="Runs" value={item.run_count} /></div><p>{item.message}</p>{item.stop_reason && <div className="detail-alert"><strong>Last stop reason</strong>{item.stop_reason}</div>}<div className="detail-columns"><div><h3>Recent runs</h3>{history.data?.runs.slice(0,5).map((run,index) => <HistoryRow key={index} title={String(run.mode ?? 'run')} meta={`${formatTime(run.started_at)} · ${run.status ?? ''}`} />)}{!history.data?.runs.length && <small>No run history yet.</small>}</div><div><h3>Recent attempts</h3>{history.data?.attempts.slice(0,8).map((attempt,index) => <HistoryRow key={index} title={`${attempt.provider}${attempt.credential_label ? ` [${attempt.credential_label}]` : ''}:${attempt.model}`} meta={`${attempt.outcome} · ${String(attempt.conversation_id).slice(0,12)}…`} />)}{!history.data?.attempts.length && <small>No attempt history yet.</small>}</div></div>{errors.data?.errors.length ? <div className="error-summary"><strong>{errors.data.errors.length} active error(s)</strong><span>Choose this date to preselect Retry errors.</span></div> : null}</section>
}

function HistoryRow({ title, meta }: { title: string; meta: string }) { return <div className="history-row"><strong>{title}</strong><span>{meta}</span></div> }
function formatTime(value: unknown) { return typeof value === 'string' ? new Date(value).toLocaleString() : 'unknown time' }

function ReportsPage() {
  const dependencies = useQuery({ queryKey: ['dependencies'], queryFn: api.dependencies })
  const reports = useQuery({ queryKey: ['reports'], queryFn: api.reports, refetchInterval: 5000 })
  const [value, setValue] = useState(month)
  const readiness = useQuery({ queryKey: ['report-readiness', value], queryFn: () => api.reportReadiness(value) })
  const launch = useLaunch()
  const submit = (e: FormEvent) => { e.preventDefault(); launch.mutate({ kind: 'report', payload: { month: value } }) }
  const unavailable = dependencyMessage(dependencies.data?.report) || (readiness.data && !readiness.data.ready ? readiness.data.reason : '')
  return <><PageHeading eyebrow="MVP 3" title="Build the monthly evidence." text="Rebuild from completed local evaluations. No Dimensions or model API access required." /><FormPanel title="Generate workbook" note={readiness.data ? `${readiness.data.source_date_folders} source dates · ${readiness.data.evaluator_result_files} evaluator results` : 'Checking report source data...'} onSubmit={submit} launch={launch} unavailable={unavailable}><label>Month<input type="month" value={value} onChange={(e) => setValue(e.target.value)} required /></label>{readiness.data?.ready && <p className="report-ready">Ready: evaluator output found. If canonical workbook is open in Excel, a safe versioned copy will be generated.</p>}</FormPanel><section className="report-list"><div className="section-head"><div><span>Generated files</span><h2>Monthly reports</h2></div></div>{reports.data?.map((report) => <article key={report.name}><div><strong>{report.name}</strong><span>{report.canonical ? 'Canonical workbook' : 'Versioned copy'} · {(report.size / 1024 / 1024).toFixed(2)} MB · {new Date(report.modified * 1000).toLocaleString()}</span>{report.status === 'complete_with_locked_copy' && <em>Canonical workbook was locked when this copy was generated.</em>}{report.warnings.map((warning) => <em key={warning}>{warning}</em>)}</div><a className="button ghost" href={api.reportFileUrl(report.name)}>Download</a></article>)}{!reports.data?.length && <Empty text="No monthly reports found. Run MVP 1, then MVP 2, before building a report." />}</section></>
}

const credentialFields: { name: CredentialName; label: string; hint: string; secret: boolean }[] = [
  { name: 'GROQ_API_KEY', label: 'Groq API key', hint: 'Optional text evaluation fallback', secret: true },
  { name: 'CLOUDFLARE_API_TOKEN', label: 'Cloudflare API token', hint: 'Workers AI token with inference permission', secret: true },
  { name: 'CLOUDFLARE_ACCOUNT_ID', label: 'Cloudflare account ID', hint: 'Required together with Cloudflare token', secret: false },
  { name: 'OPENROUTER_API_KEY', label: 'OpenRouter API key', hint: 'Optional free-model overflow route', secret: true },
]

function SettingsPage() {
  const client = useQueryClient()
  const credentials = useQuery({ queryKey: ['credentials'], queryFn: api.credentials })
  const [values, setValues] = useState<Partial<Record<CredentialName, string>>>({})
  const [message, setMessage] = useState('')
  const accounts = useQuery({ queryKey: ['gemini-accounts'], queryFn: api.geminiAccounts })
  const [accountLabel, setAccountLabel] = useState('')
  const [accountEmail, setAccountEmail] = useState('')
  const [accountKey, setAccountKey] = useState('')
  const addAccount = useMutation({
    mutationFn: () => api.addGeminiAccount({ label: accountLabel, email: accountEmail, api_key: accountKey }),
    onSuccess: (result) => {
      setAccountLabel(''); setAccountEmail(''); setAccountKey('')
      setMessage(`Gemini account ${result.account.label} added.`)
      client.invalidateQueries({ queryKey: ['gemini-accounts'] })
      client.invalidateQueries({ queryKey: ['credentials'] })
      client.invalidateQueries({ queryKey: ['providers'] })
    },
  })
  const removeAccount = useMutation({
    mutationFn: (id: string) => api.removeGeminiAccount(id),
    onSuccess: () => {
      setMessage('Gemini account removed.')
      client.invalidateQueries({ queryKey: ['gemini-accounts'] })
      client.invalidateQueries({ queryKey: ['credentials'] })
      client.invalidateQueries({ queryKey: ['providers'] })
    },
  })
  const save = useMutation({
    mutationFn: ({ name, value }: { name: CredentialName; value: string }) => api.saveCredential(name, value),
    onSuccess: (_, variables) => {
      setValues((current) => ({ ...current, [variables.name]: '' }))
      setMessage(`${variables.name} saved to Windows Credential Manager.`)
      client.invalidateQueries({ queryKey: ['credentials'] })
      client.invalidateQueries({ queryKey: ['providers'] })
    },
  })
  const remove = useMutation({
    mutationFn: (name: CredentialName) => api.removeCredential(name),
    onSuccess: (_, name) => {
      setMessage(`${name} removed from Windows Credential Manager.`)
      client.invalidateQueries({ queryKey: ['credentials'] })
      client.invalidateQueries({ queryKey: ['providers'] })
    },
  })
  return <>
    <PageHeading eyebrow="Local settings" title="Credentials stay with Windows." text="Secrets are encrypted by Windows Credential Manager and injected only into evaluator worker processes. Values are never returned to this page." />
    <section className="settings-panel">
      <header><span>Provider access</span><h2>API credentials</h2><p>Enter a new value to save or replace it. Blank fields do not mean credentials are missing; use status labels.</p></header>
      {message && <p className="settings-message">{message}</p>}
      <section className="gemini-pool"><header><span>Primary evaluation pool</span><h3>Gemini accounts</h3><p>Keys should belong to separate quota projects/accounts to add usable capacity. Keys from the same project may share quota.</p></header>{accounts.data?.accounts.map((account: GeminiAccount) => <div className="gemini-account" key={account.id}><div><strong>{account.label}</strong><span>{account.email || 'No email label'} · priority {account.priority}</span></div><StatusBadge state={account.configured ? 'complete' : 'failed'} /><button type="button" className="button danger" disabled={removeAccount.isPending} onClick={() => { if (window.confirm(`Remove Gemini account ${account.label}?`)) removeAccount.mutate(account.id) }}>Remove</button></div>)}{!accounts.data?.accounts.length && <p className="pool-empty">No Gemini accounts saved. Add one below.</p>}<form className="gemini-add" onSubmit={(event) => { event.preventDefault(); if (accountLabel.trim() && accountKey.trim()) addAccount.mutate() }}><label>Label<input value={accountLabel} onChange={(event) => setAccountLabel(event.target.value)} placeholder="Primary account" required /></label><label>Email (optional)<input type="email" value={accountEmail} onChange={(event) => setAccountEmail(event.target.value)} placeholder="person@example.com" /></label><label>Google AI Studio API key<input type="password" autoComplete="new-password" value={accountKey} onChange={(event) => setAccountKey(event.target.value)} required /></label><button className="button primary" disabled={addAccount.isPending || !accountLabel.trim() || !accountKey.trim()}>Add Gemini account</button></form>{addAccount.error && <p className="form-error">{addAccount.error.message}</p>}</section>
      {credentialFields.map((field) => {
        const configured = credentials.data?.[field.name] ?? false
        return <form key={field.name} className="credential-row" onSubmit={(event) => {
          event.preventDefault()
          const value = values[field.name]?.trim()
          if (value) save.mutate({ name: field.name, value })
        }}>
          <div className="credential-copy"><div><strong>{field.label}</strong><StatusBadge state={configured ? 'complete' : 'historical'} /></div><span>{field.hint}</span></div>
          <input type={field.secret ? 'password' : 'text'} autoComplete="new-password" value={values[field.name] ?? ''} onChange={(event) => setValues((current) => ({ ...current, [field.name]: event.target.value }))} placeholder={configured ? 'Enter replacement value' : 'Enter value'} aria-label={field.label} />
          <button className="button primary" disabled={!values[field.name]?.trim() || save.isPending}>Save</button>
          <button type="button" className="button danger" disabled={!configured || remove.isPending} onClick={() => remove.mutate(field.name)}>Remove</button>
        </form>
      })}
      {(save.error || remove.error || removeAccount.error) && <p className="form-error">{(save.error || remove.error || removeAccount.error)?.message}</p>}
    </section>
    <aside className="credential-warning"><strong>Security boundary</strong><p>The API returns only configured/not-configured status. Stored values cannot be revealed by this UI. Gemini routes model-first across enabled accounts: account 1, then account 2 for same model. A quota/auth failure disables only affected account for current worker run. Input safety blocks still become manual review and do not move to another account.</p></aside>
  </>
}

function useLaunch() {
  const client = useQueryClient()
  return useMutation({ mutationFn: ({ kind, payload }: { kind: Worker['kind']; payload: unknown }) => api.start(kind, payload), onSuccess: () => client.invalidateQueries({ queryKey: ['workers'] }) })
}

function dependencyMessage(value?: { ready: boolean; missing: string[] }) { return value && !value.ready ? `Unavailable. Missing packages: ${value.missing.join(', ')}. Restart the app to install them.` : '' }

function FormPanel({ title, note, onSubmit, launch, children, unavailable = '' }: any) {
  return <section className="form-layout"><form className="run-form" onSubmit={onSubmit}><header><span>Configure</span><h2>{title}</h2><p>{note}</p></header>{children}{unavailable && <p className="form-error">{unavailable}</p>}{launch.error && <p className="form-error">{launch.error.message}</p>}{launch.data && <p className="settings-message">Worker started. <NavLink to="/">Open Overview for live progress and logs.</NavLink></p>}<button className="button primary" disabled={launch.isPending || !!unavailable}>{launch.isPending ? 'Starting...' : 'Start worker'}</button></form><aside className="safety-note"><span>Independent by design</span><h3>This operation will not launch another MVP.</h3><p>Progress, logs, and stop controls remain available from Overview while it runs.</p></aside></section>
}

function Checks({ values }: { values: [boolean, (v: boolean) => void, string][] }) { return <div className="checks">{values.map(([value,setValue,label]) => <label key={label}><input type="checkbox" checked={value} onChange={(e) => setValue(e.target.checked)} /><span>{label}</span></label>)}</div> }

function LogModal({ worker, onClose }: { worker: Worker; onClose: () => void }) {
  const log = useQuery({ queryKey: ['log', worker.id], queryFn: () => api.log(worker.id), refetchInterval: worker.state === 'running' ? 1500 : false })
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="log-modal" onMouseDown={(e) => e.stopPropagation()}><header><div><span>{worker.kind} · {worker.scope}</span><h2>Worker log</h2></div><button onClick={onClose} aria-label="Close">×</button></header><pre>{log.data || 'Waiting for output...'}</pre></section></div>
}

export default Shell
