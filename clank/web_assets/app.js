"use strict";
const $ = id => document.getElementById(id);
const initialToken = new URLSearchParams(location.hash.slice(1)).get("token");
if (initialToken) { sessionStorage.setItem("clank-token", initialToken); history.replaceState(null, "", "/"); }
const token = sessionStorage.getItem("clank-token") || "";
let sessionId = null, busy = false, status = null, conversation = [];
const headers = () => ({"Authorization": "Bearer " + token, "Content-Type": "application/json"});
function error(text) { $("error").textContent = text; $("error").hidden = !text; }
async function api(path) { const r = await fetch(path, {headers: headers()}); const data = await r.json(); if (!r.ok) throw new Error(data.error || "Workspace request failed"); return data; }
const escape = s => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
function markdown(text) {
  return text.split(/(```[\s\S]*?```)/g).map(part => {
    if (part.startsWith("```")) return "<pre><code>" + escape(part.replace(/^```[^\n]*\n?/, "").replace(/```$/, "")) + "</code></pre>";
    return escape(part).replace(/^### (.+)$/gm,"<h3>$1</h3>").replace(/^## (.+)$/gm,"<h2>$1</h2>").replace(/\*\*([^*\n]+)\*\*/g,"<strong>$1</strong>").replace(/`([^`\n]+)`/g,"<code>$1</code>").replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s<>]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1 ↗</a>');
  }).join("");
}
function scroll() { $("scroll-area").scrollTop = $("scroll-area").scrollHeight; }
function message(role, text = "") {
  const box = document.createElement("article"); box.className = "message " + role;
  const label = document.createElement("div"); label.className = "message-label"; label.textContent = role === "user" ? "YOU" : "CLANK";
  const content = document.createElement("div"); content.className = "message-content"; content.innerHTML = markdown(text);
  box.append(label, content); $("messages").append(box); return {box, content, text};
}
function tool(data) {
  const details = document.createElement("details"); details.className = "tool-card" + (!data.ok ? " denied" : "");
  const action={edit_file:"edited",write_file:"created",run_tests:"tests",run_command:"executed",web_search:"searched",get_weather:"weather"}[data.name]||"read";
  const summary = document.createElement("summary"); summary.textContent = `${data.ok ? "✓" : "!"} ${data.name} · ${data.denied ? "denied" : data.ok ? action : "failed"} · ${JSON.stringify(data.arguments)}`;
  const output = document.createElement("pre"); output.textContent = data.output; details.append(summary, output); $("messages").append(details); return details;
}
function finishMessage(item, caption = "") {
  if (!item || !item.text) return;
  const row = document.createElement("div"); row.className = "message-footer";
  const note = document.createElement("span"); note.textContent = caption;
  const copy = document.createElement("button"); copy.className = "copy-button"; copy.textContent = "Copy answer";
  copy.onclick = async () => { try { await navigator.clipboard.writeText(item.text); copy.textContent = "Copied ✓"; } catch { error("Clipboard unavailable. Select and copy the answer instead."); } };
  row.append(note, copy); item.box.append(row);
}
function setBusy(value) {
  busy = value; $("send").disabled = value; $("prompt").disabled = value;
  $("new").disabled = value; $("model").disabled = value;
  $("export").disabled = value; $("retry").disabled = value;
  $("autonomous").disabled=value;$("web-enabled").disabled=value;$("changes").disabled=value;$("stop").hidden=!value;$("stop").disabled=true;$("stop").textContent="Stop";
  document.querySelectorAll("[data-prompt],.session").forEach(b => b.disabled = value);
  $("send").textContent = value ? "Thinking…" : "Send ↑";
}
async function loadSessions() {
  const data = await api("/api/sessions"); $("sessions").replaceChildren();
  if (!data.sessions.length) { const p = document.createElement("p"); p.className="muted"; p.textContent="Your conversations will appear here."; $("sessions").append(p); }
  data.sessions.forEach(s => { const b=document.createElement("button"); b.className="session"+(s.id===sessionId?" active":""); b.disabled=busy;
    const name=document.createElement("span"); name.textContent=s.title||"New conversation";
    const date=document.createElement("small"); date.textContent=new Date(s.updated_at).toLocaleDateString(undefined,{month:"short",day:"numeric"})+" · "+s.message_count+" messages";
    b.append(name,date); b.onclick=()=>resume(s.id); $("sessions").append(b); });
}
async function resume(id) {
  if (busy) return;
  error("");
  closeHistory();
  try { const data=await api("/api/history?id="+encodeURIComponent(id)); sessionId=id; conversation=data.messages; $("welcome").hidden=true; $("messages").replaceChildren();
    const calls = new Map();
    data.messages.forEach(m => {
      (m.tool_calls || []).forEach(call => calls.set(call.id, call));
      if(m.role==="tool") tool({name:m.name||"file tool",arguments:calls.get(m.tool_call_id)?.arguments || {},output:m.content,ok:!m.content.startsWith("ERROR:")&&!m.content.startsWith("DENIED:"),denied:m.content.startsWith("DENIED:")});
      else if(m.role==="user"||m.role==="assistant") { if(m.content) { const item=message(m.role,m.content); if(m.role==="assistant") finishMessage(item,"Saved conversation"); } }
    });
    await loadSessions(); scroll();
  } catch(e) {error(e.message);}
}
async function send(prompt) {
  if(busy || !prompt.trim()) return;
  if(!$("model").value) {error("Choose an installed model. If none appear, start Ollama and install a model, then reload.");return;}
  setBusy(true); error("");
  let current=null, toolCount=0, completed=false;
  const thinking=document.createElement("div");thinking.className="thinking";thinking.textContent="Clank is thinking and checking the evidence…";$("messages").append(thinking);scroll();
  const started=performance.now();
  try {
    const response=await fetch("/api/chat",{method:"POST",headers:headers(),body:JSON.stringify({prompt,session_id:sessionId,model:$("model").value,autonomous:$("autonomous").checked,web_enabled:$("web-enabled").checked})});
    if(!response.ok){const data=await response.json();throw new Error(data.error||"Request failed");}
    $("welcome").hidden=true; message("user",prompt); $("prompt").value="";
    const reader=response.body.getReader(), decoder=new TextDecoder();let buffer="";
    function event(block) {
      const type=block.split("\n").find(l=>l.startsWith("event: "))?.slice(7);
      const raw=block.split("\n").find(l=>l.startsWith("data: "))?.slice(6);if(!raw)return;
      const data=JSON.parse(raw);
      if(type==="session"){sessionId=data.id;$("stop").disabled=false;}
      if(type==="delta"){thinking.remove();if(!current)current=message("assistant");current.text+=data.text;current.content.innerHTML=markdown(current.text);}
      if(type==="tool"){thinking.remove();if(current){finishMessage(current,"Reading your project");current=null;}tool(data);toolCount++;}
      if(type==="error")throw new Error(data.text);
      if(type==="notice")error(data.text);
      if(type==="progress"){const progress=document.createElement("div");progress.className="thinking";progress.textContent=data.text;$("messages").append(progress);}
      if(type==="done"){completed=true;thinking.remove();finishMessage(current,`${data.stopped_early?"Task incomplete":data.test_status?"Fixed verification: "+data.test_status:"Ollama answer"} · ${data.changed_files?.length||0} backed-up file edits · ${toolCount} tool calls · ${((performance.now()-started)/1000).toFixed(1)}s`);}
      scroll();
    }
    for(;;){const {done,value}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let boundary;while((boundary=buffer.indexOf("\n\n"))!==-1){const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);event(block);}if(done)break;}
    if(!completed)throw new Error("Connection ended before the answer completed. Partial output was not saved.");
    conversation=(await api("/api/history?id="+encodeURIComponent(sessionId))).messages;
  }catch(e){if(current)finishMessage(current,"Incomplete · not saved");error(e.message);}finally{thinking.remove();setBusy(false);try{if(sessionId)conversation=(await api("/api/history?id="+encodeURIComponent(sessionId))).messages;await loadSessions();}catch(e){error(e.message);}$("prompt").focus();}
}
$("composer").onsubmit=e=>{e.preventDefault();send($("prompt").value);};
$("prompt").onkeydown=e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send($("prompt").value);}};
document.querySelectorAll("[data-prompt]").forEach(b=>b.onclick=()=>send(b.dataset.prompt));
$("new").onclick=()=>{if(busy)return;closeHistory();sessionId=null;conversation=[];$("messages").replaceChildren();$("welcome").hidden=false;error("");loadSessions().catch(e=>error(e.message));$("prompt").focus();};
$("export").onclick=async()=>{if(!sessionId||!conversation.length){error("Send a message or open a saved conversation first.");return;}try{$("export-text").value=(await api("/api/export?id="+encodeURIComponent(sessionId))).markdown;$("copy-export").textContent="Copy Markdown";$("export-dialog").showModal();}catch(e){error(e.message);}};
$("close-export").onclick=()=>$("export-dialog").close();
$("copy-export").onclick=async()=>{try{await navigator.clipboard.writeText($("export-text").value);$("copy-export").textContent="Copied ✓";}catch{$("export-text").focus();$("export-text").select();$("copy-export").textContent="Press Ctrl+C to copy";}};
$("download-export").onclick=()=>{const url=URL.createObjectURL(new Blob([$("export-text").value],{type:"text/markdown;charset=utf-8"}));const a=document.createElement("a");a.href=url;a.download="clank-conversation.md";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$("model").onchange=()=>sessionStorage.setItem("clank-model",$("model").value);
$("retry").onclick=()=>init();
function updateMode(){const active=$("autonomous").checked,web=status?.web_available&&$("web-enabled").checked;$("web-notice").hidden=!web;$("autonomy-notice").hidden=!active;$("tools-badge").textContent=active?"EDIT + TEST":status?.tools?"READ ONLY":"CHAT ONLY";$("mode-caption").textContent="Ollama"+(active?" · Autonomous commands":" · Chat")+(web?" · Web on":" · Web off");}
$("web-enabled").onchange=()=>{sessionStorage.setItem("clank-web-enabled",String($("web-enabled").checked));updateMode();};
$("autonomous").onchange=updateMode;
$("stop").onclick=async()=>{try{const response=await fetch("/api/cancel",{method:"POST",headers:headers(),body:"{}"});const data=await response.json();if(!response.ok)throw new Error(data.error);$("stop").disabled=true;$("stop").textContent="Stopping…";error("Stop requested. Waiting for the current step; a silent model may take until its next event or read timeout.");}catch(e){error(e.message);}};
async function showChanges(){try{$("change-error").hidden=true;const data=await api("/api/changes");$("change-list").replaceChildren();if(!data.changes.length)$("change-list").textContent="No project changes yet.";data.changes.forEach(change=>{const row=document.createElement("div");row.className="change-row";const name=document.createElement("span");name.textContent=change.path+" · "+new Date(change.created_at).toLocaleString();const button=document.createElement("button");button.textContent=change.restored?"Undone":"Undo change";button.disabled=change.restored;button.onclick=async()=>{button.disabled=true;try{const response=await fetch("/api/restore",{method:"POST",headers:headers(),body:JSON.stringify({id:change.id})});const result=await response.json();if(!response.ok)throw new Error(result.error);await showChanges();}catch(e){$("change-error").textContent=e.message;$("change-error").hidden=false;button.disabled=false;}};row.append(name,button);$("change-list").append(row);});if(!$("changes-dialog").open)$("changes-dialog").showModal();}catch(e){error(e.message);}}
$("changes").onclick=showChanges;$("close-changes").onclick=()=>$("changes-dialog").close();
function closeHistory(){document.querySelector(".sidebar").classList.remove("open");$("show-history").setAttribute("aria-expanded","false");}
$("show-history").onclick=()=>{document.querySelector(".sidebar").classList.add("open");$("show-history").setAttribute("aria-expanded","true");$("close-history").focus();};
$("close-history").onclick=()=>{closeHistory();$("show-history").focus();};
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeHistory();});
async function init(){if(busy)return;error("");try{status=await api("/api/status");$("mode-caption").textContent="Ollama · Approved files only";$("model").replaceChildren();status.models.forEach(m=>$("model").add(new Option(m,m)));const preferred=sessionStorage.getItem("clank-model")||status.model||status.models.find(m=>m==="qwen3.5:4b")||status.models[0];if(preferred&&status.models.includes(preferred))$("model").value=preferred;
  $("connection").textContent=status.models.length?"Ollama · Connected":"Ollama · Unavailable";
  const root=status.roots[0]||"Your workspace";$("project-name").textContent=root.split(/[\\/]/).filter(Boolean).pop();$("project-path").textContent=status.roots.join(" · ");$("tools-badge").textContent=status.tools?"READ ONLY":"CHAT ONLY";
  $("web-control").hidden=!status.web_available;$("web-enabled").checked=status.web_available&&sessionStorage.getItem("clank-web-enabled")!=="false";$("autonomy-control").hidden=!status.autonomous_available;$("changes").hidden=!status.autonomous_available;$("autonomous").checked=false;$("test-command").textContent="Fixed tests: "+JSON.stringify(status.test_command||[]);updateMode();
  $("test-command").textContent += " · Task budget: "+status.max_tool_rounds+" tool rounds, progress review every 20.";
  if(status.error)error(status.error);await loadSessions();
 }catch(e){error(e.message);$("connection").textContent="Open the private workspace link to connect";}}
init();
