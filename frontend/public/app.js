/* Listing Scraper - plain JS frontend.
   Rules (strict):
   - ASCII only in source strings (no em dash, unicode bullets, etc).
   - Use createElement + textContent for any user/API data.
   - addEventListener only (no inline onclick on dynamic nodes).
   - XMLHttpRequest for API calls (no fetch).
*/

(function () {
  var BACKEND = (window && window.__BACKEND_URL__) || (function () {
    // Use REACT_APP_BACKEND_URL replacement done at build time? We don't have that here.
    // Instead, use same origin since /api routes are proxied through ingress.
    return window.location.origin;
  })();

  // ---------- HTTP helpers ----------
  function xhr(method, path, body, opts) {
    opts = opts || {};
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      var url = BACKEND + path;
      x.open(method, url, true);
      if (body && !opts.raw) x.setRequestHeader('Content-Type', 'application/json');
      if (opts.responseType) x.responseType = opts.responseType;
      x.onload = function () {
        if (x.status >= 200 && x.status < 300) {
          if (opts.responseType === 'blob') return resolve(x.response);
          var data = x.responseText;
          try { data = data ? JSON.parse(data) : null; } catch (e) { /* keep text */ }
          resolve(data);
        } else {
          var err;
          try { err = JSON.parse(x.responseText); } catch (e) { err = { detail: x.responseText || ('HTTP ' + x.status) }; }
          reject(err);
        }
      };
      x.onerror = function () { reject({ detail: 'Network error' }); };
      x.send(body ? (opts.raw ? body : JSON.stringify(body)) : null);
    });
  }

  function api(method, path, body) { return xhr(method, path, body); }

  // ---------- Toast ----------
  var toastRoot = null;
  function toast(msg, kind) {
    if (!toastRoot) toastRoot = document.getElementById('toast-root');
    var el = document.createElement('div');
    el.className = 'toast ' + (kind || 'info');
    el.textContent = msg;
    toastRoot.appendChild(el);
    setTimeout(function () {
      el.style.opacity = '0';
      el.style.transition = 'opacity 0.4s';
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 450);
    }, 3500);
  }

  // ---------- DOM helpers ----------
  function el(tag, props, children) {
    var n = document.createElement(tag);
    if (props) {
      for (var k in props) {
        if (k === 'class') n.className = props[k];
        else if (k === 'text') n.textContent = props[k];
        else if (k === 'data') {
          for (var dk in props[k]) n.setAttribute('data-' + dk, props[k][dk]);
        } else if (k.indexOf('on') === 0 && typeof props[k] === 'function') {
          n.addEventListener(k.slice(2).toLowerCase(), props[k]);
        } else if (k === 'attrs') {
          for (var ak in props[k]) n.setAttribute(ak, props[k][ak]);
        } else {
          n[k] = props[k];
        }
      }
    }
    if (children) {
      for (var i = 0; i < children.length; i++) {
        var c = children[i];
        if (c == null) continue;
        if (typeof c === 'string') n.appendChild(document.createTextNode(c));
        else n.appendChild(c);
      }
    }
    return n;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function fmtTime(iso) {
    if (!iso) return '-';
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      var y = d.getFullYear();
      var m = String(d.getMonth() + 1).padStart(2, '0');
      var dd = String(d.getDate()).padStart(2, '0');
      var hh = String(d.getHours()).padStart(2, '0');
      var mm = String(d.getMinutes()).padStart(2, '0');
      return y + '-' + m + '-' + dd + ' ' + hh + ':' + mm;
    } catch (e) { return iso; }
  }

  // ---------- Modal ----------
  function showModal(opts) {
    var root = document.getElementById('modal-root');
    clear(root);
    var backdrop = el('div', { class: 'modal-backdrop' });
    backdrop.addEventListener('click', function (e) { if (e.target === backdrop) close(); });
    var modal = el('div', { class: 'modal' });
    var head = el('div', { class: 'modal-head' }, [
      el('h3', { text: opts.title || 'Confirm' }),
      el('button', { class: 'btn btn-ghost btn-sm', text: 'X', onclick: close })
    ]);
    var bodyWrap = el('div', { class: 'modal-body' });
    if (opts.body) bodyWrap.appendChild(opts.body);
    var footActions = (opts.actions || []).map(function (a) {
      return el('button', {
        class: 'btn ' + (a.variant === 'primary' ? 'btn-primary' : (a.variant === 'danger' ? 'btn-danger' : 'btn-secondary')),
        text: a.label,
        onclick: function () {
          if (a.onClick) {
            var r = a.onClick();
            if (r && r.then) r.then(function (v) { if (v !== false) close(); }).catch(function () {});
            else if (r !== false) close();
          } else close();
        },
        attrs: a.testid ? { 'data-testid': a.testid } : {}
      });
    });
    var foot = el('div', { class: 'modal-foot' }, footActions);
    modal.appendChild(head); modal.appendChild(bodyWrap); modal.appendChild(foot);
    backdrop.appendChild(modal);
    root.appendChild(backdrop);
    function close() { clear(root); }
    return { close: close };
  }

  function confirmModal(message, onYes) {
    showModal({
      title: 'Confirm',
      body: el('p', { text: message }),
      actions: [
        { label: 'Cancel' },
        { label: 'Confirm', variant: 'danger', onClick: onYes }
      ]
    });
  }

  // ---------- App State ----------
  var state = {
    view: 'home',
    brands: [],
    currentBrand: null,
    pollHandle: null
  };

  // ---------- Home View ----------
  function renderHome() {
    document.getElementById('view-home').hidden = false;
    document.getElementById('view-brand').hidden = true;
    state.view = 'home';
    refreshBrands();
  }

  function refreshBrands() {
    api('GET', '/api/brands').then(function (brands) {
      state.brands = brands || [];
      drawBrandGrid();
    }).catch(function (e) {
      toast('Failed to load brands: ' + (e.detail || ''), 'error');
    });
  }

  function drawBrandGrid() {
    var grid = document.getElementById('brand-grid');
    clear(grid);
    if (!state.brands.length) {
      grid.appendChild(el('div', { class: 'empty', text: 'No brands yet. Create one below.' }));
      return;
    }
    state.brands.forEach(function (b) {
      var card = el('div', { class: 'brand-card', attrs: { 'data-testid': 'brand-card-' + b.name } });
      card.addEventListener('click', function () { openBrand(b.name); });

      var del = el('button', { class: 'btn btn-danger btn-sm delete-btn', text: 'Delete', attrs: { 'data-testid': 'btn-delete-brand-' + b.name } });
      del.addEventListener('click', function (ev) {
        ev.stopPropagation();
        confirmModal('Delete brand "' + b.name + '"? This removes all its ASINs, pincodes, and results.', function () {
          return api('DELETE', '/api/brands/' + encodeURIComponent(b.name)).then(function () {
            toast('Brand deleted', 'success');
            refreshBrands();
          }).catch(function (e) { toast('Delete failed: ' + (e.detail || ''), 'error'); return false; });
        });
      });
      card.appendChild(del);

      card.appendChild(el('div', { class: 'brand-card-name', text: b.name }));

      var stats = el('div', { class: 'brand-card-stats' }, [
        el('div', { class: 'stat' }, [
          el('span', { class: 'stat-label', text: 'ASINs' }),
          el('span', { class: 'stat-value', text: String(b.asin_count || 0) })
        ]),
        el('div', { class: 'stat' }, [
          el('span', { class: 'stat-label', text: 'Pincodes' }),
          el('span', { class: 'stat-value', text: String(b.pincode_count || 0) })
        ]),
        el('div', { class: 'stat' }, [
          el('span', { class: 'stat-label', text: 'In Stock' }),
          el('span', { class: 'stat-value', text: String(b.in_stock_count || 0) })
        ]),
        el('div', { class: 'stat' }, [
          el('span', { class: 'stat-label', text: 'Status' }),
          el('span', { class: 'stat-value', text: b.is_scraping ? 'Scraping...' : 'Idle' })
        ])
      ]);
      card.appendChild(stats);
      card.appendChild(el('div', { class: 'brand-card-foot' }, [
        el('span', { class: 'last-scraped', text: 'Last scraped: ' + fmtTime(b.last_scraped) })
      ]));
      grid.appendChild(card);
    });
  }

  // Create brand
  document.getElementById('btn-create-brand').addEventListener('click', function () {
    var input = document.getElementById('new-brand-name');
    var name = (input.value || '').trim();
    if (!name) { toast('Enter brand name', 'error'); return; }
    api('POST', '/api/brands', { name: name }).then(function () {
      input.value = '';
      toast('Brand created', 'success');
      refreshBrands();
    }).catch(function (e) { toast('Create failed: ' + (e.detail || ''), 'error'); });
  });

  document.getElementById('new-brand-name').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') document.getElementById('btn-create-brand').click();
  });

  // Scrape all
  document.getElementById('btn-scrape-all').addEventListener('click', function () {
    confirmModal('Start scraping ALL brands using their saved pincodes?', function () {
      return api('POST', '/api/scrape-all').then(function (r) {
        toast('Started: ' + (r.started || []).join(', ') + (r.skipped && r.skipped.length ? ' | Skipped: ' + r.skipped.join(', ') : ''), 'success');
        refreshBrands();
      }).catch(function (e) { toast('Scrape-all failed: ' + (e.detail || ''), 'error'); return false; });
    });
  });

  // ---------- Brand Detail View ----------
  function openBrand(name) {
    state.view = 'brand';
    document.getElementById('view-home').hidden = true;
    document.getElementById('view-brand').hidden = false;
    loadBrandDetail(name);
  }

  function loadBrandDetail(name) {
    api('GET', '/api/brands/' + encodeURIComponent(name)).then(function (b) {
      state.currentBrand = b;
      drawBrandDetail();
      maybeStartPolling();
    }).catch(function (e) {
      toast('Failed to load brand: ' + (e.detail || ''), 'error');
    });
  }

  function maybeStartPolling() {
    stopPolling();
    if (!state.currentBrand) return;
    var name = state.currentBrand.name;
    state.pollHandle = setInterval(function () {
      api('GET', '/api/brands/' + encodeURIComponent(name) + '/status').then(function (s) {
        var box = document.getElementById('progress-card');
        if (!box) return;
        renderProgress(box, s);
        if (!s.isScraping && state.lastScraping) {
          // Just finished -> reload
          state.lastScraping = false;
          loadBrandDetail(name);
        } else {
          state.lastScraping = !!s.isScraping;
        }
      }).catch(function () {});
    }, 3000);
  }

  function stopPolling() {
    if (state.pollHandle) { clearInterval(state.pollHandle); state.pollHandle = null; }
  }

  function renderProgress(host, s) {
    clear(host);
    if (!s || !s.isScraping) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    host.appendChild(el('div', { class: 'progress-label', attrs: { 'data-testid': 'progress-label' }, text: s.progress.label || 'Working...' }));
    var pct = s.progress.total > 0 ? Math.floor((s.progress.current / s.progress.total) * 100) : 0;
    var bar = el('div', { class: 'progress-bar' }, [
      el('div', { class: 'progress-fill', attrs: { style: 'width:' + pct + '%' } })
    ]);
    host.appendChild(bar);
    host.appendChild(el('div', { class: 'small text-muted mb-8', text: s.progress.current + ' of ' + s.progress.total + ' (' + pct + '%)' }));
    var logBox = el('div', { class: 'log-box', attrs: { 'data-testid': 'progress-log' } });
    (s.logs || []).slice(-50).forEach(function (line) {
      var cls = 'log-line';
      if (/error|fatal/i.test(line)) cls += ' err';
      else if (/^\d{2}:\d{2}:\d{2}\s-\sOK\s/.test(line)) cls += ' ok';
      logBox.appendChild(el('div', { class: cls, text: line }));
    });
    host.appendChild(logBox);
    logBox.scrollTop = logBox.scrollHeight;
  }

  function drawBrandDetail() {
    var b = state.currentBrand;
    var root = document.getElementById('view-brand');
    clear(root);

    // Header
    var backBtn = el('button', { class: 'back-link', text: '< Back', attrs: { 'data-testid': 'btn-back' } });
    backBtn.addEventListener('click', function () { stopPolling(); state.currentBrand = null; renderHome(); });

    var head = el('div', { class: 'detail-head' }, [
      backBtn,
      el('div', { class: 'detail-title', text: b.name }),
      (function () {
        var actions = el('div', { class: 'detail-actions' });
        var scrapeBtn = el('button', { class: 'btn btn-primary', text: 'Scrape This Brand', attrs: { 'data-testid': 'btn-scrape-brand' } });
        scrapeBtn.addEventListener('click', openPincodeSelectionModal);
        var exportBtn = el('button', { class: 'btn btn-secondary', text: 'Export Results CSV', attrs: { 'data-testid': 'btn-export-csv' } });
        exportBtn.addEventListener('click', function () {
          window.location.href = '/api/brands/' + encodeURIComponent(b.name) + '/csv';
        });
        actions.appendChild(scrapeBtn); actions.appendChild(exportBtn);
        return actions;
      })()
    ]);
    root.appendChild(head);

    // Stats bar
    var inStock = 0, outOfStock = 0, ratings = [];
    (b.results || []).forEach(function (r) {
      if (r.stock === 'In Stock') inStock++;
      else if (r.stock === 'Out of Stock') outOfStock++;
      var rv = parseFloat(r.rating);
      if (!isNaN(rv)) ratings.push(rv);
    });
    var avg = ratings.length ? (ratings.reduce(function (a, c) { return a + c; }, 0) / ratings.length).toFixed(2) : '-';
    var lastScraped = (b.results || []).reduce(function (acc, r) {
      return (!acc || (r.scraped_at && r.scraped_at > acc)) ? r.scraped_at : acc;
    }, '');

    var stats = el('div', { class: 'stats-bar' }, [
      statCard('Total ASINs', String((b.asins || []).length)),
      statCard('In Stock', String(inStock)),
      statCard('Out of Stock', String(outOfStock)),
      statCard('Avg Rating', avg),
      statCard('Last Scraped', fmtTime(lastScraped))
    ]);
    root.appendChild(stats);

    // Progress card (hidden until scraping)
    var progressCard = el('div', { class: 'progress-card', id: 'progress-card', hidden: true });
    root.appendChild(progressCard);
    if (b.job && b.job.isScraping) {
      progressCard.hidden = false;
      renderProgress(progressCard, b.job);
      state.lastScraping = true;
    }

    // Pincode manager
    root.appendChild(buildPincodeManager(b));
    // ASIN manager
    root.appendChild(buildAsinManager(b));
    // Results table
    root.appendChild(buildResultsSection(b));
    // Scrape History
    root.appendChild(buildHistorySection(b));
    // Compare view (tabs A and B)
    root.appendChild(buildCompareSection(b));
  }

  function statCard(label, value) {
    return el('div', { class: 'stat-card' }, [
      el('div', { class: 'stat-label', text: label }),
      el('div', { class: 'stat-value', text: value })
    ]);
  }

  // Pincode manager
  function buildPincodeManager(b) {
    var section = el('div', { class: 'section', attrs: { 'data-testid': 'section-pincodes' } });
    section.appendChild(el('div', { class: 'section-head' }, [
      el('h3', { class: 'section-title', text: 'Delivery Pincodes' })
    ]));
    var listWrap = el('div', { class: 'pincode-list' });
    if (!b.pincodes.length) listWrap.appendChild(el('div', { class: 'empty', text: 'No pincodes yet. Add one below.' }));
    b.pincodes.forEach(function (p) {
      var pill = el('span', { class: 'pincode-pill', attrs: { 'data-testid': 'pincode-pill-' + p.code } }, [
        document.createTextNode(p.code + ' - ' + p.city + ' '),
        (function () {
          var x = el('button', { class: 'x', text: 'x' });
          x.addEventListener('click', function () {
            confirmModal('Remove pincode ' + p.code + '?', function () {
              return api('DELETE', '/api/brands/' + encodeURIComponent(b.name) + '/pincodes/' + p.code).then(function () {
                toast('Pincode removed', 'success');
                loadBrandDetail(b.name);
              }).catch(function (e) { toast('Failed: ' + (e.detail || ''), 'error'); return false; });
            });
          });
          return x;
        })()
      ]);
      listWrap.appendChild(pill);
    });
    section.appendChild(listWrap);

    var codeInput = el('input', { class: 'input', type: 'text', maxLength: 6, placeholder: '6-digit pincode (e.g. 110001)', attrs: { 'data-testid': 'input-pincode-code' } });
    var cityInput = el('input', { class: 'input', type: 'text', placeholder: 'City (e.g. Delhi)', attrs: { 'data-testid': 'input-pincode-city' } });
    var addBtn = el('button', { class: 'btn btn-primary', text: 'Add Pincode', attrs: { 'data-testid': 'btn-add-pincode' } });
    addBtn.addEventListener('click', function () {
      var code = (codeInput.value || '').trim();
      var city = (cityInput.value || '').trim();
      if (!/^\d{6}$/.test(code)) { toast('Pincode must be exactly 6 digits', 'error'); return; }
      if (!city) { toast('City is required', 'error'); return; }
      api('POST', '/api/brands/' + encodeURIComponent(b.name) + '/pincodes', { code: code, city: city })
        .then(function () { toast('Pincode added', 'success'); codeInput.value = ''; cityInput.value = ''; loadBrandDetail(b.name); })
        .catch(function (e) { toast('Failed: ' + (e.detail || ''), 'error'); });
    });
    section.appendChild(el('div', { class: 'row' }, [codeInput, cityInput, addBtn]));
    return section;
  }

  // ASIN manager
  function buildAsinManager(b) {
    var section = el('div', { class: 'section', attrs: { 'data-testid': 'section-asins' } });
    section.appendChild(el('div', { class: 'section-head' }, [
      el('h3', { class: 'section-title', text: 'Products (ASINs)' }),
      (function () {
        var wrap = el('div', { class: 'row' });
        var dl = el('button', { class: 'btn btn-secondary btn-sm', text: 'Download ASIN List (CSV)', attrs: { 'data-testid': 'btn-download-asins' } });
        dl.addEventListener('click', function () { window.location.href = '/api/brands/' + encodeURIComponent(b.name) + '/download'; });
        wrap.appendChild(dl);
        return wrap;
      })()
    ]));

    // Upload area
    var upload = el('label', { class: 'upload-area', attrs: { 'data-testid': 'upload-area' } });
    upload.appendChild(document.createTextNode('Click to upload .xlsx or .csv (replaces entire ASIN list)'));
    var fileInput = el('input', { type: 'file', accept: '.xlsx,.csv,.xls' });
    upload.appendChild(fileInput);
    var uploadStatus = el('div', { class: 'upload-status' });
    upload.appendChild(uploadStatus);
    fileInput.addEventListener('change', function () {
      var f = fileInput.files && fileInput.files[0];
      if (!f) return;
      handleFileUpload(b.name, f, uploadStatus);
    });
    upload.addEventListener('dragover', function (e) { e.preventDefault(); upload.classList.add('dragover'); });
    upload.addEventListener('dragleave', function () { upload.classList.remove('dragover'); });
    upload.addEventListener('drop', function (e) {
      e.preventDefault(); upload.classList.remove('dragover');
      var f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) handleFileUpload(b.name, f, uploadStatus);
    });
    section.appendChild(upload);

    // Add single ASIN
    var asinInput = el('input', { class: 'input', type: 'text', maxLength: 10, placeholder: 'ASIN (10 chars)', attrs: { 'data-testid': 'input-asin' } });
    var notesInput = el('input', { class: 'input', type: 'text', placeholder: 'Notes (optional)', attrs: { 'data-testid': 'input-asin-notes' } });
    var addBtn = el('button', { class: 'btn btn-primary', text: 'Add ASIN', attrs: { 'data-testid': 'btn-add-asin' } });
    addBtn.addEventListener('click', function () {
      var asin = (asinInput.value || '').trim().toUpperCase();
      if (!/^[A-Z0-9]{10}$/.test(asin)) { toast('ASIN must be 10 alphanumeric characters', 'error'); return; }
      api('POST', '/api/brands/' + encodeURIComponent(b.name) + '/asins', { asin: asin, notes: (notesInput.value || '').trim() })
        .then(function () { toast('ASIN added', 'success'); asinInput.value = ''; notesInput.value = ''; loadBrandDetail(b.name); })
        .catch(function (e) { toast('Failed: ' + (e.detail || ''), 'error'); });
    });
    section.appendChild(el('div', { class: 'row mt-12' }, [asinInput, notesInput, addBtn]));

    // Products table
    if (!b.asins.length) {
      section.appendChild(el('div', { class: 'empty', text: 'No products yet. Add ASINs above.' }));
    } else {
      var selected = {};
      var tableWrap = el('div', { class: 'table-wrap mt-12' });
      var table = el('table', { class: 'data' });
      var head = el('thead', null, [
        el('tr', null, [
          el('th', null, [
            (function () {
              var cb = el('input', { type: 'checkbox', attrs: { 'data-testid': 'asin-check-all' } });
              cb.addEventListener('change', function () {
                var all = table.querySelectorAll('tbody input.asin-check');
                Array.prototype.forEach.call(all, function (x) { x.checked = cb.checked; selected[x.getAttribute('data-asin')] = cb.checked; });
              });
              return cb;
            })()
          ]),
          el('th', { text: 'ASIN' }),
          el('th', { text: 'Notes' }),
          el('th', { text: 'Added' }),
          el('th', { text: 'Action' })
        ])
      ]);
      var body = el('tbody');
      b.asins.forEach(function (a) {
        var tr = el('tr', { attrs: { 'data-testid': 'asin-row-' + a.asin } }, [
          el('td', null, [
            (function () {
              var cb = el('input', { type: 'checkbox', class: 'asin-check', attrs: { 'data-asin': a.asin } });
              cb.addEventListener('change', function () { selected[a.asin] = cb.checked; });
              return cb;
            })()
          ]),
          el('td', { text: a.asin }),
          el('td', { text: a.notes || '' }),
          el('td', { text: fmtTime(a.created_at) }),
          el('td', null, [
            (function () {
              var del = el('button', { class: 'btn btn-ghost btn-sm', text: 'Remove' });
              del.addEventListener('click', function () {
                confirmModal('Remove ASIN ' + a.asin + '?', function () {
                  return api('DELETE', '/api/brands/' + encodeURIComponent(b.name) + '/asins/' + a.asin)
                    .then(function () { toast('ASIN removed', 'success'); loadBrandDetail(b.name); })
                    .catch(function (e) { toast('Failed: ' + (e.detail || ''), 'error'); return false; });
                });
              });
              return del;
            })()
          ])
        ]);
        body.appendChild(tr);
      });
      table.appendChild(head); table.appendChild(body);
      tableWrap.appendChild(table);
      section.appendChild(tableWrap);

      var bulkRm = el('button', { class: 'btn btn-danger btn-sm mt-12', text: 'Remove Selected', attrs: { 'data-testid': 'btn-remove-selected-asins' } });
      bulkRm.addEventListener('click', function () {
        var picks = Object.keys(selected).filter(function (k) { return selected[k]; });
        if (!picks.length) { toast('Nothing selected', 'error'); return; }
        confirmModal('Remove ' + picks.length + ' selected ASIN(s)?', function () {
          var promises = picks.map(function (asin) {
            return api('DELETE', '/api/brands/' + encodeURIComponent(b.name) + '/asins/' + asin);
          });
          return Promise.all(promises).then(function () {
            toast('Removed ' + picks.length + ' ASIN(s)', 'success');
            loadBrandDetail(b.name);
          }).catch(function (e) { toast('Some removals failed: ' + (e.detail || ''), 'error'); return false; });
        });
      });
      section.appendChild(bulkRm);
    }

    return section;
  }

  function handleFileUpload(brandName, file, statusEl) {
    statusEl.textContent = 'Reading ' + file.name + '...';
    var reader = new FileReader();
    reader.onload = function (e) {
      try {
        var data = new Uint8Array(e.target.result);
        var workbook = XLSX.read(data, { type: 'array' });
        var sheet = workbook.Sheets[workbook.SheetNames[0]];
        var rows = XLSX.utils.sheet_to_json(sheet, { defval: '' });
        var asins = [];
        var skipped = 0;
        rows.forEach(function (row) {
          var asin = '';
          var notes = '';
          for (var k in row) {
            var lk = String(k).toLowerCase().trim();
            if (lk === 'asin' || lk === 'asins' || lk === 'product asin') asin = String(row[k]).trim().toUpperCase();
            else if (lk === 'notes' || lk === 'note' || lk === 'description') notes = String(row[k]).trim();
          }
          if (!asin) {
            // try first column as ASIN if no header match
            var keys = Object.keys(row);
            if (keys.length >= 1) asin = String(row[keys[0]]).trim().toUpperCase();
            if (keys.length >= 2 && !notes) notes = String(row[keys[1]]).trim();
          }
          if (/^[A-Z0-9]{10}$/.test(asin)) asins.push({ asin: asin, notes: notes });
          else if (asin) skipped++;
        });
        if (!asins.length) {
          statusEl.textContent = 'No valid ASINs found in file. Each row needs a 10-character ASIN in column "ASIN".';
          return;
        }
        statusEl.textContent = 'Uploading ' + asins.length + ' ASIN(s)...';
        api('POST', '/api/brands/' + encodeURIComponent(brandName) + '/upload', { asins: asins })
          .then(function (r) {
            statusEl.textContent = 'Added ' + r.added + ', skipped ' + (r.skipped + skipped) + '. List replaced.';
            toast('Uploaded ' + r.added + ' ASIN(s)', 'success');
            loadBrandDetail(brandName);
          })
          .catch(function (e) { statusEl.textContent = 'Upload failed: ' + (e.detail || ''); toast('Upload failed', 'error'); });
      } catch (err) {
        statusEl.textContent = 'Could not parse file: ' + (err.message || err);
      }
    };
    reader.readAsArrayBuffer(file);
  }

  // Results table
  function buildResultsSection(b) {
    var section = el('div', { class: 'section', attrs: { 'data-testid': 'section-results' } });
    section.appendChild(el('div', { class: 'section-head' }, [
      el('h3', { class: 'section-title', text: 'Latest Results (per ASIN x Pincode)' }),
      (function () {
        var pill = el('span', { class: 'badge badge-blue', text: (b.results || []).length + ' rows' });
        return pill;
      })()
    ]));
    if (!b.results || !b.results.length) {
      section.appendChild(el('div', { class: 'empty', text: 'No scrape results yet. Click "Scrape This Brand" to start.' }));
      return section;
    }
    var wrap = el('div', { class: 'table-wrap' });
    var table = el('table', { class: 'data' });
    var thead = el('thead', null, [
      el('tr', null, ['ASIN', 'Notes', 'Pincode', 'City', 'Title', 'Price', 'Seller', 'Rating', 'Reviews', 'Stock', 'Delivery', 'Verified', 'Scraped At'].map(function (h) {
        return el('th', { text: h });
      }))
    ]);
    var tbody = el('tbody');
    b.results.forEach(function (r) {
      var stockClass = 'badge-grey';
      if (r.stock === 'In Stock') stockClass = 'badge-green';
      else if (r.stock === 'Out of Stock') stockClass = 'badge-red';
      var verifiedBadge = el('span', { class: 'badge ' + (r.pincode_verified ? 'badge-green' : 'badge-grey'), text: r.pincode_verified ? 'Yes' : 'No' });
      var stockBadge = el('span', { class: 'badge ' + stockClass, text: r.stock || 'Unknown' });
      var titleCell = el('td', { class: 'cell-truncate', attrs: { title: r.title || '' }, text: r.title || '-' });
      tbody.appendChild(el('tr', null, [
        el('td', { text: r.asin || '' }),
        el('td', { text: r.notes || '' }),
        el('td', { text: r.pincode_code || '' }),
        el('td', { text: r.pincode_city || '' }),
        titleCell,
        el('td', { text: r.price || '-' }),
        el('td', { class: 'cell-truncate', attrs: { title: r.seller || '' }, text: r.seller || '-' }),
        el('td', { text: r.rating || '-' }),
        el('td', { text: r.reviews || '-' }),
        el('td', null, [stockBadge]),
        el('td', { class: 'cell-truncate', attrs: { title: r.delivery || '' }, text: r.delivery || '-' }),
        el('td', null, [verifiedBadge]),
        el('td', { text: fmtTime(r.scraped_at) })
      ]));
    });
    table.appendChild(thead); table.appendChild(tbody);
    wrap.appendChild(table);
    section.appendChild(wrap);
    return section;
  }

  // ---------- Scrape History Section ----------
  var ATTR_DEFS = [
    { key: 'price', label: 'Selling Price' },
    { key: 'seller', label: 'Seller Name' },
    { key: 'rating', label: 'Rating' },
    { key: 'reviews', label: 'Reviews' },
    { key: 'stock', label: 'Stock Status' },
    { key: 'delivery', label: 'Delivery Info' },
    { key: 'title', label: 'Title' },
    { key: 'notes', label: 'Notes' },
    { key: 'pincode_verified', label: 'Pincode Verified' }
  ];

  function fmtFriendlyDate(iso) {
    if (!iso) return '-';
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      return d.getDate() + '-' + months[d.getMonth()] + '-' + d.getFullYear() + ' ' +
             String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    } catch (e) { return iso; }
  }

  function buildHistorySection(b) {
    var section = el('div', { class: 'section', attrs: { 'data-testid': 'section-history' } });
    section.appendChild(el('div', { class: 'section-head' }, [
      el('h3', { class: 'section-title', text: 'Scrape History' }),
      el('span', { class: 'small text-muted', text: 'Download a CSV snapshot of any past scrape run' })
    ]));
    var listEl = el('div', { attrs: { 'data-testid': 'history-list' } });
    listEl.appendChild(el('div', { class: 'empty', text: 'Loading history...' }));
    section.appendChild(listEl);

    api('GET', '/api/brands/' + encodeURIComponent(b.name) + '/runs').then(function (data) {
      clear(listEl);
      var runs = data.runs || [];
      if (!runs.length && !data.legacy_results_without_run) {
        listEl.appendChild(el('div', { class: 'empty', text: 'No scrape history yet. Run a scrape to start building history.' }));
        return;
      }

      var wrap = el('div', { class: 'table-wrap' });
      var table = el('table', { class: 'data' });
      table.appendChild(el('thead', null, [
        el('tr', null, ['When', 'ASINs', 'Pincodes', 'Results', 'Status', 'Action'].map(function (h) { return el('th', { text: h }); }))
      ]));
      var tbody = el('tbody');
      runs.forEach(function (r) {
        var statusClass = r.status === 'completed' ? 'badge-green' : (r.status === 'running' ? 'badge-blue' : 'badge-grey');
        var statusBadge = el('span', { class: 'badge ' + statusClass, text: r.status || '-' });
        var pcLabel = (r.pincodes || []).map(function (p) { return p.code + ' ' + p.city; }).join(', ');
        var actions = el('div', { class: 'row' });
        var dlBtn = el('button', {
          class: 'btn btn-secondary btn-sm',
          text: 'Download CSV',
          attrs: { 'data-testid': 'btn-download-run-' + r.id }
        });
        dlBtn.addEventListener('click', function () {
          window.location.href = '/api/brands/' + encodeURIComponent(b.name) + '/runs/' + r.id + '/csv';
        });
        var delBtn = el('button', { class: 'btn btn-ghost btn-sm', text: 'Delete' });
        delBtn.addEventListener('click', function () {
          confirmModal('Delete this scrape run and all its results?', function () {
            return api('DELETE', '/api/brands/' + encodeURIComponent(b.name) + '/runs/' + r.id)
              .then(function () { toast('Run deleted', 'success'); loadBrandDetail(b.name); })
              .catch(function (e) { toast('Failed: ' + (e.detail || ''), 'error'); return false; });
          });
        });
        actions.appendChild(dlBtn); actions.appendChild(delBtn);
        tbody.appendChild(el('tr', { attrs: { 'data-testid': 'history-run-' + r.id } }, [
          el('td', null, [
            el('div', { text: fmtFriendlyDate(r.started_at) }),
            el('div', { class: 'small text-muted', text: r.finished_at ? ('finished ' + fmtFriendlyDate(r.finished_at)) : '' })
          ]),
          el('td', { text: String(r.asin_count || 0) }),
          el('td', { class: 'cell-truncate', attrs: { title: pcLabel }, text: (r.pincodes || []).length + ' (' + pcLabel + ')' }),
          el('td', { text: (r.total_results || 0) + ' / ' + (r.total_expected || 0) }),
          el('td', null, [statusBadge]),
          el('td', null, [actions])
        ]));
      });
      table.appendChild(tbody);
      wrap.appendChild(table);
      listEl.appendChild(wrap);
      if (data.legacy_results_without_run) {
        listEl.appendChild(el('div', { class: 'small text-muted mt-12', text: 'Note: ' + data.legacy_results_without_run + ' legacy result(s) exist from before history tracking was enabled. Future scrapes are tracked here.' }));
      }
    }).catch(function (e) {
      clear(listEl);
      listEl.appendChild(el('div', { class: 'empty', text: 'Failed to load history: ' + (e.detail || '') }));
    });
    return section;
  }

  // ---------- Compare View (tabs A and B) ----------
  function buildCompareSection(b) {
    var section = el('div', { class: 'section', attrs: { 'data-testid': 'section-compare' } });
    section.appendChild(el('div', { class: 'section-head' }, [
      el('h3', { class: 'section-title', text: 'Compare Across Dates' }),
      el('span', { class: 'small text-muted', text: 'See how Amazon data changed for your products over time' })
    ]));

    // Tabs
    var tabs = el('div', { class: 'tabs' });
    var tabA = el('button', { class: 'tab tab-active', text: 'Single ASIN Deep-Dive', attrs: { 'data-testid': 'tab-deep-dive' } });
    var tabB = el('button', { class: 'tab', text: 'Multi-ASIN Matrix', attrs: { 'data-testid': 'tab-matrix' } });
    tabs.appendChild(tabA); tabs.appendChild(tabB);
    section.appendChild(tabs);

    var content = el('div', { class: 'mt-12' });
    section.appendChild(content);

    function activate(view) {
      if (view === 'A') { tabA.classList.add('tab-active'); tabB.classList.remove('tab-active'); renderDeepDive(b, content); }
      else { tabB.classList.add('tab-active'); tabA.classList.remove('tab-active'); renderMatrix(b, content); }
    }
    tabA.addEventListener('click', function () { activate('A'); });
    tabB.addEventListener('click', function () { activate('B'); });
    activate('A');
    return section;
  }

  // Tab A: Single ASIN deep-dive
  function renderDeepDive(b, host) {
    clear(host);

    // Controls
    var pincodeSel = el('select', { class: 'input', attrs: { 'data-testid': 'compare-pincode' } });
    var asinSel = el('select', { class: 'input', attrs: { 'data-testid': 'compare-asin' } });
    var attrChecks = {};
    var attrWrap = el('div', { class: 'attr-checks' });
    ATTR_DEFS.forEach(function (a) {
      attrChecks[a.key] = a.key !== 'title';
      var lbl = el('label', { class: 'attr-check' });
      var cb = el('input', { type: 'checkbox', attrs: { 'data-testid': 'compare-attr-' + a.key } });
      cb.checked = attrChecks[a.key];
      cb.addEventListener('change', function () { attrChecks[a.key] = cb.checked; renderTable(); });
      lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + a.label));
      attrWrap.appendChild(lbl);
    });

    var controls = el('div', { class: 'compare-controls' }, [
      el('div', { class: 'control-group' }, [el('label', { class: 'small text-muted', text: 'Pincode' }), pincodeSel]),
      el('div', { class: 'control-group' }, [el('label', { class: 'small text-muted', text: 'ASIN' }), asinSel]),
      el('div', { class: 'control-group control-attrs' }, [el('label', { class: 'small text-muted', text: 'Attributes to display' }), attrWrap])
    ]);
    host.appendChild(controls);

    var tableHost = el('div', { class: 'mt-12' });
    host.appendChild(tableHost);

    if (!(b.pincodes || []).length) {
      tableHost.appendChild(el('div', { class: 'empty', text: 'No pincodes configured.' }));
      return;
    }
    pincodeSel.appendChild(el('option', { value: '', text: '-- select pincode --' }));
    b.pincodes.forEach(function (p) {
      pincodeSel.appendChild(el('option', { value: p.code, text: p.code + ' - ' + p.city }));
    });
    asinSel.appendChild(el('option', { value: '', text: '-- select ASIN --' }));
    (b.asins || []).forEach(function (a) {
      asinSel.appendChild(el('option', { value: a.asin, text: a.asin + (a.notes ? ' (' + a.notes + ')' : '') }));
    });

    pincodeSel.addEventListener('change', renderTable);
    asinSel.addEventListener('change', renderTable);

    function renderTable() {
      clear(tableHost);
      var pc = pincodeSel.value;
      var asin = asinSel.value;
      if (!pc || !asin) {
        tableHost.appendChild(el('div', { class: 'empty', text: 'Select a pincode and an ASIN above to view its history.' }));
        return;
      }
      tableHost.appendChild(el('div', { class: 'empty', text: 'Loading...' }));
      api('GET', '/api/brands/' + encodeURIComponent(b.name) + '/history?pincode=' + encodeURIComponent(pc) + '&asin=' + encodeURIComponent(asin)).then(function (data) {
        clear(tableHost);
        var rawRows = data.results || [];
        if (!rawRows.length) {
          tableHost.appendChild(el('div', { class: 'empty', text: 'No historical scrapes for this ASIN at this pincode.' }));
          return;
        }
        // Dedupe by day - keep only the LATEST scrape per calendar date
        // rawRows are sorted scraped_at DESC, so first occurrence per day is the latest
        var seenDay = {};
        var rows = [];
        rawRows.forEach(function (r) {
          var day = (r.scraped_at || '').slice(0, 10);
          if (!seenDay[day]) { seenDay[day] = true; rows.push(r); }
        });
        // rows are now DESC by date
        var selectedAttrs = ATTR_DEFS.filter(function (a) { return attrChecks[a.key]; });
        if (!selectedAttrs.length) {
          tableHost.appendChild(el('div', { class: 'empty', text: 'Select at least one attribute to display.' }));
          return;
        }
        var wrap = el('div', { class: 'table-wrap' });
        var table = el('table', { class: 'data' });
        var thead = el('thead', null, [
          el('tr', null, [el('th', { text: 'Date' })].concat(
            selectedAttrs.map(function (a) { return el('th', { text: a.label }); })
          ))
        ]);
        var tbody = el('tbody');
        rows.forEach(function (r, idx) {
          var prev = rows[idx + 1];  // older
          var tr = el('tr', null, [el('td', null, [
            el('div', { text: fmtFriendlyDay(r.scraped_at) }),
            el('div', { class: 'small text-muted', text: 'last scraped at ' + fmtFriendlyTime(r.scraped_at) })
          ])].concat(
            selectedAttrs.map(function (a) {
              var v = r[a.key];
              if (a.key === 'pincode_verified') v = v ? 'Yes' : 'No';
              var prevV = prev ? prev[a.key] : null;
              if (a.key === 'pincode_verified' && prev) prevV = prev[a.key] ? 'Yes' : 'No';
              var changed = prev && String(v || '') !== String(prevV || '');
              var td;
              if (a.key === 'stock' && v) {
                var bcls = v === 'In Stock' ? 'badge-green' : (v === 'Out of Stock' ? 'badge-red' : 'badge-grey');
                td = el('td', null, [el('span', { class: 'badge ' + bcls, text: v })]);
              } else {
                td = el('td', null, [el('span', { text: v == null || v === '' ? '-' : String(v) })]);
              }
              if (changed) td.classList.add('cell-changed');
              return td;
            })
          ));
          tbody.appendChild(tr);
        });
        table.appendChild(thead); table.appendChild(tbody);
        wrap.appendChild(table);
        tableHost.appendChild(wrap);
        tableHost.appendChild(el('div', { class: 'small text-muted mt-8', text: 'Showing the latest scrape per day. Highlighted cells changed compared to the previous day.' }));
      }).catch(function (e) {
        clear(tableHost);
        tableHost.appendChild(el('div', { class: 'empty', text: 'Failed: ' + (e.detail || '') }));
      });
    }
  }

  // Tab B: Multi-ASIN matrix - multi pincode + multi attribute + export
  function renderMatrix(b, host) {
    clear(host);

    // ----- Multi-select pincode (checkboxes) -----
    var pincodeChecks = {};
    (b.pincodes || []).forEach(function (p) { pincodeChecks[p.code] = { code: p.code, city: p.city, checked: true }; });
    var pcWrap = el('div', { class: 'attr-checks pincode-multi' });

    function renderPincodeChecks() {
      clear(pcWrap);
      // Select All / None toggles
      var allOn = Object.keys(pincodeChecks).every(function (k) { return pincodeChecks[k].checked; });
      var allBtn = el('label', { class: 'attr-check' }, [
        (function () {
          var cb = el('input', { type: 'checkbox', attrs: { 'data-testid': 'matrix-pincode-all' } });
          cb.checked = allOn;
          cb.addEventListener('change', function () {
            Object.keys(pincodeChecks).forEach(function (k) { pincodeChecks[k].checked = cb.checked; });
            renderPincodeChecks();
            scheduleRender();
          });
          return cb;
        })(),
        el('strong', { text: ' All' })
      ]);
      pcWrap.appendChild(allBtn);
      Object.keys(pincodeChecks).forEach(function (code) {
        var item = pincodeChecks[code];
        var lbl = el('label', { class: 'attr-check' });
        var cb = el('input', { type: 'checkbox', attrs: { 'data-testid': 'matrix-pincode-' + code } });
        cb.checked = item.checked;
        cb.addEventListener('change', function () {
          item.checked = cb.checked;
          renderPincodeChecks();
          scheduleRender();
        });
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(' ' + code + ' ' + item.city));
        pcWrap.appendChild(lbl);
      });
    }
    renderPincodeChecks();

    // ----- Multi-select attributes (checkboxes) -----
    var matrixAttrChecks = {};
    var matrixAttrWrap = el('div', { class: 'attr-checks' });
    var DEFAULT_MATRIX_ATTRS = ['price', 'stock', 'seller'];
    ATTR_DEFS.forEach(function (a) {
      matrixAttrChecks[a.key] = DEFAULT_MATRIX_ATTRS.indexOf(a.key) !== -1;
      var lbl = el('label', { class: 'attr-check' });
      var cb = el('input', { type: 'checkbox', attrs: { 'data-testid': 'matrix-attr-' + a.key } });
      cb.checked = matrixAttrChecks[a.key];
      cb.addEventListener('change', function () { matrixAttrChecks[a.key] = cb.checked; scheduleRender(); });
      lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + a.label));
      matrixAttrWrap.appendChild(lbl);
    });

    var exportBtn = el('button', { class: 'btn btn-secondary btn-sm', text: 'Export CSV', attrs: { 'data-testid': 'matrix-export-csv' } });
    var refreshBtn = el('button', { class: 'btn btn-ghost btn-sm', text: 'Refresh' });

    var controls = el('div', { class: 'compare-controls' }, [
      el('div', { class: 'control-group control-attrs' }, [el('label', { class: 'small text-muted', text: 'Pincodes' }), pcWrap]),
      el('div', { class: 'control-group control-attrs' }, [el('label', { class: 'small text-muted', text: 'Attributes' }), matrixAttrWrap]),
      el('div', { class: 'control-group' }, [el('label', { class: 'small text-muted', text: 'Actions' }), el('div', { class: 'row' }, [exportBtn, refreshBtn])])
    ]);
    host.appendChild(controls);

    var tableHost = el('div', { class: 'mt-12' });
    host.appendChild(tableHost);

    if (!(b.pincodes || []).length) {
      tableHost.appendChild(el('div', { class: 'empty', text: 'No pincodes configured.' }));
      return;
    }

    refreshBtn.addEventListener('click', renderMtx);
    exportBtn.addEventListener('click', exportCurrentMatrixCsv);

    // Cache last data for export and debouncing
    var lastMatrix = null;
    var renderTimer = null;
    function scheduleRender() {
      if (renderTimer) clearTimeout(renderTimer);
      renderTimer = setTimeout(renderMtx, 200);
    }

    renderMtx();

    function renderMtx() {
      clear(tableHost);
      var selectedPincodes = Object.keys(pincodeChecks).filter(function (k) { return pincodeChecks[k].checked; });
      var selectedAttrs = ATTR_DEFS.filter(function (a) { return matrixAttrChecks[a.key]; });
      if (!selectedPincodes.length) {
        tableHost.appendChild(el('div', { class: 'empty', text: 'Select at least one pincode.' }));
        lastMatrix = null;
        return;
      }
      if (!selectedAttrs.length) {
        tableHost.appendChild(el('div', { class: 'empty', text: 'Select at least one attribute.' }));
        lastMatrix = null;
        return;
      }
      tableHost.appendChild(el('div', { class: 'empty', text: 'Loading...' }));

      // Fetch history for each selected pincode in parallel, then combine
      var promises = selectedPincodes.map(function (pc) {
        return api('GET', '/api/brands/' + encodeURIComponent(b.name) + '/history?pincode=' + encodeURIComponent(pc));
      });
      Promise.all(promises).then(function (responses) {
        clear(tableHost);
        var allRows = [];
        responses.forEach(function (resp) {
          (resp.results || []).forEach(function (r) { allRows.push(r); });
        });
        if (!allRows.length) {
          tableHost.appendChild(el('div', { class: 'empty', text: 'No historical scrapes for the selected pincode(s).' }));
          lastMatrix = null;
          return;
        }
        var matrix = buildMatrix(allRows, selectedAttrs);
        lastMatrix = matrix;
        renderMatrixTable(matrix, selectedAttrs, tableHost);
      }).catch(function (e) {
        clear(tableHost);
        tableHost.appendChild(el('div', { class: 'empty', text: 'Failed: ' + (e.detail || '') }));
      });
    }

    function buildMatrix(rows, selectedAttrs) {
      // rows are per-API sort: scraped_at DESC.
      // 1. Group by (asin, pincode_code) -> rowKey
      // 2. For each rowKey, group results by day (YYYY-MM-DD) -> keep the LATEST
      // 3. Collect all distinct day columns in ascending order
      var rowMap = {};            // key -> { asin, pincode_code, pincode_city, byDay: { day: row } }
      var daySet = {};
      rows.forEach(function (r) {
        var key = r.asin + '||' + r.pincode_code;
        if (!rowMap[key]) {
          rowMap[key] = {
            key: key,
            asin: r.asin,
            pincode_code: r.pincode_code,
            pincode_city: r.pincode_city,
            byDay: {}
          };
        }
        var day = (r.scraped_at || '').slice(0, 10);
        if (!day) return;
        daySet[day] = true;
        // Keep latest within day. Since input is DESC, first seen = latest.
        if (rowMap[key].byDay[day] === undefined) {
          rowMap[key].byDay[day] = r;
        }
      });
      var days = Object.keys(daySet).sort();   // ascending
      var rowKeys = Object.keys(rowMap).sort(function (a, bk) {
        var A = rowMap[a], B = rowMap[bk];
        if (A.asin !== B.asin) return A.asin < B.asin ? -1 : 1;
        return A.pincode_code < B.pincode_code ? -1 : 1;
      });
      return { rows: rowKeys.map(function (k) { return rowMap[k]; }), days: days };
    }

    function valueOf(row, attrKey) {
      if (!row) return '';
      var v = row[attrKey];
      if (attrKey === 'pincode_verified') return v ? 'Yes' : 'No';
      return v == null ? '' : String(v);
    }

    function renderMatrixTable(matrix, selectedAttrs, host) {
      var rows = matrix.rows;
      var days = matrix.days;
      if (!rows.length) {
        host.appendChild(el('div', { class: 'empty', text: 'No data for the selected pincodes.' }));
        return;
      }
      var nAttrs = selectedAttrs.length;
      var wrap = el('div', { class: 'table-wrap' });
      var table = el('table', { class: 'data data-matrix' });

      // Two-row header: date spans nAttrs sub-cols, second row has attribute names
      var thead = el('thead');
      var hr1 = el('tr');
      hr1.appendChild(el('th', { attrs: { rowspan: 2, class: 'matrix-corner-1' }, text: 'ASIN' }));
      hr1.appendChild(el('th', { attrs: { rowspan: 2, class: 'matrix-corner-2' }, text: 'Pincode' }));
      days.forEach(function (d) {
        hr1.appendChild(el('th', { attrs: { colspan: nAttrs, class: 'date-group' }, text: fmtFriendlyDay(d) }));
      });
      var hr2 = el('tr');
      days.forEach(function () {
        selectedAttrs.forEach(function (a) {
          hr2.appendChild(el('th', { class: 'attr-sub', text: a.label }));
        });
      });
      thead.appendChild(hr1);
      thead.appendChild(hr2);

      var tbody = el('tbody');
      rows.forEach(function (rowObj) {
        var tr = el('tr');
        tr.appendChild(el('td', { class: 'mono-cell', text: rowObj.asin }));
        tr.appendChild(el('td', { class: 'mono-cell', text: rowObj.pincode_code + ' ' + (rowObj.pincode_city || '') }));
        // For change detection, track previous non-empty value per attribute
        var prevByAttr = {};
        days.forEach(function (d) {
          var rec = rowObj.byDay[d];
          selectedAttrs.forEach(function (a) {
            var v = valueOf(rec, a.key);
            var prev = prevByAttr[a.key];
            var changed = prev && v !== '' && v !== prev;
            var td;
            if (a.key === 'stock' && v) {
              var bcls = v === 'In Stock' ? 'badge-green' : (v === 'Out of Stock' ? 'badge-red' : 'badge-grey');
              td = el('td', null, [el('span', { class: 'badge ' + bcls, text: v })]);
            } else {
              td = el('td', { text: v || '-' });
            }
            if (changed) td.classList.add('cell-changed');
            tr.appendChild(td);
            if (v !== '') prevByAttr[a.key] = v;
          });
        });
        tbody.appendChild(tr);
      });
      table.appendChild(thead); table.appendChild(tbody);
      wrap.appendChild(table);
      host.appendChild(el('div', { class: 'small text-muted mb-8', text: 'One column per date (latest scrape of the day). Highlighted cells changed from the previous date.' }));
      host.appendChild(wrap);
    }

    function exportCurrentMatrixCsv() {
      if (!lastMatrix) { toast('Nothing to export yet', 'error'); return; }
      var selectedAttrs = ATTR_DEFS.filter(function (a) { return matrixAttrChecks[a.key]; });
      if (!selectedAttrs.length) { toast('Select at least one attribute', 'error'); return; }
      var rows = lastMatrix.rows;
      var days = lastMatrix.days;
      // Header rows
      var header1 = ['ASIN', 'Pincode', 'City'];
      var header2 = ['', '', ''];
      days.forEach(function (d) {
        for (var i = 0; i < selectedAttrs.length; i++) {
          header1.push(fmtFriendlyDay(d));
          header2.push(selectedAttrs[i].label);
        }
      });
      var lines = [];
      lines.push(csvLine(header1));
      lines.push(csvLine(header2));
      rows.forEach(function (rowObj) {
        var row = [rowObj.asin, rowObj.pincode_code, rowObj.pincode_city || ''];
        days.forEach(function (d) {
          var rec = rowObj.byDay[d];
          selectedAttrs.forEach(function (a) {
            row.push(valueOf(rec, a.key));
          });
        });
        lines.push(csvLine(row));
      });
      var csv = '\ufeff' + lines.join('\r\n');
      var dt = new Date();
      var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      var fname = (b.name || 'Brand').replace(/[^A-Za-z0-9_-]+/g, '_') + '_compare_' + dt.getDate() + '-' + months[dt.getMonth()] + '-' + dt.getFullYear() + '_' + String(dt.getHours()).padStart(2,'0') + '-' + String(dt.getMinutes()).padStart(2,'0') + '.csv';
      downloadBlob(csv, fname, 'text/csv;charset=utf-8;');
      toast('Exported ' + fname, 'success');
    }
  }

  // CSV helpers
  function csvLine(arr) {
    return arr.map(function (v) {
      var s = v == null ? '' : String(v);
      if (s.indexOf(',') !== -1 || s.indexOf('"') !== -1 || s.indexOf('\n') !== -1) {
        s = '"' + s.replace(/"/g, '""') + '"';
      }
      return s;
    }).join(',');
  }
  function downloadBlob(text, filename, mime) {
    var blob = new Blob([text], { type: mime || 'text/plain' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = filename; a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
  }

  function fmtFriendlyDay(input) {
    if (!input) return '-';
    try {
      var iso = String(input);
      // Accept either YYYY-MM-DD or full ISO timestamp
      if (iso.length >= 10 && iso[4] === '-' && iso[7] === '-') {
        var y = parseInt(iso.slice(0, 4), 10);
        var m = parseInt(iso.slice(5, 7), 10) - 1;
        var d = parseInt(iso.slice(8, 10), 10);
        var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        if (!isNaN(y) && m >= 0 && m < 12 && d > 0) return d + '-' + months[m] + '-' + y;
      }
      var dt = new Date(iso);
      if (!isNaN(dt.getTime())) {
        var months2 = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return dt.getDate() + '-' + months2[dt.getMonth()] + '-' + dt.getFullYear();
      }
    } catch (e) {}
    return input;
  }

  function fmtFriendlyTime(iso) {
    if (!iso) return '-';
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    } catch (e) { return iso; }
  }

  // Pincode selection modal
  function openPincodeSelectionModal() {
    var b = state.currentBrand;
    if (!b) return;
    if (!b.pincodes.length) { toast('Add at least one pincode first', 'error'); return; }
    if (!b.asins.length) { toast('Add at least one ASIN first', 'error'); return; }
    if (b.job && b.job.isScraping) { toast('Already scraping this brand', 'error'); return; }

    var checks = {};
    b.pincodes.forEach(function (p) { checks[p.code] = { code: p.code, city: p.city, checked: true }; });

    var listWrap = el('div', { class: 'list-pincodes' });
    function rebuild() {
      clear(listWrap);
      var allCheck = el('div', { class: 'checkbox-row' }, [
        (function () {
          var cb = el('input', { type: 'checkbox', attrs: { 'data-testid': 'select-all-pincodes' } });
          cb.checked = Object.keys(checks).every(function (k) { return checks[k].checked; });
          cb.addEventListener('change', function () {
            Object.keys(checks).forEach(function (k) { checks[k].checked = cb.checked; });
            rebuild();
            updateBtn();
          });
          return cb;
        })(),
        el('strong', { text: 'Select All' })
      ]);
      listWrap.appendChild(allCheck);
      Object.keys(checks).forEach(function (code) {
        var item = checks[code];
        var row = el('div', { class: 'checkbox-row' }, [
          (function () {
            var cb = el('input', { type: 'checkbox', attrs: { 'data-testid': 'pincode-check-' + code } });
            cb.checked = item.checked;
            cb.addEventListener('change', function () { item.checked = cb.checked; updateBtn(); });
            return cb;
          })(),
          el('span', { text: code + ' - ' + item.city })
        ]);
        listWrap.appendChild(row);
      });
    }

    var bodyEl = el('div', null, [
      el('p', { class: 'mb-12 text-muted', text: 'Brand: ' + b.name + ' (' + b.asins.length + ' ASINs). Choose which pincodes to scrape.' }),
      listWrap
    ]);
    rebuild();

    var modal;
    function updateBtn() {
      if (!modal) return;
      var n = Object.keys(checks).filter(function (k) { return checks[k].checked; }).length;
      var btn = modal.foot.querySelector('[data-testid=\"confirm-scrape\"]');
      if (btn) {
        btn.disabled = n === 0;
        btn.textContent = 'Scrape Selected (' + n + ' pincode' + (n === 1 ? '' : 's') + ' x ' + b.asins.length + ' ASINs)';
      }
    }

    modal = showModalCustom({
      title: 'Select Pincodes to Scrape',
      body: bodyEl,
      actions: [
        { label: 'Cancel' },
        {
          label: 'Scrape Selected',
          variant: 'primary',
          testid: 'confirm-scrape',
          onClick: function () {
            var picks = Object.keys(checks).filter(function (k) { return checks[k].checked; }).map(function (k) { return { code: checks[k].code, city: checks[k].city }; });
            if (!picks.length) return false;
            return api('POST', '/api/brands/' + encodeURIComponent(b.name) + '/scrape', { pincodes: picks })
              .then(function () {
                toast('Scrape started', 'success');
                loadBrandDetail(b.name);
              })
              .catch(function (e) { toast('Scrape failed: ' + (e.detail || ''), 'error'); return false; });
          }
        }
      ]
    });
    setTimeout(updateBtn, 0);
  }

  // Custom modal that exposes foot for live-updating
  function showModalCustom(opts) {
    var root = document.getElementById('modal-root');
    clear(root);
    var backdrop = el('div', { class: 'modal-backdrop' });
    backdrop.addEventListener('click', function (e) { if (e.target === backdrop) close(); });
    var modal = el('div', { class: 'modal' });
    var head = el('div', { class: 'modal-head' }, [
      el('h3', { text: opts.title || 'Confirm' }),
      el('button', { class: 'btn btn-ghost btn-sm', text: 'X', onclick: close })
    ]);
    var bodyWrap = el('div', { class: 'modal-body' });
    if (opts.body) bodyWrap.appendChild(opts.body);
    var foot = el('div', { class: 'modal-foot' });
    (opts.actions || []).forEach(function (a) {
      var btn = el('button', {
        class: 'btn ' + (a.variant === 'primary' ? 'btn-primary' : (a.variant === 'danger' ? 'btn-danger' : 'btn-secondary')),
        text: a.label,
        onclick: function () {
          if (a.onClick) {
            var r = a.onClick();
            if (r && r.then) r.then(function (v) { if (v !== false) close(); }).catch(function () {});
            else if (r !== false) close();
          } else close();
        }
      });
      if (a.testid) btn.setAttribute('data-testid', a.testid);
      foot.appendChild(btn);
    });
    modal.appendChild(head); modal.appendChild(bodyWrap); modal.appendChild(foot);
    backdrop.appendChild(modal);
    root.appendChild(backdrop);
    function close() { clear(root); }
    return { close: close, foot: foot, body: bodyWrap };
  }

  // ---------- Boot ----------
  // Wait for the auth gate to determine the signed-in state. If signed in,
  // render the home view; on sign-out we clear UI and stop polling.
  function bootApp() {
    var auth = window.__appAuth;
    if (!auth) {
      // Auth not configured; render directly (degrades gracefully).
      renderHome();
      return;
    }
    var booted = false;
    auth.ready.then(function () {
      function syncToAuthState(user) {
        if (user) {
          if (!booted) {
            booted = true;
            renderHome();
          } else {
            // Re-fetch in case user changed while signed in
            refreshBrands();
          }
        } else {
          booted = false;
          // Stop any polling and clear current brand
          stopPolling();
          state.currentBrand = null;
          // Clear visible UI so previous user's data doesn't leak between logins
          var grid = document.getElementById('brand-grid');
          if (grid) clear(grid);
          var brandView = document.getElementById('view-brand');
          if (brandView) { clear(brandView); brandView.hidden = true; }
        }
      }
      syncToAuthState(auth.getUser());
      auth.onChange(syncToAuthState);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootApp);
  } else {
    bootApp();
  }
})();
