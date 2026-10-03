const PAGE = 100;
const state = { sets: [], activeId: null, cards: [], shown: PAGE, listings: [], chart: null };
const el = (id) => document.getElementById(id);

async function api(path, opts) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

function esc(str) {
  const d = document.createElement("div");
  d.textContent = str == null ? "" : String(str);
  return d.innerHTML;
}
const money = (n) => (n == null ? "—" : `$${Number(n).toFixed(2)}`);

// ---------- set dropdown ----------

async function loadSets(selectId) {
  state.sets = await api("/api/sets");
  const sel = el("set-select");
  if (!state.sets.length) {
    sel.innerHTML = `<option value="">No sets yet — add one</option>`;
    showEmpty();
    return;
  }
  const byYear = {};
  state.sets.forEach((s) => (byYear[s.year || "Other"] ||= []).push(s));
  const years = Object.keys(byYear).sort((a, b) => b - a);
  sel.innerHTML =
    `<option value="">Choose a set…</option>` +
    years
      .map(
        (y) =>
          `<optgroup label="${esc(y)}">` +
          byYear[y].map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("") +
          `</optgroup>`
      )
      .join("");
  if (selectId) {
    sel.value = selectId;
    await selectSet(selectId);
  } else if (state.activeId && state.sets.some((s) => s.id === state.activeId)) {
    sel.value = state.activeId;
  } else {
    showEmpty();
  }
}

function showEmpty() {
  state.activeId = null;
  el("set-view").hidden = true;
  el("empty-state").hidden = false;
}

el("set-select").onchange = (e) => {
  if (e.target.value) selectSet(parseInt(e.target.value));
  else showEmpty();
};

// ---------- set view ----------

async function selectSet(id) {
  state.activeId = id;
  const s = state.sets.find((x) => x.id === id);
  el("empty-state").hidden = true;
  el("set-view").hidden = false;
  el("set-title").textContent = s.name;
  el("set-sub").textContent = [s.year, s.sport, `${s.card_count} cards in checklist`, `eBay search: "${s.search_query}"`]
    .filter(Boolean)
    .join(" · ");
  const attr = el("set-attribution");
  if (s.checklist_source === "setlist") {
    attr.innerHTML = `Checklist via <a href="https://setlistcards.com" target="_blank" rel="noopener">SetList</a> (CC BY-NC 4.0)`;
    attr.hidden = false;
  } else {
    attr.hidden = true;
  }
  await refresh();
}

async function refresh() {
  const id = state.activeId;
  const [summary, checklist, listings] = await Promise.all([
    api(`/api/sets/${id}/summary`),
    api(`/api/sets/${id}/checklist`),
    api(`/api/sets/${id}/listings`),
  ]);
  state.cards = checklist.cards;
  state.listings = listings.listings;
  state.shown = PAGE;
  el("fetched-at").textContent = summary.fetched_at
    ? `Last searched ${new Date(summary.fetched_at).toLocaleString()}`
    : "Not searched yet — click “Search eBay”";
  renderSummary(summary);
  renderSubsetOptions();
  renderChecklist();
  renderOther();
  if (!el("tab-history").hidden) await renderHistory();
}

function statCard(label, value, pct, note, extraClass) {
  return `<div class="stat-card ${extraClass || ""}"><div class="label">${label}</div><div class="value">${value}</div>
    ${pct != null ? `<div class="bar"><span style="width:${pct}%"></span></div>` : ""}
    ${note ? `<div class="stat-note">${note}</div>` : ""}</div>`;
}

