/* Which job of a version to show: a running one, else the latest that produced results. A cancelled or
   failed run never hides an earlier finished one. `jobs` comes newest first. */
(function (root) {
  'use strict';
  const ACTIVE = ['queued', 'running', 'paused', 'finalizing'];
  const RESULT = ['done', 'partial'];

  function pick(jobs, types) {
    const mine = (jobs || []).filter((j) => types.includes(j.type));
    return mine.find((j) => ACTIVE.includes(j.status)) || mine.find((j) => RESULT.includes(j.status)) || mine[0] || null;
  }

  const api = { pick };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.JobPick = api;
}(typeof window !== 'undefined' ? window : globalThis));
