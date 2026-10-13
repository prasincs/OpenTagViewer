'use strict';
const capability = location.hash.slice(1);
window.addEventListener('hashchange',()=>location.reload());
const statusNode = document.getElementById('status');
const qr = document.getElementById('qr');
const expiry = document.getElementById('expiry');
const apple = document.getElementById('apple');
const cancel = document.getElementById('cancel');
let timer, stopped = false, shownSvg;
function stop(message, restart=true) {
  stopped = true; clearTimeout(timer); qr.replaceChildren(); expiry.textContent = '';
  statusNode.textContent = message; cancel.disabled = true; cancel.hidden = true;
  document.getElementById('restart').hidden = !restart;
}
function render(state) {
  const messages = {waiting:'Waiting for your phone…', confirming:'Phone connected. Waiting for it to save the connection…', paired:'Phone pairing confirmed.', cancelled:'Pairing cancelled.', failed:'Pairing failed. Start again to retry.'};
  statusNode.textContent = messages[state.status] || 'Pairing unavailable.';
  if (state.qr_svg !== shownSvg) {
    qr.replaceChildren(); shownSvg = state.qr_svg;
    if (state.qr_svg) {
      const doc = new DOMParser().parseFromString(state.qr_svg, 'image/svg+xml');
      if (doc.documentElement.localName !== 'svg') throw new Error('Invalid QR');
      qr.appendChild(document.importNode(doc.documentElement, true));
    }
  }
  expiry.textContent = state.status === 'waiting' ? 'Current code expires at ' + new Date(state.expires_at*1000).toLocaleTimeString() + '. A new one will appear automatically.' : '';
  const appleMessages = {setup_running:'Complete Apple sign-in and verification in the local Mac terminal.', setup_failed:'Apple sign-in failed on the Mac. Run setup-on-mac.command again to retry.', ready:'Apple session is ready on the Mac.', setup_required:'Finish Apple sign-in locally on your Mac with setup-on-mac.command. This window will update when it is ready.', mac_error:'The Mac could not fetch reports. Check its Apple session and connection.'};
  apple.textContent = appleMessages[state.apple_status] || '';
  if (state.apple_status === 'setup_failed') stop('Apple sign-in failed. Retry setup-on-mac.command locally on the Mac.');
  else if (['cancelled','failed'].includes(state.status)) stop(messages[state.status]);
  else if (state.status === 'paired') {
    cancel.disabled = true;
    if (state.apple_status === 'ready') stop('Phone paired and Apple session ready. You can close this window.',false);
    else if (state.apple_status === 'mac_error') stop('Phone paired, but Apple report retrieval failed. Check the Mac setup.');
  }
}
async function request(path, method='GET') {
  const response = await fetch(path,{method,cache:'no-store',credentials:'omit',headers:{Authorization:'Setup '+capability}});
  if (!response.ok) throw new Error('Service unavailable');
  return response.json();
}
async function poll() {
  if (stopped) return;
  try { const state=await request('/v1/pairing-status'); if(stopped)return; render(state); }
  catch (_) { stop('Pairing service is unavailable or this window was replaced. Start again.'); }
  if (!stopped) timer = setTimeout(poll,2000);
}
cancel.addEventListener('click',async()=>{
  stopped=true;clearTimeout(timer);cancel.disabled=true;
  try { render(await request('/v1/pairing-cancel','POST')); }
  catch (_) { stop('Could not reach the Mac service. Close this window; the pending invitation will expire.'); }
});
if (/^[A-Za-z0-9_-]{43}$/.test(capability)) poll();
else stop('Open pair-phone.command on your Mac to start pairing.');
