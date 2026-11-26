"use strict";
let state = null;
let selectedId = null;
let selectedMessage = null;
let busy = false;
let catalogSignature = "";
const $ = id => document.getElementById(id);
const statusNames = {PENDING:"Pendiente",DRAFT:"Requiere preparación",SAMPLE_LOADED:"Muestra cargada",READY:"Resultado listo",SENDING:"Enviando",SENT:"Aceptada (AA)",RECEIVED:"Recibido",REJECTED:"Rechazado",ACCEPTED:"Aceptado (AA)",ERROR:"Error",DUPLICATE:"Repetido",INVALID:"Inválido"};
function feedback(message, error=false) { $("feedback").textContent = message; $("feedback").className = error ? "feedback error" : "feedback"; }
async function api(path, method="GET", body) {
  const response = await fetch(path, {method, headers: {"Content-Type":"application/json"}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Revisa los campos obligatorios y sus valores.");
  return data;
}
function display(raw) { return (raw || "Sin ACK.").replace(/\r/g,"\n"); }
function cell(row, text, className="") { const td=document.createElement("td"); td.textContent=text; td.className=className; row.append(td); return td; }
function actionButton(row, label, action) { const button=document.createElement("button"); button.type="button"; button.textContent=label; button.onclick=action; cell(row,"").append(button); }
function renderProfile() {
  const p=state.profile;
  for (const [id,value] of Object.entries({"profile-name":p.name,"profile-application":p.application,"profile-facility":p.facility,"destination-host":p.destinationHost,"destination-port":p.destinationPort})) $(id).value=value;
  $("listener-info").textContent=`Receptor MLLP: ${state.listener.host}:${state.listener.port}. En Docker usa el puerto publicado del host.`;
}
function selectOrder(identifier) {
  selectedId=identifier;
  const order=state.orders.find(item=>item.orderId===identifier);
  if (!order) return;
  $("result-empty").hidden=true;
  $("result-form").hidden=false;
  $("selected-label").textContent=`${order.orderId} · ${order.patientName} · ${statusNames[order.status] || order.status}`;
  $("result-inputs").replaceChildren();
  for (const code of order.tests) {
    const test=state.catalog.find(item=>item.code===code);
    const label=document.createElement("label"); label.textContent=`${test.name} (${test.unit})`;
    const input=document.createElement("input"); input.name=code; input.type="text"; input.inputMode=(test.valueType||"NM")==="NM"?"decimal":"text"; input.required=true; input.maxLength=(test.valueType||"NM")==="NM"?30:1000; input.value=order.values[code] || ""; input.autocomplete="off"; input.disabled=["SENT","SENDING"].includes(order.status); input.oninput=()=>showPreview(state.orders.find(item=>item.orderId===selectedId)); label.append(input); $("result-inputs").append(label);
  }
  $("result-form").querySelector("button").disabled=["SENT","SENDING"].includes(order.status);
  showPreview(order);
}
function showPreview(order) {
  const visible=visibleValues();
  const dirty=!!order.rawResult&&order.tests.some(code=>visible[code]!==String(order.values[code]??""));
  $("selected-label").textContent=`${order.orderId} · ${order.patientName} · ${statusNames[order.status] || order.status}`;
  $("result-form").querySelector("button").disabled=!state.equipment.configured||["SENT","SENDING"].includes(order.status);
  $("preview-panel").hidden=!order.rawResult;
  $("result-preview").textContent=display(order.rawResult);
  $("send-order").disabled=order.status!=="READY" || busy || dirty || !state.equipment.configured;
  $("result-dirty").hidden=!dirty&&!order.lastError;
  $("result-dirty").textContent=dirty?"Cambiaste los valores: vuelve a preparar el ORU. El mensaje anterior no se enviará con estos cambios pendientes.":order.lastError||"";
}
function visibleValues(){return Object.fromEntries([...$("result-inputs").querySelectorAll("input")].map(input=>[input.name,input.value]));}
function render() {
  $("metric-listeners").textContent=state.listener.active?"1":"0";
  $("metric-pending").textContent=state.orders.filter(order=>order.status!=="SENT").length;
  $("metric-messages").textContent=state.messages.length;
  $("metric-errors").textContent=state.messages.filter(message=>["ERROR","REJECTED","INVALID"].includes(message.status)).length;
  $("equipment-name").textContent=state.profile.name;
  $("equipment-listener").textContent=`HL7 / MLLP · Listener activo · ${state.listener.host}:${state.listener.port}`;
  $("equipment-destination").textContent=`Destino: ${state.profile.destinationHost}:${state.profile.destinationPort}`;
  $("equipment-tests").textContent=`${state.catalog.length} pruebas configuradas · Sin límite de catálogo`;
  const equipmentState=state.equipment.state;
  $("equipment-state").textContent=equipmentState;
  $("equipment-state").className="state " + equipmentState;
  $("equipment-progress").parentElement.hidden=true;
  $("equipment-reason").textContent=state.equipment.reason;
  $("order-form").querySelector('button[type="submit"]').disabled=!state.equipment.configured;
  window.liteDesignEditor?.update(state);
  window.liteCatalogEditor?.update(state);
  for(const id of ["order-analyzer","config-analyzer"]) { const option=document.createElement("option"); option.textContent=state.profile.name; option.value="lite"; $(id).replaceChildren(option); }
  const signature=JSON.stringify(state.catalog);
  if(signature!==catalogSignature) { renderTests(); renderCatalog(); catalogSignature=signature; }
  $("connection").textContent=`MLLP activo · puerto ${state.listener.port}`;
  $("orders").replaceChildren();
  for (const order of state.orders) {
    const row=document.createElement("tr");
    cell(row,order.orderId); cell(row,state.profile.name); cell(row,`${order.patientId} · ${order.patientName}`); cell(row,order.tests.join(", ")); cell(row,order.sampleId); cell(row,statusNames[order.status] || order.status,"status");
    actionButton(row,"Abrir",()=>{showPage("operate");selectOrder(order.orderId);}); $("orders").append(row);
  }
  $("orders-empty").hidden=state.orders.length>0;
  $("message-list").replaceChildren();
  for (const message of state.messages) {
    const row=document.createElement("tr");
    cell(row,new Date(message.created).toLocaleString()); cell(row,message.direction); cell(row,message.raw.split("\r")[0].split("|")[8] || "—"); cell(row,statusNames[message.status] || message.status,"status"); actionButton(row,"Ver",()=>selectLog(message.id)); $("message-list").append(row);
  }
  $("messages-empty").hidden=state.messages.length>0;
  if (selectedId) { const order=state.orders.find(item=>item.orderId===selectedId); if (order) showPreview(order); }
  if (selectedMessage) selectLog(selectedMessage);
}
function selectLog(id) {
  selectedMessage=id;
  const item=state.messages.find(message=>message.id===id);
  if (!item) return;
  $("message-detail").textContent=`${item.direction} · ${statusNames[item.status] || item.status} · ${item.detail}`;
  $("message-raw").textContent=display(item.raw); $("message-ack").textContent=display(item.ack);
}
async function refresh(initial=false) { state=await api("/api/state"); if(initial) renderProfile(); render(); }
function renderTests() {
  const checked=new Set([...$("test-options").querySelectorAll("input:checked")].map(input=>input.value));
  $("test-options").replaceChildren();
  for(const test of state.catalog.filter(test=>test.enabled!==false)) { const label=document.createElement("label"); const input=document.createElement("input"); input.type="checkbox"; input.name="tests"; input.value=test.code; input.checked=checked.has(test.code); label.append(input,document.createTextNode(test.code)); $("test-options").append(label); }
}
function renderCatalog() { window.liteCatalogEditor?.update(state); }
async function run(operation, button) {
  if(busy) return;
  busy=true; if(button) button.disabled=true;
  try { await operation(); await refresh(); } catch(error) { feedback(error.message,true); }
  finally { busy=false; if(button) button.disabled=false; if(state && selectedId) {const order=state.orders.find(item=>item.orderId===selectedId); if(order) showPreview(order);} }
}
const pages={dashboard:["dashboard","Panel general"],operate:["workspace","Operar muestras"],worklist:["worklist","Worklist"],messages:["messages","Mensajes"],analyzers:["dashboard","Analizadores"],configure:["settings","Configurar equipo"],protocol:["protocol","Perfil del equipo"]};
function showPage(name) {
  const pageName=name==="catalog" ? "protocol" : name;
  const entry=pages[pageName];
  if(!entry) return;
  feedback("");document.body.dataset.page=pageName;
  document.querySelectorAll(".page").forEach(page=>page.hidden=page.id!==entry[0]);
  document.querySelectorAll(".nav [data-page-link]").forEach(button=>button.classList.toggle("active",button.dataset.pageLink===pageName));
  $("page-title").textContent=entry[1]; $("compact-navigation").value=pageName;
  if(pageName==="protocol") window.liteDesignEditor?.setTab(name==="catalog"?"catalog":"fields");
}
document.querySelectorAll("[data-page-link]").forEach(button=>button.onclick=()=>showPage(button.dataset.pageLink));
function premiumMessage(feature) {
  $("premium-title").textContent=feature;
  $("premium-description").textContent="Esta función requiere la versión Premium. No está habilitada en la edición Lite.";
  $("premium-dialog").showModal();
}
document.addEventListener("click",event=>{const button=event.target.closest("[data-premium]");if(!button) return;event.preventDefault();event.stopPropagation();premiumMessage(button.dataset.premium);},true);
$("compact-navigation").onchange=event=>{const value=event.target.value;if(value==="pacs"||value==="dicom"){premiumMessage(value==="pacs" ? "Simulador PACS" : "Simulador DICOM");event.target.value=document.body.dataset.page;}else showPage(value);};
$("scan-form").onsubmit=event=>{event.preventDefault();run(async()=>{const identifier=$("scan-id").value.trim();const order=state.orders.find(item=>item.sampleId===identifier||item.orderId===identifier);if(!order)throw new Error("La muestra no existe en Worklist.");await api(`/api/orders/${encodeURIComponent(order.orderId)}/load`,"POST",{});await refresh();selectOrder(order.orderId);feedback("Muestra cargada. Ingresa los valores manualmente.");},event.submitter);};
$("order-form").onsubmit=event=>{event.preventDefault();run(async()=>{
  const tests=[...$("test-options").querySelectorAll("input:checked")].map(input=>input.value);
  if(!tests.length) throw new Error("Selecciona al menos una prueba.");
  const order=await api("/api/orders","POST",{orderId:$("order-id").value,sampleId:$("sample-id").value,studyCode:$("study-code").value,studyName:$("study-name").value,patientId:$("patient-id").value,patientName:$("patient-name").value,tests});
  await refresh(); selectOrder(order.orderId); feedback("Orden creada. Ingresa los valores manualmente.");
},event.submitter);};
$("result-form").onsubmit=event=>{event.preventDefault();run(async()=>{
  const values=visibleValues();
  await api(`/api/orders/${encodeURIComponent(selectedId)}/results`,"POST",{values}); feedback("ORU preparado. Revisa la vista previa y envíalo cuando estés listo.");
},event.submitter);};
$("send-order").onclick=event=>run(async()=>{const response=await api(`/api/orders/${encodeURIComponent(selectedId)}/send`,"POST",{values:visibleValues()});feedback(response.detail,response.status!=="ACCEPTED");},event.target);
$("refresh").onclick=event=>run(async()=>feedback("Registro actualizado."),event.target);
$("profile-form").onsubmit=event=>{event.preventDefault();run(async()=>{await api("/api/profile","PUT",{name:$("profile-name").value,application:$("profile-application").value,facility:$("profile-facility").value,destinationHost:$("destination-host").value,destinationPort:Number($("destination-port").value)}); feedback("Perfil guardado.");},event.submitter);};
$("validate").onclick=event=>run(async()=>{const response=await api("/api/validate","POST",{raw:$("raw").value}); $("validation").textContent=response.valid ? "Estructura básica válida dentro del alcance Lite." : response.errors.join(" ");},event.target);
$("send-raw").onclick=event=>run(async()=>{const response=await api("/api/send","POST",{raw:$("raw").value}); $("raw-ack").textContent=display(response.ack); feedback(response.detail,response.status!=="ACCEPTED");},event.target);
// Locked features only display this notice; no Premium API is called.
$("close-premium").onclick=()=>$("premium-dialog").close();
showPage("dashboard");
window.refreshLite=refresh;
refresh(true).catch(error=>feedback(error.message,true));
setInterval(()=>{if(!busy) refresh().catch(()=>{$("connection").textContent="Sin conexión con la aplicación";$("metric-listeners").textContent="0";});},4000);
