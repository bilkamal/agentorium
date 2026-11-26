/* Word-level diff for showing a revision like a code review: the text once, only changed words marked. */
(function (root) {
  'use strict';

  // a word (numbers like 3.840, 9,4% or 01-15 stay whole), a run of spaces, or one punctuation mark
  const TOKEN = /\s+|[\p{L}\p{N}]+(?:[.,:/-][\p{L}\p{N}]+)*%?|[^\s]/gu;
  const MAX_CELLS = 4e6;

  function tokens(s) { return (s || '').match(TOKEN) || []; }

  function lcsOps(a, b) {
    const n = a.length;
    const m = b.length;
    if (n * m > MAX_CELLS) return [['del', a.join('')], ['ins', b.join('')]];
    const w = m + 1;
    const dp = new Int32Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        dp[i * w + j] = a[i] === b[j] ? dp[(i + 1) * w + j + 1] + 1
          : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
      }
    }
    const ops = [];
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { ops.push(['eq', a[i]]); i++; j++; }
      else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) ops.push(['del', a[i++]]);
      else ops.push(['ins', b[j++]]);
    }
    while (i < n) ops.push(['del', a[i++]]);
    while (j < m) ops.push(['ins', b[j++]]);
    return ops;
  }

  // [['eq'|'del'|'ins', text], ...]: joining eq+del gives the old text, eq+ins the new one
  function diff(oldText, newText) {
    const a = tokens(oldText);
    const b = tokens(newText);
    let p = 0;
    while (p < a.length && p < b.length && a[p] === b[p]) p++;
    let s = 0;
    while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
    const ops = [];
    if (p) ops.push(['eq', a.slice(0, p).join('')]);
    ops.push(...lcsOps(a.slice(p, a.length - s), b.slice(p, b.length - s)));
    if (s) ops.push(['eq', a.slice(a.length - s).join('')]);
    // a space between two changes belongs to the change, so "dan terdapat" reads as one deletion
    const tidy = ops.map((op, k) => (op[0] === 'eq' && !op[1].trim() && k > 0 && k < ops.length - 1
      && ops[k - 1][0] !== 'eq' && ops[k + 1][0] !== 'eq') ? ['both', op[1]] : op);
    const out = [];
    let dels = '';
    let ins = '';
    const flush = () => {
      if (dels) out.push(['del', dels]);
      if (ins) out.push(['ins', ins]);
      dels = ''; ins = '';
    };
    tidy.forEach(([kind, text]) => {
      if (kind === 'eq') {
        flush();
        if (out.length && out[out.length - 1][0] === 'eq') out[out.length - 1][1] += text;
        else out.push(['eq', text]);
      } else {
        if (kind !== 'ins') dels += text;
        if (kind !== 'del') ins += text;
      }
    });
    flush();
    return out.filter((op) => op[1]);
  }

  // DOM fragment: unchanged text, <del class="wd-del">, <ins class="wd-ins">
  function render(oldText, newText) {
    const frag = document.createDocumentFragment();
    diff(oldText, newText).forEach(([kind, text]) => {
      if (kind === 'eq') { frag.append(document.createTextNode(text)); return; }
      const node = document.createElement(kind);
      node.className = `wd-${kind}`;
      node.textContent = text;
      frag.append(node);
    });
    return frag;
  }

  const api = { diff, render };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.WordDiff = api;
}(typeof window !== 'undefined' ? window : globalThis));
