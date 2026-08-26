/* LUMA — map tab on the team view.
 *
 * A separate file for the same reason as dayview.js: script tags are parsed
 * independently, so a fault in here cannot break the card view crews rely on.
 *
 * Leaflet + OpenStreetMap tiles, loaded from a CDN on first use. No Google
 * key is exposed to the browser — geocoding happens server-side and the
 * coordinates arrive already resolved.
 */
(function () {
  'use strict';

  var LEAFLET_CSS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
  var LEAFLET_JS  = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';

  // Installs and pickups only — the map shows where crews go to a property.
  var TYPE_COL = { install: '#4a7c59', pickup: '#2e5a8a' };
  var TYPE_LBL = { install: 'Install', pickup: 'Pickup' };

  var map = null, layer = null, styled = false, loading = false;
  var points = [];      // kept so popup buttons can refer to a point by index

  var CSS = [
    '#mapview-root{padding-bottom:20px;}',
    '#mapview-root .mv-bar{padding:12px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;border-bottom:1px solid var(--border,#e0d8ce);background:#fff;}',
    '#mapview-root .mv-date{font-family:"Cormorant Garamond",serif;font-size:1.3rem;}',
    '#mapview-root .mv-note{font-size:0.7rem;color:var(--muted,#9a8f80);}',
    '#mapview-root .mv-legend{display:flex;gap:12px;flex-wrap:wrap;padding:8px 16px;font-size:0.68rem;color:var(--muted,#9a8f80);}',
    '#mapview-root .mv-legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;font-style:normal;}',
    '#mapview-root .mv-map{height:62vh;min-height:340px;margin:0 16px;border:1px solid var(--border,#e0d8ce);border-radius:6px;}',
    '@media(min-width:1100px){#mapview-root .mv-map{height:70vh;}}',
    '#mapview-root .mv-msg{padding:36px 20px;text-align:center;color:var(--muted,#9a8f80);font-size:0.85rem;line-height:1.6;}',
    '#mapview-root .mv-icon{background:none;border:none;}',
    '#mapview-root .mv-label{position:absolute;top:100%;left:50%;transform:translateX(-50%);margin-top:1px;',
    '  background:rgba(255,255,255,0.94);border:1px solid rgba(0,0,0,0.12);border-radius:3px;',
    '  padding:1px 5px;font-family:Jost,sans-serif;font-size:10px;font-weight:600;color:#1a1714;',
    '  white-space:nowrap;pointer-events:none;}',
    '#mapview-root .mv-label-wh{font-weight:600;}',
    '#mapview-root .mv-pop-ref{font-weight:600;font-size:0.9rem;}',
    '#mapview-root .mv-pop-row{font-size:0.78rem;color:#555;margin-top:2px;}',
    '.mv-nav{margin-top:8px;width:100%;padding:7px 10px;background:#b8935a;color:#fff;border:none;',
    '  border-radius:5px;font-family:Jost,sans-serif;font-size:0.78rem;font-weight:500;',
    '  letter-spacing:0.04em;cursor:pointer;}',
    '.mv-nav:hover{filter:brightness(1.07);}'
  ].join('\n');

  function ensureStyles() {
    if (styled) return;
    var s = document.createElement('style');
    s.id = 'mapview-styles';
    s.textContent = CSS;
    document.head.appendChild(s);
    styled = true;
  }

  /* Load Leaflet once, on first use — not on every page load, since most
     visits to the team view never open this tab. */
  function ensureLeaflet(cb) {
    if (window.L) { cb(null); return; }
    if (loading) { setTimeout(function () { ensureLeaflet(cb); }, 120); return; }
    loading = true;

    if (!document.getElementById('leaflet-css')) {
      var link = document.createElement('link');
      link.id = 'leaflet-css';
      link.rel = 'stylesheet';
      link.href = LEAFLET_CSS;
      document.head.appendChild(link);
    }
    var s = document.createElement('script');
    s.src = LEAFLET_JS;
    s.onload = function () { loading = false; cb(null); };
    s.onerror = function () { loading = false; cb(new Error('Could not load the map library.')); };
    document.head.appendChild(s);
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function msg(html) {
    var root = document.getElementById('mapview-root');
    if (root) root.innerHTML = '<div class="mv-msg">' + html + '</div>';
  }

  function shell(dateObj) {
    var root = document.getElementById('mapview-root');
    root.innerHTML =
      '<div class="mv-bar"><div class="mv-date" id="mv-date">' +
        (dateObj ? esc(dateObj.toLocaleDateString('en-AU',
          { weekday: 'long', day: 'numeric', month: 'long' })) : '') +
      '</div><div class="mv-note" id="mv-note"></div></div>' +
      '<div class="mv-legend">' +
        '<span><i style="background:#4a7c59"></i>Install (I)</span>' +
        '<span><i style="background:#2e5a8a"></i>Pickup (P)</span>' +
        '<span><i style="background:#1a1714"></i>Warehouse</span>' +
        '<span>Label under each pin is the crew</span>' +
      '</div>' +
      '<div class="mv-map" id="mv-map"></div>';
  }

  /* A teardrop pin, coloured by type, with the crew name on a small label
     beneath it — so the map answers "which truck is where" at a glance
     without tapping anything. */
  function jobIcon(type, crew) {
    var colour = TYPE_COL[type] || '#9a8f80';
    var letter = type === 'pickup' ? 'P' : 'I';
    var label = crew
      ? '<div class="mv-label">' + esc(crew) + '</div>'
      : '';
    return window.L.divIcon({
      className: 'mv-icon',
      html:
        '<svg width="26" height="34" viewBox="0 0 26 34">' +
          '<path d="M13 33C13 33 24 20.5 24 13A11 11 0 1 0 2 13C2 20.5 13 33 13 33Z" ' +
            'fill="' + colour + '" stroke="#fff" stroke-width="2"/>' +
          '<text x="13" y="17" text-anchor="middle" font-family="Jost,sans-serif" ' +
            'font-size="11" font-weight="600" fill="#fff">' + letter + '</text>' +
        '</svg>' + label,
      iconSize: [26, 34],
      iconAnchor: [13, 33],
      popupAnchor: [0, -30]
    });
  }

  /* The warehouse is a place, not a job — a house, visually distinct. */
  function warehouseIcon() {
    return window.L.divIcon({
      className: 'mv-icon',
      html:
        '<svg width="32" height="32" viewBox="0 0 32 32">' +
          '<circle cx="16" cy="16" r="14" fill="#1a1714" stroke="#fff" stroke-width="2"/>' +
          '<path d="M9 16.5L16 10l7 6.5V23a1 1 0 0 1-1 1h-4v-5h-4v5h-4a1 1 0 0 1-1-1z" ' +
            'fill="#fff"/>' +
        '</svg><div class="mv-label mv-label-wh">Warehouse</div>',
      iconSize: [32, 32],
      iconAnchor: [16, 16],
      popupAnchor: [0, -16]
    });
  }

  function plot(data) {
    var L = window.L;
    var el = document.getElementById('mv-map');
    if (!el) return;

    // shell() rebuilds the container on each render, so a cached map would
    // still be bound to the discarded element and draw nowhere.
    if (map && map.getContainer && map.getContainer() !== el) {
      try { map.remove(); } catch (e) { /* already gone */ }
      map = null;
      layer = null;
    }
    if (!map) {
      map = L.map(el, { scrollWheelZoom: true });
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors'
      }).addTo(map);
    } else {
      map.invalidateSize();
    }
    if (layer) { map.removeLayer(layer); }
    layer = L.layerGroup().addTo(map);

    var bounds = [];

    if (data.warehouse) {
      L.marker([data.warehouse.lat, data.warehouse.lng], { icon: warehouseIcon() })
        .bindPopup('<div class="mv-pop-ref">Warehouse</div>' +
                   '<div class="mv-pop-row">' + esc(data.warehouse.address) + '</div>' +
                   '<button class="mv-nav" onclick="MapView.navTo(' +
                     JSON.stringify(data.warehouse.address) + ')">📍 Navigate</button>')
        .addTo(layer);
      bounds.push([data.warehouse.lat, data.warehouse.lng]);
    }

    points = (data.points || []).slice();
    points.forEach(function (p, i) {
      L.marker([p.lat, p.lng], { icon: jobIcon(p.type, p.crew) })
        .bindPopup(
          '<div class="mv-pop-ref">' + esc(p.ref) + '</div>' +
          '<div class="mv-pop-row">' + esc(p.address) + '</div>' +
          '<div class="mv-pop-row">' + (TYPE_LBL[p.type] || esc(p.type)) +
            (p.time ? ' · ' + esc(p.time) : '') +
            (p.crew ? ' · ' + esc(p.crew) : '') + '</div>' +
          '<button class="mv-nav" onclick="MapView.nav(' + i + ')">📍 Navigate</button>')
        .addTo(layer);
      bounds.push([p.lat, p.lng]);
    });

    if (bounds.length > 1) {
      map.fitBounds(bounds, { padding: [40, 40] });
    } else if (bounds.length === 1) {
      map.setView(bounds[0], 12);
    } else {
      map.setView([-27.47, 153.02], 10);      // Brisbane, so the tab is never blank
    }

    var note = document.getElementById('mv-note');
    if (note) {
      var n = (data.points || []).length;
      var bits = [n + (n === 1 ? ' stop' : ' stops') + ' mapped'];
      if (data.unmapped) bits.push(data.unmapped + ' without a location yet');
      if (!data.warehouse) bits.push('warehouse not resolved');
      note.textContent = bits.join(' · ');
    }
  }

  /* Straight to Maps — no Slack prompt. The map is for orienting quickly
     and rescuing a job that's gone wrong; the card view keeps the ETA flow
     for the planned run. */
  function openDirections(address) {
    window.open('https://www.google.com/maps/search/?api=1&query=' +
                encodeURIComponent(address || ''), '_blank');
  }

  window.MapView = {
    nav: function (i) {
      var p = points[i];
      if (!p) return;
      openDirections(p.address);
    },
    navTo: function (address) {
      openDirections(address);
    },
    render: function (dateStr, dateObj) {
      var root = document.getElementById('mapview-root');
      if (!root) return;
      ensureStyles();
      shell(dateObj);
      var mapEl = document.getElementById('mv-map');
      mapEl.innerHTML = '<div class="mv-msg">Loading the map…</div>';

      ensureLeaflet(function (err) {
        if (err) {
          msg('Could not load the map library.<br>Check the connection and try again — ' +
              'the Cards tab is unaffected.');
          return;
        }
        fetch('/api/map/' + dateStr)
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (!data.geocoding_available) {
              msg('Map locations need the Google Maps key to be configured on the server.');
              return;
            }
            shell(dateObj);
            plot(data);
            if (!(data.points || []).length) {
              var note = document.getElementById('mv-note');
              if (note) {
                note.textContent = data.unmapped
                  ? data.unmapped + ' stop(s) scheduled but no location resolved yet — reopen to continue'
                  : 'Nothing scheduled for this day';
              }
            }
          })
          .catch(function () {
            msg('Could not load the day’s locations.<br>The Cards tab is unaffected.');
          });
      });
    }
  };
})();
