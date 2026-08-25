/* LUMA — day timeline ("Day view" tab on the team view).
 *
 * Deliberately a SEPARATE file from today.html: script tags are parsed and
 * executed independently, so a syntax error or a crash in here cannot stop
 * the card view, Navigate, or anything else crews rely on mid-job.
 *
 * The host page owns the data and the date and calls:
 *     DayView.render(dayData, dateObj)
 * Nothing in here reaches into the host page's state.
 */
(function () {
  'use strict';

  var DAY_START = 7 * 60 + 30, DAY_END = 15 * 60 + 30;   // 07:30 – 15:30
  var TYPE_LBL = { install: 'Install', collect: 'Collect', load: 'Load', task: 'Task', brk: 'Break' };

  var CREWS = [], JOBS = {}, pxPerMin = 0.9, styled = false, built = false;

  var CSS = [
    '#dayview-root{padding-bottom:20px;}',
    '#dayview-root .daybar{padding:14px 16px;display:flex;align-items:baseline;gap:20px;flex-wrap:wrap;border-bottom:1px solid var(--border,#e0d8ce);background:#fff;}',
    '#dayview-root .day-date{font-family:"Cormorant Garamond",serif;font-size:1.3rem;}',
    '#dayview-root .stat{display:flex;align-items:baseline;gap:6px;}',
    '#dayview-root .stat-n{font-family:"Cormorant Garamond",serif;font-size:1.3rem;line-height:1;}',
    '#dayview-root .stat-l{font-size:0.66rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--muted,#9a8f80);}',
    '#dayview-root .stat-idle .stat-n{color:var(--red,#e85c47);}',
    '#dayview-root .dv-controls{padding:10px 16px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;border-bottom:1px solid var(--border,#e0d8ce);}',
    '#dayview-root .zoom-presets{display:flex;border:1px solid var(--border,#e0d8ce);border-radius:3px;overflow:hidden;}',
    '#dayview-root .zoom-presets button{padding:6px 12px;background:#fff;border:none;border-right:1px solid var(--border,#e0d8ce);font-family:inherit;font-size:0.7rem;letter-spacing:0.06em;text-transform:uppercase;color:var(--muted,#9a8f80);cursor:pointer;}',
    '#dayview-root .zoom-presets button:last-child{border-right:none;}',
    '#dayview-root .zoom-presets button.on{background:var(--ink,#1a1714);color:#fff;}',
    '#dayview-root .zoom-step{width:30px;height:30px;border:1px solid var(--border,#e0d8ce);background:#fff;border-radius:3px;font-size:1rem;color:var(--ink,#1a1714);cursor:pointer;line-height:1;font-family:inherit;}',
    '#dayview-root .hint{font-size:0.68rem;color:var(--muted,#9a8f80);margin-left:auto;}',
    '#dayview-root .scroller{overflow-x:auto;padding-bottom:8px;}',
    '#dayview-root .grid{position:relative;padding:0 16px 16px;min-width:max-content;}',
    '#dayview-root .dv-row{display:flex;align-items:stretch;border-bottom:1px solid var(--border,#e0d8ce);}',
    '#dayview-root .dv-row:last-child{border-bottom:none;}',
    '#dayview-root .rowhead{position:sticky;left:0;z-index:10;background:var(--warm,#faf8f4);width:124px;flex:0 0 124px;padding:10px 10px 10px 0;border-right:1px solid var(--border,#e0d8ce);}',
    '#dayview-root .crew-name{font-size:0.82rem;font-weight:500;}',
    '#dayview-root .crew-people{font-size:0.66rem;color:var(--muted,#9a8f80);margin-top:2px;}',
    '#dayview-root .crew-util{margin-top:5px;height:3px;background:var(--sand,#f0ebe4);border-radius:2px;overflow:hidden;}',
    '#dayview-root .crew-util span{display:block;height:100%;background:var(--green,#4a7c59);}',
    '#dayview-root .crew-util.low span{background:var(--red,#e85c47);}',
    '#dayview-root .lane{position:relative;flex:1;padding:10px 0;}',
    '#dayview-root .ruler{display:flex;align-items:flex-end;height:24px;}',
    '#dayview-root .ruler .rowhead{padding-bottom:4px;border-bottom:none;}',
    '#dayview-root .ruler-lane{position:relative;flex:1;}',
    '#dayview-root .tick{position:absolute;bottom:0;font-size:0.62rem;color:var(--muted,#9a8f80);transform:translateX(-50%);white-space:nowrap;}',
    '#dayview-root .gridline{position:absolute;top:0;bottom:0;width:1px;background:var(--border,#e0d8ce);opacity:0.55;}',
    '#dayview-root .gridline.hour{opacity:0.9;}',
    '#dayview-root .blk{position:absolute;top:10px;bottom:10px;border-radius:3px;padding:4px 6px;overflow:hidden;cursor:pointer;color:#fff;font-size:0.7rem;line-height:1.25;}',
    '#dayview-root .blk-ref{font-weight:600;white-space:nowrap;}',
    '#dayview-root .blk-sub{opacity:0.86;font-size:0.64rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
    '#dayview-root .blk.install{background:var(--green,#4a7c59);}',
    '#dayview-root .blk.collect{background:var(--blue,#2e5a8a);}',
    '#dayview-root .blk.load{background:var(--accent,#b8935a);}',
    '#dayview-root .blk.task{background:var(--purple,#6a3d8a);}',
    '#dayview-root .blk.brk{background:repeating-linear-gradient(45deg,#8C8375,#8C8375 5px,#7E7568 5px,#7E7568 10px);}',
    '#dayview-root .blk.tight .blk-sub{display:none;}',
    '#dayview-root .idle{position:absolute;top:10px;bottom:10px;border-radius:3px;background:repeating-linear-gradient(135deg,rgba(232,92,71,0.10),rgba(232,92,71,0.10) 4px,transparent 4px,transparent 9px);border:1px dashed rgba(232,92,71,0.35);}',
    '#dayview-root .idle-lbl{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:0.58rem;color:var(--red,#e85c47);opacity:0.75;white-space:nowrap;}',
    '#dayview-root .now{position:absolute;top:0;bottom:0;width:2px;background:var(--red,#e85c47);z-index:5;}',
    '#dayview-root .dv-legend{display:flex;gap:14px;flex-wrap:wrap;padding:10px 16px 6px;font-size:0.68rem;color:var(--muted,#9a8f80);}',
    '#dayview-root .dv-legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;font-style:normal;}',
    '#dayview-root .dv-note{padding:4px 16px 18px;font-size:0.7rem;color:var(--muted,#9a8f80);line-height:1.6;}',
    '#dayview-root .dv-empty{padding:40px 20px;text-align:center;color:var(--muted,#9a8f80);font-size:0.85rem;}',
    '#dayview-root .dv-panel{position:fixed;right:0;top:0;bottom:0;width:280px;max-width:100%;background:#fff;border-left:1px solid var(--border,#e0d8ce);padding:20px 18px;transform:translateX(100%);transition:transform .18s ease;z-index:400;box-shadow:-8px 0 28px rgba(0,0,0,0.07);}',
    '#dayview-root .dv-panel.open{transform:translateX(0);}',
    '#dayview-root .dv-panel h3{font-family:"Cormorant Garamond",serif;font-size:1.3rem;font-weight:400;margin-bottom:2px;}',
    '#dayview-root .p-sub{font-size:0.74rem;color:var(--muted,#9a8f80);margin-bottom:14px;}',
    '#dayview-root .p-row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border,#e0d8ce);font-size:0.78rem;}',
    '#dayview-root .p-row span:first-child{color:var(--muted,#9a8f80);}',
    '#dayview-root .p-close{position:absolute;top:12px;right:14px;background:none;border:none;font-size:1.2rem;color:var(--muted,#9a8f80);cursor:pointer;}',
    '@media(max-width:640px){#dayview-root .rowhead{width:92px;flex:0 0 92px;}#dayview-root .hint{display:none;}}'
  ].join('\n');

  var MARKUP =
    '<div class="daybar">' +
      '<div class="day-date" id="dv-date">—</div>' +
      '<div class="stat"><span class="stat-n" id="dv-crews">–</span><span class="stat-l">crews</span></div>' +
      '<div class="stat"><span class="stat-n" id="dv-stops">–</span><span class="stat-l">stops</span></div>' +
      '<div class="stat"><span class="stat-n" id="dv-booked">–</span><span class="stat-l">on site</span></div>' +
      '<div class="stat stat-idle"><span class="stat-n" id="dv-idle">–</span><span class="stat-l">idle</span></div>' +
    '</div>' +
    '<div class="dv-controls">' +
      '<div class="zoom-presets" id="dv-presets">' +
        '<button data-z="0.9" class="on">Whole day</button>' +
        '<button data-z="2.2">Half day</button>' +
        '<button data-z="4.5">Detail</button>' +
      '</div>' +
      '<button class="zoom-step" id="dv-out" title="Zoom out">−</button>' +
      '<button class="zoom-step" id="dv-in" title="Zoom in">+</button>' +
      '<span class="hint">Ctrl + scroll to zoom · drag to pan</span>' +
    '</div>' +
    '<div class="scroller" id="dv-scroller"><div class="grid" id="dv-grid"></div></div>' +
    '<div class="dv-legend">' +
      '<span><i style="background:#4a7c59"></i>Install</span>' +
      '<span><i style="background:#2e5a8a"></i>Collect</span>' +
      '<span><i style="background:#b8935a"></i>Load</span>' +
      '<span><i style="background:#6a3d8a"></i>Task</span>' +
      '<span><i style="background:#8C8375"></i>Break</span>' +
      '<span><i style="background:rgba(232,92,71,0.25);border:1px dashed rgba(232,92,71,0.5)"></i>Idle</span>' +
    '</div>' +
    '<p class="dv-note">Idle blocks show where the 07:30–15:30 day is going unused. ' +
    'Times are what is scheduled, not what actually happened.</p>' +
    '<div class="dv-panel" id="dv-panel">' +
      '<button class="p-close" id="dv-panel-close">×</button>' +
      '<h3 id="dv-p-title">—</h3><div class="p-sub" id="dv-p-sub">—</div>' +
      '<div id="dv-p-body"></div>' +
    '</div>';

  function toMin(t) {
    if (!t) return null;
    var parts = String(t).split(':');
    var h = Number(parts[0]), m = Number(parts[1]);
    return (isNaN(h) || isNaN(m)) ? null : h * 60 + m;
  }
  function fmt(m) {
    return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
  }
  function dur(m) {
    if (m < 60) return m + 'm';
    var h = Math.floor(m / 60), r = m % 60;
    return r ? h + 'h' + r + 'm' : h + 'h';
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  var x = function (m) { return (m - DAY_START) * pxPerMin; };

  /* Turn the runsheet payload into crew rows and blocks. Entry-to-team
     resolution mirrors the card view: team_id first, vehicle for older rows
     saved before team_id existed. */
  function shape(dayData) {
    var teams = (dayData && dayData.teams) || [];
    var schedule = (dayData && dayData.schedule) || [];
    var tasks = (dayData && dayData.tasks) || [];
    var jobs = (dayData && dayData.jobs) || [];
    var jobById = {};
    jobs.forEach(function (j) { jobById[j.id] = j; });

    CREWS = teams.map(function (t) {
      var others = (t.members || []).filter(function (m) { return m !== t.lead; });
      return {
        id: t.id,
        name: t.name || t.vehicle || 'Team',
        people: [t.lead].concat(others).filter(Boolean).join(', ')
      };
    });

    function belongs(e, t) {
      return e.team_id === t.id || (!e.team_id && t.vehicle && e.vehicle === t.vehicle);
    }

    JOBS = {};
    teams.forEach(function (t) {
      var key = t.name || t.vehicle || 'Team', rows = [];
      schedule.forEach(function (e) {
        if (!belongs(e, t)) return;
        var s = toMin(e.start_time);
        if (s === null) return;                       // unplaced — lives in the tray
        var job = jobById[e.job_id] || {};
        rows.push({
          s: s, d: e.duration || 60, type: e.type || 'install',
          ref: job.job_ref || job.job_number || '—',
          addr: (job.address || '').split(',')[0].trim()
        });
      });
      tasks.forEach(function (k) {
        if (!belongs(k, t) && k.vehicle !== 'ALL') return;
        var s = toMin(k.start_time);
        if (s === null) return;
        rows.push({
          s: s, d: k.duration || 30,
          type: k.kind === 'break' ? 'brk' : 'task',
          ref: k.title || 'Task', addr: ''
        });
      });
      rows.sort(function (a, b) { return a.s - b.s; });
      JOBS[key] = rows;
    });
  }

  function draw() {
    var grid = document.getElementById('dv-grid');
    if (!grid) return;

    if (!CREWS.length) {
      grid.innerHTML = '<div class="dv-empty">No crews set up for this day yet.</div>';
      ['dv-crews', 'dv-stops', 'dv-booked', 'dv-idle'].forEach(function (id) {
        var el = document.getElementById(id); if (el) el.textContent = '–';
      });
      return;
    }

    var laneW = (DAY_END - DAY_START) * pxPerMin;
    var stepMin = pxPerMin < 1.4 ? 60 : (pxPerMin < 3 ? 30 : 15);
    var ticks = '', lines = '', m;
    for (m = DAY_START; m <= DAY_END; m += stepMin) {
      lines += '<div class="gridline ' + (m % 60 === 0 ? 'hour' : '') + '" style="left:' + x(m) + 'px"></div>';
      ticks += '<div class="tick" style="left:' + x(m) + 'px">' + fmt(m) + '</div>';
    }

    var html = '<div class="ruler"><div class="rowhead"></div>' +
      '<div class="ruler-lane" style="width:' + laneW + 'px">' + ticks + '</div></div>';

    var totalBooked = 0, totalIdle = 0, stops = 0;

    CREWS.forEach(function (crew) {
      var list = JOBS[crew.name] || [];
      var blocks = '', booked = 0, cursor = DAY_START;

      list.forEach(function (j) {
        if (j.s - cursor >= 10) {
          var mins = j.s - cursor, w = mins * pxPerMin;
          totalIdle += mins;
          blocks += '<div class="idle" style="left:' + x(cursor) + 'px;width:' + w + 'px">' +
            (w > 52 ? '<span class="idle-lbl">' + dur(mins) + ' idle</span>' : '') + '</div>';
        }
        var bw = j.d * pxPerMin;
        if (j.type !== 'brk') { booked += j.d; stops++; }
        blocks += '<div class="blk ' + j.type + (bw < 64 ? ' tight' : '') +
          '" style="left:' + x(j.s) + 'px;width:' + Math.max(14, bw) + 'px"' +
          ' data-blk=\'' + esc(JSON.stringify({ s: j.s, d: j.d, type: j.type, ref: j.ref, addr: j.addr, crew: crew.name })) + '\'>' +
          '<div class="blk-ref">' + (bw < 34 ? '' : (j.type === 'brk' ? '🍽' : esc(j.ref))) + '</div>' +
          '<div class="blk-sub">' + esc(j.addr || TYPE_LBL[j.type]) + ' · ' + dur(j.d) + '</div></div>';
        cursor = Math.max(cursor, j.s + j.d);
      });

      if (DAY_END - cursor >= 10) {
        var em = DAY_END - cursor;
        totalIdle += em;
        blocks += '<div class="idle" style="left:' + x(cursor) + 'px;width:' + (em * pxPerMin) + 'px">' +
          (em * pxPerMin > 52 ? '<span class="idle-lbl">' + dur(em) + ' idle</span>' : '') + '</div>';
      }
      totalBooked += booked;

      var pct = Math.round(booked / (DAY_END - DAY_START) * 100);
      html += '<div class="dv-row"><div class="rowhead">' +
        '<div class="crew-name">' + esc(crew.name) + '</div>' +
        '<div class="crew-people">' + esc(crew.people) + '</div>' +
        '<div class="crew-util ' + (pct < 55 ? 'low' : '') + '"><span style="width:' + pct + '%"></span></div>' +
        '</div><div class="lane" style="width:' + laneW + 'px">' + lines + blocks + '</div></div>';
    });

    var now = new Date(), nowM = now.getHours() * 60 + now.getMinutes();
    if (nowM > DAY_START && nowM < DAY_END) {
      html += '<div class="now" style="left:' + (124 + 16 + x(nowM)) + 'px"></div>';
    }

    grid.innerHTML = html;
    document.getElementById('dv-crews').textContent = CREWS.length;
    document.getElementById('dv-stops').textContent = stops;
    document.getElementById('dv-booked').textContent = dur(totalBooked);
    document.getElementById('dv-idle').textContent = dur(totalIdle);
  }

  function setZoom(z, fromX) {
    var sc = document.getElementById('dv-scroller');
    if (!sc) return;
    var at = (fromX == null) ? sc.clientWidth / 2 : fromX;
    var anchor = (sc.scrollLeft + at) / pxPerMin;
    pxPerMin = Math.min(8, Math.max(0.5, z));
    draw();
    sc.scrollLeft = anchor * pxPerMin - at;
    var btns = document.querySelectorAll('#dv-presets button');
    Array.prototype.forEach.call(btns, function (b) {
      b.classList.toggle('on', Math.abs(parseFloat(b.dataset.z) - pxPerMin) < 0.01);
    });
  }

  function showBlock(j) {
    document.getElementById('dv-p-title').textContent = j.type === 'brk' ? 'Break' : j.ref;
    document.getElementById('dv-p-sub').textContent = j.addr || TYPE_LBL[j.type];
    document.getElementById('dv-p-body').innerHTML =
      '<div class="p-row"><span>Crew</span><span>' + esc(j.crew) + '</span></div>' +
      '<div class="p-row"><span>Type</span><span>' + TYPE_LBL[j.type] + '</span></div>' +
      '<div class="p-row"><span>Start</span><span>' + fmt(j.s) + '</span></div>' +
      '<div class="p-row"><span>Finish</span><span>' + fmt(j.s + j.d) + '</span></div>' +
      '<div class="p-row"><span>On site</span><span>' + dur(j.d) + '</span></div>';
    document.getElementById('dv-panel').classList.add('open');
  }
  function closePanel() {
    var p = document.getElementById('dv-panel');
    if (p) p.classList.remove('open');
  }

  function wire() {
    var root = document.getElementById('dayview-root');
    var presets = document.getElementById('dv-presets');
    if (presets) presets.addEventListener('click', function (e) {
      if (e.target.dataset && e.target.dataset.z) setZoom(parseFloat(e.target.dataset.z));
    });
    document.getElementById('dv-in').addEventListener('click', function () { setZoom(pxPerMin * 1.35); });
    document.getElementById('dv-out').addEventListener('click', function () { setZoom(pxPerMin / 1.35); });
    document.getElementById('dv-panel-close').addEventListener('click', closePanel);

    root.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('.blk') : null;
      if (!b || !b.dataset.blk) return;
      try { showBlock(JSON.parse(b.dataset.blk)); } catch (err) { /* ignore */ }
    });

    var sc = document.getElementById('dv-scroller');
    sc.addEventListener('wheel', function (e) {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      setZoom(pxPerMin * (e.deltaY < 0 ? 1.12 : 1 / 1.12), e.clientX);
    }, { passive: false });

    var down = false, sx = 0, sl = 0;
    sc.addEventListener('mousedown', function (e) {
      if (e.target.closest && e.target.closest('.blk')) return;
      down = true; sx = e.pageX; sl = sc.scrollLeft; sc.style.cursor = 'grabbing';
    });
    window.addEventListener('mouseup', function () { down = false; sc.style.cursor = ''; });
    window.addEventListener('mousemove', function (e) {
      if (down) sc.scrollLeft = sl - (e.pageX - sx);
    });
    window.addEventListener('keydown', function (e) { if (e.key === 'Escape') closePanel(); });
  }

  function ensureStyles() {
    if (styled) return;
    var s = document.createElement('style');
    s.id = 'dayview-styles';
    s.textContent = CSS;
    document.head.appendChild(s);
    styled = true;
  }

  window.DayView = {
    render: function (dayData, dateObj) {
      var root = document.getElementById('dayview-root');
      if (!root) return;
      ensureStyles();
      if (!built) { root.innerHTML = MARKUP; built = true; wire(); }
      if (dateObj) {
        var el = document.getElementById('dv-date');
        if (el) el.textContent = dateObj.toLocaleDateString('en-AU',
          { weekday: 'long', day: 'numeric', month: 'long' });
      }
      shape(dayData || {});
      draw();
    }
  };
})();