function renderSummary(s) {
  const searched = !!s.fetched_at;
  const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
  const sold = s.sold;
  let html = "";
  html += statCard("Cards on eBay", searched ? `${s.cards_listed} <small>/ ${s.cards_total}</small>` : `— <small>/ ${s.cards_total}</small>`,
    searched ? pct(s.cards_listed, s.cards_total) : null);
  html += statCard("Hits on eBay", searched ? `${s.hits_listed} <small>/ ${s.hits_total}</small>` : `— <small>/ ${s.hits_total}</small>`,
    searched && s.hits_total ? pct(s.hits_listed, s.hits_total) : null,
    s.hits_total ? "Autographs, relics & patches" : "No hit cards flagged in this checklist");
  html += statCard("Avg. asking price", money(s.avg_price), null,
    searched ? `Median ${money(s.median_price)} · ${s.matched_listing_count} matched listings` : "Not searched yet");
  html += statCard("Avg. sold price", money(sold.avg_price), null,
    sold.comp_count ? `From ${sold.matched_comp_count} matched sales across ${sold.cards_with_comps} cards` : "No sold comps imported yet",
    "sold");
  el("summary-grid").innerHTML = html;
}

// ---------- checklist ----------

function renderSubsetOptions() {
  const cur = el("f-subset").value;
  const subs = [...new Set(state.cards.map((c) => c.subset).filter(Boolean))].sort();
  el("f-subset").innerHTML =
    `<option value="">All subsets</option>` + subs.map((s) => `<option>${esc(s)}</option>`).join("");
  el("f-subset").value = subs.includes(cur) ? cur : "";
}

