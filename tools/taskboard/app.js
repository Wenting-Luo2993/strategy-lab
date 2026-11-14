"use strict";
const data = JSON.parse(document.getElementById("taskboard-data").textContent);
const projects = new Map(data.projects.map(p => [p.id, p]));
const documents = new Map(data.documents.map(d => [d.id, d]));
const tasks = new Map(data.tasks.map(t => [t.id, t]));
const labels = {backlog: "Backlog", ready: "Ready", in_progress: "In progress", waiting: "Waiting", review: "Review", done: "Done"};
const colors = ["#588464", "#b18b48", "#69889a", "#897399", "#7c8d51"];
const state = {view: "overview", project: "", status: "", search: ""};
const $ = id => document.getElementById(id);
const escape = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const badge = status => `<span class="badge ${escape(status)}">${escape(labels[status] || status)}</span>`;
const docLink = id => `<a href="#doc/${id}">${escape(documents.get(id).title)}</a>`;
const taskLink = id => `<a href="#task/${id}">${escape(tasks.get(id).title)}</a>`;
const matches = value => JSON.stringify(value).toLowerCase().includes(state.search);
const filteredTasks = () => data.tasks.filter(t => (!state.project || t.project === state.project)
  && (!state.status || t.status === state.status) && matches(t));
const filteredDocs = () => data.documents.filter(d => (!state.project || d.projects.includes(state.project))
  && matches({title:d.title, path:d.path, kind:d.kind, status:d.status}));
const ageDays = value => Math.floor((Date.now() - Date.parse(value + "T00:00:00Z")) / 86400000);
const lastReview = items => [...items].map(t => t.reviewed).sort().at(0);

data.projects.forEach((p, i) => {
  const option = document.createElement("option");
  option.value = p.id;
  option.textContent = p.title;
  $("project-filter").append(option);
  const button = document.createElement("button");
  button.className = "project-button";
  button.dataset.project = p.id;
  button.innerHTML = `<span class="dot" style="background:${colors[i % colors.length]}"></span>${escape(p.title)}`;
  $("project-nav").append(button);
});
Object.entries(labels).forEach(([value, label]) => {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  $("status-filter").append(option);
});
$("snapshot").innerHTML = `Built ${escape(data.generated_at.slice(0, 10))}<br>${data.documents.length} linked documents · ${data.tasks.length} tracked tasks`;

function taskCard(t) {
  return `<button class="task" data-task="${t.id}">
    <small>${escape(projects.get(t.project).title)} / ${escape(t.milestone)}</small>
    <strong>${escape(t.title)}</strong><p>${escape(t.note)}</p>
    ${t.blocked_by.length ? `<span class="badge blocked">${t.blocked_by.length} prerequisite(s) open</span>` : ""}
    <small style="margin-top:10px">Reviewed ${escape(t.reviewed)} · ${escape(t.id)}</small></button>`;
}

