(() => {
  const byId = (id) => document.getElementById(id);
  const endpoint = (name) => `/api/runs/${encodeURIComponent(name)}`;
  let selectedRun = null;

  async function readJson(url) {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok) throw Error(data.error || "Could not read the log.");
    return data;
  }

  function notice(error) {
    byId("notice").textContent = String(error.message || error);
  }

  async function selectRun(name) {
    selectedRun = name;
    const path = endpoint(name);
    const data = await readJson(path);
    if (selectedRun !== name) return;

    byId("notice").textContent = `${data.config.model || "—"} · ${data.state.phase || "—"} · ${data.records.length} graded submissions`;
    byId("report").src = path + "/report";
    for (const id of ["report", "downloads", "inspector"]) byId(id).hidden = false;
    for (const format of ["json", "csv", "html"]) {
      byId(format).href = `${path}/download?format=${format}`;
    }
    byId("trial").replaceChildren();
    byId("trace").replaceChildren();
    for (const record of data.records) {
      const option = document.createElement("option");
      option.value = record.call;
      option.textContent = `#${record.call} ${record.problem_id} R${record.round}, attempt ${record.attempt} · ${record.feedback}`;
      byId("trial").append(option);
    }
    for (const id of ["request", "response", "record"]) byId(id).textContent = "";
  }

  async function refresh() {
    const {runs} = await readJson("/api/runs");
    const list = byId("runs");
    list.replaceChildren();
    for (const run of runs) {
      const button = document.createElement("button");
      button.textContent = `${run.config.model} · ${run.name}`;
      button.addEventListener("click", () => selectRun(run.name).catch(notice));
      list.append(button);
    }
    if (!runs.length) list.textContent = "No saved runs.";
    if (selectedRun) await selectRun(selectedRun);
  }

  function appendTrace(trace, label) {
    if (!trace) return;
    const title = document.createElement("h3");
    title.textContent = `${label}: ${trace.reason}`;
    const list = document.createElement("ol");
    for (const step of trace.steps) {
      const item = document.createElement("li");
      item.value = step.index;
      const move = step.move ? `Disk ${step.disk ?? "?"}, ${step.move.split("").join(" → ")}` : "Initial board";
      const pegs = Object.entries(step.state).map(([peg, disks]) => `${peg}=[${disks}]`).join(" ");
      item.textContent = `${move} · ${step.message} · ${pegs}`;
      list.append(item);
    }
    byId("trace").append(title, list);
  }

  async function loadTrial() {
    const name = selectedRun;
    const trial = byId("trial").value;
    if (!name || !trial) return;
    const details = await readJson(`${endpoint(name)}/trials/${encodeURIComponent(trial)}`);
    if (selectedRun !== name || byId("trial").value !== trial) return;
    for (const id of ["request", "response", "record"]) {
      byId(id).textContent = JSON.stringify(details[id], null, 2);
    }
    byId("trace").replaceChildren();
    appendTrace(details.current_trace, "Current rule");
    const previous = details.previous_trace;
    const label = previous?.scope === "previous_rule_only"
      ? "Previous restriction, current initial board, goal ignored"
      : "Previous restriction, current initial board and goal";
    appendTrace(previous, label);
    if (details.errors.length) notice(details.errors.join(" / "));
  }

  byId("refresh").addEventListener("click", () => refresh().catch(notice));
  byId("load-trial").addEventListener("click", () => loadTrial().catch(notice));
  refresh().catch(notice);
})();
