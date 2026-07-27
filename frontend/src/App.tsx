import { useState, useEffect, useRef } from 'react';
import { ShieldCheck, Cpu, ScanFace, CheckCircle2, AlertTriangle } from 'lucide-react';

// Live state published by the Python backend (GET /api/state)
interface Identity { match: string; confidence: number | null; user_id: string | null; distance: number | null; }
interface Risk { decision: string | null; reason: string | null; failures: number; }
interface Result { ok: boolean; big: string; small: string; }
interface Enroll { name: string | null; progress: number; total: number; }
interface State {
  phase: string;
  intent: string | null;
  camera_ok: boolean;
  session_id: string;
  session_owner: string | null;
  owner_balance: string | null;
  amount: string;
  prompt: string;
  seconds_left: number;
  identity: Identity;
  liveness: string;
  risk: Risk;
  pin_mode: string | null;
  result: Result | null;
  enroll: Enroll;
  audit: string[];
  model: string;
  threshold: number;
}

const post = (action: string, value?: string) =>
  fetch('/api/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, value }),
  });

export default function App() {
  const [s, setS] = useState<State | null>(null);
  const [amount, setAmount] = useState('');
  const [pin, setPin] = useState('');
  const [regName, setRegName] = useState('');
  const auditRef = useRef<HTMLDivElement>(null);

  // Poll live backend state
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const res = await fetch('/api/state');
        const data = await res.json();
        if (alive) setS(data);
      } catch { /* backend not up yet */ }
    };
    tick();
    const id = setInterval(tick, 350);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Keep the audit log pinned to the bottom WITHOUT scrolling the whole page
  useEffect(() => {
    if (auditRef.current) auditRef.current.scrollTop = auditRef.current.scrollHeight;
  }, [s?.audit]);

  // Clear local inputs when the flow leaves their phase
  useEffect(() => {
    if (s?.phase !== 'IDLE') setAmount('');
    if (s?.phase !== 'PIN') setPin('');
    if (s?.phase !== 'ENROLL_OFFER' && s?.phase !== 'OWNER_ENROLL_OFFER') setRegName('');
  }, [s?.phase]);

  // ESC = owner requests to end the session (backend only acts when idle in a session)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') post('logout'); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const phase = s?.phase ?? 'IDLE';

  const authorise = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!amount) return;
    await post('set_amount', amount);
    await post('approve');
  };

  const submitPin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (pin.length < 4) return;
    await post('submit_pin', pin);
    setPin('');
  };

  const matchClass = (m: string) =>
    m === 'VERIFIED' ? 'dream-glow-green' : m === 'NOT RECOGNISED' ? 'dream-glow-error' : 'text-dream-silver/40';

  const livenessNode = (l: string) => {
    if (l === 'PASSED') return <span className="dream-glow-green">PASSED</span>;
    if (l === 'FAILED') return <span className="dream-glow-error">FAILED</span>;
    if (l === 'ANALYSING') return <span className="text-dream-blue animate-pulse">ANALYSING...</span>;
    return <span className="text-dream-silver/40">AWAITING</span>;
  };

  const decisionNode = (d: string | null) => {
    if (d === 'allow') return <span className="dream-glow-green">ALLOW</span>;
    if (d === 'pin_stepup') return <span className="text-dream-blue">STEP-UP</span>;
    if (d === 'dual_approval') return <span className="dream-glow-error">DUAL CTRL</span>;
    return <span className="text-dream-silver/40">—</span>;
  };

  const panelHead = (t: string) => (
    <div className="text-[10px] font-mono text-dream-silver/50 uppercase tracking-widest border-b border-dream-border/50 pb-1 mb-1">
      {t}
    </div>
  );
  const row = (label: string, value: React.ReactNode) => (
    <div className="flex justify-between items-center text-xs font-mono">
      <span className="text-dream-silver/70">{label}</span>
      {value}
    </div>
  );

  const id = s?.identity;
  const risk = s?.risk;
  const isLiveness = phase === 'LIVENESS' || phase === 'DUAL_LIVENESS';
  const isScanning = phase === 'SCANNING' || phase === 'DUAL_SCANNING';
  const ctx = s?.intent === 'owner_login' ? 'Owner Login'
    : s?.intent === 'logout' ? 'Confirm Logout' : 'Liveness Challenge';

  return (
    <div className="fixed inset-0 bg-dream-dark overflow-hidden relative font-sans text-dream-silver">
      <div className="absolute top-1/3 left-1/4 w-[600px] h-[600px] bg-dream-purple/10 blur-[120px] rounded-full pointer-events-none mix-blend-screen" />
      <div className="absolute bottom-1/4 right-1/4 w-[500px] h-[500px] bg-dream-blue/10 blur-[100px] rounded-full pointer-events-none mix-blend-screen" />
      <div className="noise-overlay" />

      <div className="w-full h-full flex dream-glass overflow-hidden z-10">

        {/* LEFT: Camera & interaction */}
        <div className="w-[45%] flex flex-col border-r border-dream-border relative">
          <div className="h-[70%] relative overflow-hidden bg-black flex items-center justify-center">
            {s && !s.camera_ok ? (
              <div className="text-dream-error/70 font-mono text-sm tracking-widest flex flex-col items-center gap-4">
                <ScanFace size={32} strokeWidth={1} /> CAMERA UNAVAILABLE
              </div>
            ) : (
              <>
                <img
                  src="/api/video"
                  alt="Live feed"
                  className="w-full h-full object-cover"
                />
                <div className="absolute top-4 left-4 bg-black/40 backdrop-blur-md border border-dream-border px-3 py-1 font-mono text-[10px] uppercase text-dream-silver/80 z-20">
                  <span className="inline-block w-2 h-2 rounded-full bg-dream-green mr-2 shadow-[0_0_8px_var(--color-dream-green)]" />
                  REC // NODE_7
                </div>
                {isLiveness && (
                  <div className="absolute bottom-4 right-4 bg-black/40 backdrop-blur-md border border-dream-border px-3 py-1 font-mono text-[10px] text-dream-blue z-20">
                    {s?.seconds_left?.toFixed(1)}s
                  </div>
                )}
              </>
            )}
          </div>

          <div className="h-[30%] bg-dream-dark/50 border-t border-dream-border p-6 flex flex-col justify-center relative overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-t from-dream-purple/5 to-transparent pointer-events-none" />
            <div className="relative z-10 w-full">

              {phase === 'OWNER_LOGIN' && (
                <div className="flex flex-col items-center gap-3">
                  <div className="font-mono text-xs text-dream-silver/70 uppercase tracking-widest flex items-center gap-2">
                    <ShieldCheck size={14} /> Owner Terminal
                  </div>
                  <div className="font-mono text-[11px] text-dream-silver/50 uppercase tracking-widest">Sign in to start a session</div>
                  <button onClick={() => post('owner_login')}
                    className="text-[11px] font-mono border border-dream-border px-4 py-1.5 tracking-widest hover:bg-dream-silver hover:text-black transition-colors">
                    LOG IN AS OWNER
                  </button>
                </div>
              )}

              {phase === 'IDLE' && (
                <form onSubmit={authorise} className="flex flex-col items-center">
                  <div className="font-mono text-xs text-dream-silver/60 uppercase tracking-widest mb-3">Transaction Amount</div>
                  <div className="flex items-center gap-2 border-b border-dream-silver/40 pb-1 mb-3">
                    <span className="text-2xl text-dream-silver">£</span>
                    <input
                      type="number" step="0.01" autoFocus value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      className="bg-transparent text-3xl font-mono text-dream-white outline-none w-32 text-center"
                      placeholder="0.00"
                    />
                  </div>
                  <button type="submit"
                    className="text-[11px] font-mono border border-dream-border px-4 py-1.5 tracking-widest hover:bg-dream-silver hover:text-black transition-colors">
                    AUTHORISE WITH FACE
                  </button>
                  <div className="mt-2 text-[9px] font-mono text-dream-silver/30 uppercase tracking-widest">Press ESC to end session</div>
                </form>
              )}

              {isLiveness && (
                <div className="text-center space-y-2">
                  <div className="font-mono text-xs text-dream-blue uppercase tracking-widest mb-2">{ctx}</div>
                  <div className="text-xl font-bold tracking-wider dream-glow-text uppercase">
                    {s?.prompt || '…'}
                  </div>
                </div>
              )}

              {isScanning && (
                <div className="text-center font-mono text-lg text-dream-blue tracking-widest animate-pulse">
                  {s?.prompt || 'AUTHORISING…'}
                </div>
              )}

              {phase === 'PIN' && (
                <form onSubmit={submitPin} className="flex flex-col items-center">
                  <div className="font-mono text-xs text-dream-error uppercase tracking-widest mb-3 flex items-center gap-2 text-center">
                    <AlertTriangle size={14} /> {s?.prompt || 'PIN required'}
                  </div>
                  <input
                    type="password" autoFocus maxLength={8} value={pin}
                    onChange={(e) => setPin(e.target.value)}
                    className="bg-black/40 border border-dream-border text-2xl font-mono text-center text-dream-white outline-none w-40 py-2 tracking-[0.5em]"
                    placeholder="••••"
                  />
                </form>
              )}

              {phase === 'ENROLL_OFFER' && (
                <div className="flex flex-col items-center gap-2">
                  <div className="font-mono text-xs text-dream-error uppercase tracking-widest flex items-center gap-2">
                    <AlertTriangle size={14} /> Not Recognised
                  </div>
                  <div className="font-mono text-[11px] text-dream-silver/60 uppercase tracking-widest">Register a new user?</div>
                  <form onSubmit={(e) => { e.preventDefault(); if (regName.trim()) post('register', regName); }} className="flex flex-col items-center gap-2">
                    <input
                      autoFocus value={regName} onChange={(e) => setRegName(e.target.value)}
                      placeholder="enter your name"
                      className="bg-black/40 border border-dream-border text-sm font-mono text-center text-dream-white outline-none w-44 py-1.5"
                    />
                    <div className="flex gap-2">
                      <button type="submit"
                        className="text-[10px] font-mono border border-dream-border px-3 py-1 tracking-widest hover:bg-dream-silver hover:text-black transition-colors">
                        REGISTER
                      </button>
                      <button type="button" onClick={() => post('cancel_enroll')}
                        className="text-[10px] font-mono border border-dream-border/40 px-3 py-1 tracking-widest text-dream-silver/60 hover:bg-dream-silver/10 transition-colors">
                        CANCEL
                      </button>
                    </div>
                  </form>
                </div>
              )}

              {phase === 'ENROLLING' && (
                <div className="text-center space-y-2">
                  <div className="font-mono text-xs text-dream-blue uppercase tracking-widest">Registering</div>
                  <div className="text-xl font-bold tracking-wider dream-glow-text uppercase">{s?.enroll?.name}</div>
                  <div className="font-mono text-xs text-dream-silver/70">
                    Capturing {s?.enroll?.progress ?? 0}/{s?.enroll?.total ?? 5} — look at the camera
                  </div>
                </div>
              )}

              {phase === 'OWNER_ENROLL_OFFER' && (
                <div className="flex flex-col items-center gap-2">
                  <div className="font-mono text-xs text-dream-error uppercase tracking-widest flex items-center gap-2">
                    <AlertTriangle size={14} /> Owner Not Recognised
                  </div>
                  <div className="font-mono text-[11px] text-dream-silver/60 uppercase tracking-widest">Register as the owner?</div>
                  <form onSubmit={(e) => { e.preventDefault(); if (regName.trim()) post('owner_register', regName); }} className="flex flex-col items-center gap-2">
                    <input
                      autoFocus value={regName} onChange={(e) => setRegName(e.target.value)}
                      placeholder="enter your name"
                      className="bg-black/40 border border-dream-border text-sm font-mono text-center text-dream-white outline-none w-44 py-1.5"
                    />
                    <div className="flex gap-2">
                      <button type="submit"
                        className="text-[10px] font-mono border border-dream-border px-3 py-1 tracking-widest hover:bg-dream-silver hover:text-black transition-colors">
                        REGISTER OWNER
                      </button>
                      <button type="button" onClick={() => post('cancel_enroll')}
                        className="text-[10px] font-mono border border-dream-border/40 px-3 py-1 tracking-widest text-dream-silver/60 hover:bg-dream-silver/10 transition-colors">
                        CANCEL
                      </button>
                    </div>
                  </form>
                </div>
              )}

              {phase === 'OWNER_ENROLLING' && (
                <div className="text-center space-y-2">
                  <div className="font-mono text-xs text-dream-blue uppercase tracking-widest">Registering Owner</div>
                  <div className="text-xl font-bold tracking-wider dream-glow-text uppercase">{s?.enroll?.name}</div>
                  <div className="font-mono text-xs text-dream-silver/70">
                    Capturing {s?.enroll?.progress ?? 0}/{s?.enroll?.total ?? 5} — look at the camera
                  </div>
                </div>
              )}

              {phase === 'LOGOUT_CONFIRM' && (
                <div className="text-center flex flex-col items-center gap-3">
                  <div className="font-mono text-xs text-dream-silver/80 uppercase tracking-widest">{s?.prompt}</div>
                  <div className="flex gap-2">
                    <button onClick={() => post('logout_confirm')}
                      className="text-[10px] font-mono border border-dream-error/60 text-dream-error px-4 py-1 tracking-widest hover:bg-dream-error hover:text-black transition-colors">
                      YES, END
                    </button>
                    <button onClick={() => post('logout_cancel')}
                      className="text-[10px] font-mono border border-dream-border/40 px-4 py-1 tracking-widest text-dream-silver/60 hover:bg-dream-silver/10 transition-colors">
                      NO
                    </button>
                  </div>
                </div>
              )}

              {phase === 'DUAL_PROMPT' && (
                <div className="text-center flex flex-col items-center gap-3">
                  <div className="font-mono text-xs text-dream-error uppercase tracking-widest flex items-center gap-2">
                    <AlertTriangle size={14} /> Second Approval Required
                  </div>
                  <button onClick={() => post('approve')}
                    className="text-[11px] font-mono border border-dream-border px-4 py-1.5 tracking-widest hover:bg-dream-silver hover:text-black transition-colors">
                    APPROVER READY
                  </button>
                </div>
              )}

              {phase === 'RESULT' && s?.result && (
                <div className="text-center flex flex-col items-center gap-2 cursor-pointer" onClick={() => post('reset')}>
                  {s.result.ok
                    ? <CheckCircle2 size={32} className="text-dream-green" />
                    : <AlertTriangle size={28} className="text-dream-error" />}
                  <div className={`font-mono text-base uppercase tracking-widest ${s.result.ok ? 'dream-glow-green' : 'dream-glow-error'}`}>
                    {s.result.big}
                  </div>
                  {s.result.small && <div className="text-[10px] text-dream-silver/60 font-mono">{s.result.small}</div>}
                  <div className="text-[10px] text-dream-silver/40 font-mono mt-1">Click to reset</div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* RIGHT: Telemetry & logs */}
        <div className="w-[55%] flex flex-col bg-black/40 relative">
          <div className="p-5 border-b border-dream-border flex justify-between items-center bg-dream-panel">
            <div className="flex items-center gap-3">
              <ShieldCheck size={20} className="text-dream-silver" />
              <div>
                <h1 className="text-sm font-bold tracking-widest uppercase">Secure Enclave</h1>
                <p className="text-[10px] text-dream-silver/50 font-mono tracking-widest uppercase">
                  {s?.session_owner ? `Owner: ${s.session_owner} · ${s.session_id}` : 'Owner terminal · not signed in'}
                </p>
              </div>
            </div>
            <div className="font-mono text-xs text-dream-silver/50 flex gap-4 items-center">
              {s?.session_owner
                ? <span className="text-dream-silver/70">TAKINGS: <span className="dream-glow-green">{s.owner_balance}</span></span>
                : <span>{s?.model ?? 'FaceNet-512'}</span>}
              <span className="dream-glow-green">SECURE</span>
            </div>
          </div>

          <div className="p-5 grid grid-cols-2 gap-4">
            {/* Identity */}
            <div className="border border-dream-border bg-black/20 p-3 flex flex-col gap-2">
              {panelHead('Identity Verification')}
              {row('Match status:', <span className={matchClass(id?.match ?? 'PENDING')}>{id?.match ?? 'PENDING'}</span>)}
              {row('Confidence:', <span className="text-dream-white">{id?.confidence != null ? `${id.confidence}%` : '--.-%'}</span>)}
              {row('User ID:', <span className="text-dream-white uppercase">{id?.user_id ?? '---'}</span>)}
            </div>

            {/* Presentation Attack Detection */}
            <div className="border border-dream-border bg-black/20 p-3 flex flex-col gap-2">
              {panelHead('Presentation Attack Det.')}
              {row('Liveness:', livenessNode(s?.liveness ?? 'AWAITING'))}
              {row('Method:', <span className="text-dream-white">CHALLENGE-RSP</span>)}
            </div>

            {/* Session Security (real: Fernet/AES + PBKDF2) */}
            <div className="border border-dream-border bg-black/20 p-3 flex flex-col gap-2">
              {panelHead('Session Security')}
              {row('Templates:', <span className="dream-glow-green">AES @ REST</span>)}
              {row('PIN store:', <span className="text-dream-white">PBKDF2</span>)}
            </div>

            {/* Risk Engine */}
            <div className="border border-dream-border bg-black/20 p-3 flex flex-col gap-2">
              {panelHead('Risk Engine')}
              {row('Decision:', decisionNode(risk?.decision ?? null))}
              {row('Recent fails:', <span className="text-dream-white">{risk?.failures ?? 0}</span>)}
            </div>
          </div>

          {/* Audit trail */}
          <div className="flex-1 flex flex-col p-5 pt-0 overflow-hidden">
            <div className="text-[10px] font-mono text-dream-silver/50 uppercase tracking-widest border-b border-dream-border/50 pb-2 mb-3 flex items-center gap-2">
              <Cpu size={12} /> Audit Trail
            </div>
            <div ref={auditRef} className="flex-1 bg-black/60 border border-dream-border/50 p-3 overflow-y-auto font-mono text-[10px] leading-relaxed text-dream-silver/80">
              {!s?.audit?.length ? (
                <div className="text-dream-silver/30 text-center mt-4">[ WAITING FOR SECURE EVENT ]</div>
              ) : (
                s.audit.map((line, i) => (
                  <div key={i} className="mb-1 opacity-90 break-words">
                    <span className="text-dream-blue/50 mr-2">❯</span>{line}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