function overview(items) {
  const visibleProjects = data.projects.filter(p => (!state.project || state.project === p.id)
    && (matches(p) || items.some(t => t.project === p.id)));
  const counts = [
    [visibleProjects.length, "Projects in view"],
    [items.filter(t => t.status === "ready").length, "Ready to pick up"],
    [items.filter(t => ["waiting","review"].includes(t.status)).length, "Waiting / review"],
    [items.filter(t => t.status === "done").length, "Completed tasks, not proven outcomes"],
  ];
  const attention = items.filter(t => ["waiting","review"].includes(t.status));
  return `<div class="stats">${counts.map(([n,label]) => `<div class="stat"><strong>${n}</strong><span>${label}</span></div>`).join("")}</div>
    <div class="section-title"><h2>Your projects</h2><span>Outcomes first. Evidence attached.</span></div>
    <div class="project-grid">${visibleProjects.map(p => {
      const all = data.tasks.filter(t => t.project === p.id);
      return `<article class="project-card" style="--accent:${colors[data.projects.indexOf(p) % colors.length]}">
        <div class="card-top"><span class="eyebrow">${escape(p.id)}</span><span class="badge">${escape(p.stage)}</span></div>
        <h3>${escape(p.title)}</h3><p>${escape(p.goal)}</p>
        <div class="next-action"><strong>NEXT ACTION</strong>${escape(p.next_action)}</div>
        <div class="card-bottom"><span>${all.length} tasks · oldest task review ${escape(lastReview(all) || "not recorded")}</span>
          <button class="text-button" data-project-board="${p.id}">View work &rarr;</button></div>
      </article>`;
    }).join("") || '<p class="empty">No projects match this search.</p>'}</div>
    <div class="section-title"><h2>Needs attention</h2><span>Decisions and evidence still outstanding</span></div>
    <div class="attention-list">${attention.map(t => `<button class="attention" data-task="${t.id}">
      ${badge(t.status)} <strong>${escape(t.title)}</strong><span>${escape(t.note)}</span>
    </button>`).join("") || '<p class="empty">No waiting or review tasks in this view.</p>'}</div>`;
}

function render() {
  const items = filteredTasks();
  document.querySelectorAll("[data-view]").forEach(b => {
    b.classList.toggle("active", b.dataset.view === state.view);
    b.setAttribute("aria-pressed", String(b.dataset.view === state.view));
  });
  document.querySelectorAll("[data-project]").forEach(b => {
    b.classList.toggle("active", b.dataset.project === state.project);
    b.setAttribute("aria-pressed", String(b.dataset.project === state.project));
  });
  $("project-filter").value = state.project;
  $("status-label").hidden = state.view !== "board";
  const titles = {overview: "From plans to progress.", board: "One clear next step.", documents: "The knowledge behind the work."};
  $("page-title").textContent = titles[state.view];
  $("page-description").textContent = state.view === "documents"
    ? "Current references and historical context, organized without moving files."
    : "Work state is not a research verdict or a live health signal.";
  const oldest = lastReview(data.tasks);
  const stale = oldest && ageDays(oldest) >= 7;
  $("freshness").classList.toggle("stale", Boolean(stale));
  $("freshness").textContent = `${stale ? "Review due. " : ""}Task states are reviewed snapshots (oldest: ${oldest}). No live broker, research database or CI polling. Source documents may contain older observations.`;
  if (state.view === "overview") {
    $("content").innerHTML = overview(items);
    $("result-count").textContent = `${items.length} matching tasks across the selected projects.`;
  } else if (state.view === "board") {
    $("content").innerHTML = `<div class="board">${Object.entries(labels).filter(([s]) => !state.status || s === state.status).map(([s,label]) => {
      const column = items.filter(t => t.status === s);
      return `<section class="column"><h2>${label}<span>${column.length}</span></h2>
        ${column.map(taskCard).join("") || '<p class="muted">No matching tasks</p>'}</section>`;
    }).join("")}</div>`;
    $("result-count").textContent = `${items.length} matching tasks. Select a card for acceptance criteria, prerequisites and evidence.`;
  } else {
    const docs = filteredDocs().sort((a,b) => ({current:0,historical:1,superseded:2})[a.status] - ({current:0,historical:1,superseded:2})[b.status]);
    $("content").innerHTML = `<div class="documents">${docs.map(d => `<article class="document-row"><div>
      <h3>${escape(d.title)}</h3><p>${escape(d.path)}</p>${badge(d.status)} <span class="badge">${escape(d.kind)}</span>
      <span class="muted">Reviewed ${escape(d.reviewed)}</span></div><button class="text-button" data-doc="${d.id}">Read document &rarr;</button></article>`).join("")
      || '<p class="empty">No documents match. Only explicitly tagged Markdown is indexed.</p>'}</div>`;
    $("result-count").textContent = `${docs.length} matching documents. Untagged repository documents are not indexed.`;
  }
}

