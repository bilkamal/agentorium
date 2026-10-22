/* Agentorium client for Node.js / browsers — drive the office visualiser from any JS workflow.
 *
 *   import { Office } from './agentorium.js';
 *   const office = new Office('http://127.0.0.1:8000');
 *   const s = await office.session('My workflow');
 *   await s.register('coder', 'Coder', { role: 'worker', color: '#e67e22' });
 *   await s.status('coder', 'writing code');
 *   await s.flow('planner', 'coder', 'task');
 *   await s.say('coder', 'Done!');
 *   await s.status('coder', 'idle');
 */
export class Office {
  constructor(baseUrl = 'http://127.0.0.1:8000', { token = null, strict = false } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, ''); this.token = token; this.strict = strict;
  }
  async request(method, path, body) {
    const headers = { 'Content-Type': 'application/json' };
    if (this.token) headers['X-Office-Token'] = this.token;
    try {
      const res = await fetch(`${this.baseUrl}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      return res.json();
    } catch (err) { if (this.strict) throw err; console.warn('[agentorium]', method, path, err.message); return null; }
  }
  async session(title = 'External workflow') { const d = await this.request('POST', '/api/sessions', { kind: 'external', title }); return new Session(this, d?.id); }
  attach(id) { return new Session(this, id); }
}

export class Session {
  constructor(office, id) { this.office = office; this.id = id; }
  emit(type, data = {}) { return this.id ? this.office.request('POST', `/api/sessions/${this.id}/events`, { type, ...data }) : null; }
  emitMany(events) { return this.id ? this.office.request('POST', `/api/sessions/${this.id}/events`, { events }) : null; }
  register(id, name, { role = 'worker', color, hair, skills = '' } = {}) { return this.emit('register_agent', { id, name, role, color, hair, skills }); }
  status(agent, status = 'idle') { return this.emit('agent_status', { agent, status }); }
  idle(agent) { return this.status(agent, 'idle'); }
  flow(source, target, label = '') { return this.emit('flow', { source, target, label }); }
  say(agent, content) { return this.emit('chat', { agent, role: 'assistant', content }); }
  stage(stage) { return this.emit('stage', { stage }); }
  plan(objective, steps, acceptance_criteria = [], assumptions = []) { return this.emit('plan', { status: 'agreed', plan: { objective, steps, acceptance_criteria, assumptions } }); }
  step(step_id, status, attempt = 1, assignee) { return this.emit('step', { step_id, status, attempt, assignee }); }
  review(step_id, verdict, score = null, feedback = '', attempt = 1) { return this.emit('review', { step_id, verdict, score, feedback, attempt }); }
  output(agent, step_id, output, attempt = 1) { return this.emit('output', { agent, step_id, output, attempt }); }
  note(agent, message) { return this.emit('note', { agent, message }); }
  busy(busy = true) { return this.emit('busy', { busy }); }
  /** Browser/Node 18+: subscribe to the SSE stream; `onUser` receives each user chat message. */
  listen(onUser, onEvent) {
    const src = new EventSource(`${this.office.baseUrl}/api/sessions/${this.id}/events`);
    src.addEventListener('chat', (ev) => { const e = JSON.parse(ev.data); if (e.role === 'user') onUser?.(e.content, e); });
    if (onEvent) ['agent_status', 'flow', 'stage', 'plan', 'document'].forEach((t) => src.addEventListener(t, (ev) => onEvent(JSON.parse(ev.data))));
    return src;
  }
}