function filteredCards() {
  const q = el("f-search").value.trim().toLowerCase();
  const status = el("f-status").value;
  const subset = el("f-subset").value;
  const hitsOnly = el("f-hits").checked;
  return state.cards.filter((c) => {
    if (hitsOnly && !c.is_hit) return false;
    if (subset && c.subset !== subset) return false;
    if (status === "listed" && !c.listing_count) return false;
    if (status === "sold-data" && !c.sold_count) return false;
    if (status === "unlisted" && (c.listing_count || c.sold_count)) return false;
    if (q && !`${c.player} ${c.team || ""} ${c.card_number || ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function renderChecklist() {
  const rows = filteredCards();
  const visible = rows.slice(0, state.shown);
  el("filter-count").textContent = `${rows.length} of ${state.cards.length} cards`;
  el("checklist-body").innerHTML = visible
    .map((c) => {
      const asking = c.listing_count
        ? `<span class="status-on"><a href="${esc(c.best_url || "#")}" target="_blank" rel="noopener sponsored">${money(c.min_price)}</a>
           <small>${c.listing_count > 1 ? `· ${c.listing_count} listings` : ""}</small></span>`
        : `<span class="status-off">—</span>`;
      const sold = c.sold_count
        ? `<span class="sold-cell">${money(c.sold_avg_price)} <small>· ${c.sold_count} sale${c.sold_count > 1 ? "s" : ""}</small></span>`
        : `<span class="sold-off">—</span>`;
      return `<tr>
        <td class="num">${esc(c.card_number)}</td>
        <td>${esc(c.player)}${c.is_hit ? `<span class="hit-tag">hit</span>` : ""}</td>
        <td>${esc(c.team)}</td><td>${esc(c.subset)}</td><td>${esc(c.print_run)}</td>
        <td>${asking}</td><td>${sold}</td></tr>`;
    })
    .join("");
  el("btn-more").hidden = rows.length <= state.shown;
}

["f-search", "f-status", "f-subset", "f-hits"].forEach((id) =>
  el(id).addEventListener("input", () => { state.shown = PAGE; renderChecklist(); })
);
el("btn-more").onclick = () => { state.shown += PAGE; renderChecklist(); };

// ---------- other listings ----------

function renderOther() {
  const others = state.listings.filter((l) => !l.card_id);
  el("other-count").textContent = others.length ? `(${others.length})` : "";
  el("listings-grid").innerHTML = others.length
    ? others
        .map(
          (l) => `<a class="listing-card" href="${esc(l.url || "#")}" target="_blank" rel="noopener sponsored">
            <img src="${esc(l.image_url || "")}" alt="" onerror="this.style.visibility='hidden'">
            <div class="body"><div class="price">${money(l.price)}</div><div class="title">${esc(l.title)}</div></div></a>`
        )
        .join("")
    : `<p class="empty-listings">${state.listings.length ? "Everything found was matched to a checklist card." : "No listings yet — click “Search eBay”."}</p>`;
}

// ---------- price history ----------

async function renderHistory() {
  const data = await api(`/api/sets/${state.activeId}/price-history`);
  const hasData = data.asking.length || data.sold.length;
  el("history-empty").hidden = hasData;
  el("history-chart").hidden = !hasData;
  if (!hasData) return;

  const dateKey = (iso) => iso.slice(0, 10);
  const labels = [...new Set([...data.asking.map((r) => dateKey(r.fetched_at)), ...data.sold.map((r) => r.sold_date)])].sort();
  const askingByDate = {};
  data.asking.forEach((r) => (askingByDate[dateKey(r.fetched_at)] = r.avg_price));
  const soldByDate = {};
  data.sold.forEach((r) => (soldByDate[r.sold_date] = r.avg_price));

  const ctx = el("history-chart").getContext("2d");
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Avg. asking price (eBay)",
          data: labels.map((d) => (askingByDate[d] != null ? Number(askingByDate[d].toFixed(2)) : null)),
          borderColor: "#4A6FA5", backgroundColor: "#4A6FA5", spanGaps: true, tension: 0.25,
        },
        {
          label: "Avg. sold price (imported)",
          data: labels.map((d) => (soldByDate[d] != null ? Number(soldByDate[d].toFixed(2)) : null)),
          borderColor: "#C6A15B", backgroundColor: "#C6A15B", spanGaps: true, tension: 0.25,
        },
      ],
    },
    options: {
      responsive: true,
      scales: { y: { ticks: { callback: (v) => `$${v}` } } },
      plugins: { legend: { position: "bottom" } },
    },
  });
}

document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = async () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    el("tab-checklist").hidden = t.dataset.tab !== "checklist";
    el("tab-other").hidden = t.dataset.tab !== "other";
    el("tab-history").hidden = t.dataset.tab !== "history";
    if (t.dataset.tab === "history") await renderHistory();
  };
});

// ---------- actions ----------

el("btn-search").onclick = async () => {
  const btn = el("btn-search");
  btn.disabled = true;
  btn.textContent = "Searching…";
  try {
    await api(`/api/sets/${state.activeId}/search`, { method: "POST" });
    await refresh();
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Search eBay";
  }
};

el("btn-delete").onclick = async () => {
  if (!confirm("Remove this set, its checklist, and its saved listings/comps?")) return;
  await api(`/api/sets/${state.activeId}`, { method: "DELETE" });
  state.activeId = null;
  await loadSets();
};

// ---------- add-a-set modal (browse catalog + manual CSV import) ----------

let catalog = null;

el("btn-browse").onclick = async () => {
  el("c-error").hidden = true;
  el("i-error").hidden = true;
  el("browse-modal-backdrop").hidden = false;
  if (!catalog) {
    catalog = await api("/api/catalog");
    const sports = Object.keys(catalog).sort();
    el("c-sport").innerHTML =
      `<option value="">Choose…</option>` + sports.map((s) => `<option>${esc(s)}</option>`).join("");
  }
};

function resetCascadeFrom(level) {
  // level: 'manufacturer' | 'year' | 'set' — resets this select and everything after it
  const order = ["manufacturer", "year", "set"];
  const start = order.indexOf(level);
  for (let i = start; i < order.length; i++) {
    const sel = el(`c-${order[i]}`);
    sel.disabled = true;
    sel.innerHTML = `<option value="">Choose ${i === 0 ? "a sport" : order[i - 1]} first</option>`;
  }
  el("btn-browse-load").disabled = true;
}

el("c-sport").addEventListener("change", (e) => {
  resetCascadeFrom("manufacturer");
  const sport = e.target.value;
  if (!sport) return;
  const mans = Object.keys(catalog[sport]).sort();
  el("c-manufacturer").disabled = false;
  el("c-manufacturer").innerHTML =
    `<option value="">Choose…</option>` + mans.map((m) => `<option>${esc(m)}</option>`).join("");
});

el("c-manufacturer").addEventListener("change", (e) => {
  resetCascadeFrom("year");
  const sport = el("c-sport").value, man = e.target.value;
  if (!man) return;
  const years = Object.keys(catalog[sport][man]).sort((a, b) => b - a);
  el("c-year").disabled = false;
  el("c-year").innerHTML =
    `<option value="">Choose…</option>` + years.map((y) => `<option>${esc(y)}</option>`).join("");
});

el("c-year").addEventListener("change", (e) => {
  resetCascadeFrom("set");
  const sport = el("c-sport").value, man = el("c-manufacturer").value, year = e.target.value;
  if (!year) return;
  const items = catalog[sport][man][year];
  el("c-set").disabled = false;
  el("c-set").innerHTML =
    `<option value="">Choose…</option>` + items.map((it) => `<option value="${it.slug}">${esc(it.title)}</option>`).join("");
});

el("c-set").addEventListener("change", (e) => {
  el("btn-browse-load").disabled = !e.target.value;
});

el("btn-browse-load").onclick = async () => {
  const slug = el("c-set").value;
  if (!slug) return;
  const btn = el("btn-browse-load");
  btn.disabled = true;
  btn.textContent = "Loading…";
  el("c-error").hidden = true;
  try {
    const res = await api(`/api/catalog/${slug}/load`, { method: "POST" });
    el("browse-modal-backdrop").hidden = true;
    await loadSets(res.id);
  } catch (err) {
    el("c-error").textContent = err.message;
    el("c-error").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Load set";
  }
};

document.querySelectorAll(".modal-tab").forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll(".modal-tab").forEach((x) => x.classList.toggle("active", x === t));
    el("modal-pane-browse").hidden = t.dataset.modalTab !== "browse";
    el("modal-pane-manual").hidden = t.dataset.modalTab !== "manual";
  };
});

el("modal-pane-manual").onsubmit = async (e) => {
  e.preventDefault();
  const btn = el("btn-import-submit");
  btn.disabled = true;
  btn.textContent = "Importing…";
  try {
    const file = el("i-file").files[0];
    if (!file) throw new Error("Choose a checklist CSV file first.");
    const csv_text = await file.text();
    const res = await api("/api/sets/import", {
      method: "POST",
      body: JSON.stringify({
        name: el("i-name").value.trim(),
        year: el("i-year").value ? parseInt(el("i-year").value) : null,
        sport: el("i-sport").value.trim() || null,
        search_query: el("i-query").value.trim() || null,
        csv_text,
      }),
    });
    el("browse-modal-backdrop").hidden = true;
    e.target.reset();
    await loadSets(res.id);
  } catch (err) {
    el("i-error").textContent = err.message;
    el("i-error").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Import";
  }
};

// ---------- sold comps import modal ----------

el("btn-import-sold").onclick = () => {
  el("s-error").hidden = true;
  el("s-success").hidden = true;
  el("sold-modal-backdrop").hidden = false;
};

el("sold-import-form").onsubmit = async (e) => {
  e.preventDefault();
  const btn = el("btn-sold-submit");
  btn.disabled = true;
  btn.textContent = "Importing…";
  el("s-error").hidden = true;
  el("s-success").hidden = true;
  try {
    const file = el("s-file").files[0];
    const csv_text = await file.text();
    const res = await api(`/api/sets/${state.activeId}/sold-comps/import`, {
      method: "POST",
      body: JSON.stringify({ csv_text, source: el("s-source").value.trim() || null }),
    });
    el("s-success").textContent = `Imported ${res.comps_imported} sales (${res.matched} matched to a checklist card)${res.rows_skipped ? `, ${res.rows_skipped} rows skipped` : ""}.`;
    el("s-success").hidden = false;
    e.target.reset();
    await refresh();
  } catch (err) {
    el("s-error").textContent = err.message;
    el("s-error").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Import";
  }
};

document.querySelectorAll("[data-close]").forEach((btn) =>
  btn.addEventListener("click", (e) => e.target.closest(".modal-backdrop").hidden = true)
);

loadSets();