function detail(type, id, section) {
  const t = tasks.get(id), d = documents.get(id);
  $("detail-type").textContent = type === "task" ? "TASK / EVIDENCE" : "DOCUMENT READER";
  if (type === "task" && t) {
    $("detail-content").innerHTML = `<div>${badge(t.status)} <span class="muted">${escape(projects.get(t.project).title)}</span></div>
      <h2 id="detail-title">${escape(t.title)}</h2><p>${escape(t.note)}</p>
      <dl><dt>Milestone</dt><dd>${escape(t.milestone)}</dd><dt>Done means</dt><dd>${escape(t.acceptance)}</dd>
      <dt>Prerequisites</dt><dd>${t.depends_on.length ? t.depends_on.map(dep => `${taskLink(dep)} ${badge(tasks.get(dep).status)}`).join("<br>") : "No declared prerequisites"}</dd>
      <dt>Last reviewed</dt><dd>${escape(t.reviewed)}${ageDays(t.reviewed) >= 7 ? " · Review due" : ""}</dd>
      <dt>Source of task</dt><dd>${docLink(t.source)}</dd></dl>
      <h3>Supporting documents</h3><div class="link-list">${t.refs.map(docLink).join("")}</div>`;
  } else if (type === "doc" && d) {
    $("detail-content").innerHTML = `<div>${badge(d.status)} <span class="badge">${escape(d.kind)}</span></div>
      <h2 id="detail-title">${escape(d.title)}</h2><p class="muted">${escape(d.path)}<br>Reviewed ${escape(d.reviewed)} · source SHA-256 ${escape(d.sha256.slice(0, 12))}</p>
      ${d.status !== "current" ? '<p class="snapshot-note stale">Historical context, not a current status or execution instruction.</p>' : ""}
      ${d.superseded_by?.length ? `<p>Read instead: ${d.superseded_by.map(docLink).join(" · ")}</p>` : ""}
      <p class="muted">Safe Markdown preview. Raw HTML, images and diagram scripts are not executed. Only indexed documents and explicit web links are clickable.</p>
      <article class="document-body">${d.html}</article>`;
  } else {
    $("detail-content").innerHTML = '<h2 id="detail-title">Reference not found</h2><p>This snapshot does not contain that task or document.</p>';
  }
  if (!$("detail").open) $("detail").showModal();
  $("detail").scrollTop = 0;
  if (section) {
    const target = document.getElementById("section-" + section);
    if (target) target.scrollIntoView();
  }
}

function route() {
  const [kind, id, section] = location.hash.slice(1).split("/");
  if (["task","doc"].includes(kind)) {
    detail(kind, id, section);
  } else {
    if ($("detail").open) $("detail").close();
    if (["overview","board","documents"].includes(kind)) state.view = kind;
    render();
  }
}
document.addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.task) location.hash = `task/${button.dataset.task}`;
  if (button.dataset.doc) location.hash = `doc/${button.dataset.doc}`;
  if (button.dataset.view) {
    state.status = "";
    $("status-filter").value = "";
    state.view = button.dataset.view;
    location.hash = state.view;
    render();
  }
  if (button.dataset.project || button.dataset.projectBoard) {
    state.project = button.dataset.project || button.dataset.projectBoard;
    state.status = "";
    $("status-filter").value = "";
    if (button.dataset.projectBoard) state.view = "board";
    location.hash = state.view;
    render();
  }
});
$("search").addEventListener("input", event => {state.search = event.target.value.trim().toLowerCase(); render();});
$("project-filter").addEventListener("change", event => {state.project = event.target.value; render();});
$("status-filter").addEventListener("change", event => {state.status = event.target.value; render();});
$("close-detail").addEventListener("click", () => $("detail").close());
$("detail").addEventListener("close", () => {
  if (/^#(task|doc)\//.test(location.hash)) location.hash = state.view;
});
window.addEventListener("hashchange", route);
render();
route();
