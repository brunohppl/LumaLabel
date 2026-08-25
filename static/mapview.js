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

  var TYPE_COL = {
    install: '#4a7c59', pickup: '#2e5a8a', to_load: '#b8935a',
    styling: '#6a3d8a', task: '#6a3d8a'
  };
  var TYPE_LBL = {
    install: 'Install', pickup: 'Pickup', to_load: 'To Load',
    styling: 'Styling', task: 'Task'
  };

  var map = null, layer = null, styled = false, loading = false;

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
    '#mapview-root .mv-pin{border-radius:50%;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.4);}',
    '#mapview-root .mv-pop-ref{font-weight:600;font-size:0.9rem;}',
    '#mapview-root .mv-pop-row{font-size:0.78rem;color:#555;margin-top:2px;}'
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
        '<span><i style="background:#4a7c59"></i>Install</span>' +
        '<span><i style="background:#2e5a8a"></i>Pickup</span>' +
        '<span><i style="background:#b8935a"></i>To Load</span>' +
        '<span><i style="background:#1a1714"></i>Warehouse</span>' +
      '</div>' +
      '<div class="mv-map" id="mv-map"></div>';
  }

  function dot(colour, size) {
    return window.L.divIcon({
      className: '',
      html: '<div class="mv-pin" style="width:' + size + 'px;height:' + size +
            'px;background:' + colour + ';"></div>',
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2]
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
      L.marker([data.warehouse.lat, data.warehouse.lng], { icon: dot('#1a1714', 16) })
        .bindPopup('<div class="mv-pop-ref">Warehouse</div>' +
                   '<div class="mv-pop-row">' + esc(data.warehouse.address) + '</div>')
        .addTo(layer);
      bounds.push([data.warehouse.lat, data.warehouse.lng]);
    }

    (data.points || []).forEach(function (p) {
      var colour = TYPE_COL[p.type] || '#9a8f80';
      L.marker([p.lat, p.lng], { icon: dot(colour, 14) })
        .bindPopup(
          '<div class="mv-pop-ref">' + esc(p.ref) + '</div>' +
          '<div class="mv-pop-row">' + esc(p.address) + '</div>' +
          '<div class="mv-pop-row">' + (TYPE_LBL[p.type] || esc(p.type)) +
            (p.time ? ' · ' + esc(p.time) : '') +
            (p.crew ? ' · ' + esc(p.crew) : '') + '</div>')
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

  window.MapView = {
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
