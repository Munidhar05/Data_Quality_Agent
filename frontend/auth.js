(() => {
const workspacePaths = Object.values(pagePaths);
function returnPath() {
  const next = new URLSearchParams(location.search).get('next');
  if (!next) return '';
  try {
    const url = new URL(next, location.origin);
    return url.origin === location.origin && workspacePaths.includes(url.pathname) ? url.pathname + url.search : '';
  } catch { return ''; }
}
function authUrl(nextMode) {
  const url = new URL('/' + nextMode, location.origin);
  const next = returnPath() || (workspacePaths.includes(location.pathname.replace(/\/$/, '')) ? location.pathname.replace(/\/$/, '') + location.search : '');
  if (next) url.searchParams.set('next', next);
  return url.pathname + url.search;
}
let mode = 'login';
let submitting = false;
const form = document.getElementById('auth-form'), message = document.getElementById('auth-message'), submit = document.getElementById('auth-submit');
const phone = form.elements.phone, countryCode = form.elements.country_code, email = form.elements.email;
const emailPattern = /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$/;
function validateEmail() {
  email.value = email.value.trim();
  email.setCustomValidity(emailPattern.test(email.value) && email.value.split('@')[0].length <= 64 ? '' : 'Enter a valid email address, such as you@company.com.');
}
function validatePhone() {
  const option = countryCode.options[countryCode.selectedIndex];
  const lengths = option?.dataset.lengths ? JSON.parse(option.dataset.lengths) : countryCode.value === '+91' ? [10] : [];
  const max = lengths.length ? Math.max(...lengths) : 15 - (countryCode.value.length - 1);
  phone.removeAttribute('minlength');
  phone.removeAttribute('maxlength');
  phone.minLength = lengths.length ? Math.min(...lengths) : 1;
  phone.maxLength = Math.min(max, 15 - (countryCode.value.length - 1));
  phone.pattern = lengths.length ? `(?:${lengths.map(length => `[0-9]{${length}}`).join('|')})` : `[0-9]{1,${phone.maxLength}}`;
  const hint = `For ${option?.textContent || 'the selected country'}, enter ${lengths.join(' or ')} digits without the country code or domestic dialing prefix.`;
  phone.setCustomValidity(phone.disabled || new RegExp(`^${phone.pattern}$`).test(phone.value) ? '' : hint);
}
function validateText(input) {
  if (input.disabled) { input.setCustomValidity(''); return; }
  const value = input.value.trim();
  let error = '';
  if (!value) error = `Enter your ${input.name.replace('_', ' ')}.`;
  else if (input.name === 'company') {
    if (!/[\p{L}\p{N}]/u.test(value) || /[\p{Cc}\p{Cf}]/u.test(value)) error = 'Enter a valid company name.';
  } else if (!/^[\p{L}\p{M} '’-]+$/u.test(value) || !/\p{L}/u.test(value)) {
    error = 'Use letters, spaces, apostrophes, or hyphens for your name.';
  }
  input.setCustomValidity(error);
}
function validatePassword() {
  const input = form.elements.password;
  const minimum = mode === 'signup' ? 8 : 1;
  input.setCustomValidity(!input.value.trim() ? 'Enter your password.' : input.value.length < minimum ? 'Use at least 8 characters for your password.' : input.value.length > 128 ? 'Use at most 128 characters for your password.' : '');
}
function validateAll() {
  validateEmail(); validatePhone(); validatePassword();
  ['first_name', 'last_name', 'company'].forEach(name => validateText(form.elements[name]));
  countryCode.setCustomValidity(countryCode.disabled || [...countryCode.options].some(option => option.value === countryCode.value) ? '' : 'Select a country code.');
}
email.addEventListener('input', validateEmail);
email.addEventListener('blur', validateEmail);
phone.addEventListener('beforeinput', event => {
  if (event.data && /[^0-9]/.test(event.data) && event.inputType === 'insertText') event.preventDefault();
});
phone.addEventListener('paste', event => {
  event.preventDefault();
  const digits = (event.clipboardData?.getData('text') || '').replace(/[^0-9]/g, '');
  const start = phone.selectionStart ?? phone.value.length;
  const end = phone.selectionEnd ?? start;
  const capacity = Math.max(0, phone.maxLength - (phone.value.length - (end - start)));
  phone.setRangeText(digits.slice(0, capacity), start, end, 'end');
  phone.dispatchEvent(new Event('input', {bubbles:true}));
});
phone.addEventListener('input', () => {
  const position = phone.selectionStart;
  const original = phone.value;
  const prefixLength = original.slice(0, position ?? original.length).replace(/[^0-9]/g, '').length;
  phone.value = original.replace(/[^0-9]/g, '');
  if (position !== null) phone.setSelectionRange(prefixLength, prefixLength);
  validatePhone();
});
phone.addEventListener('blur', validatePhone);
countryCode.addEventListener('change', validatePhone);
form.elements.password.addEventListener('input', validatePassword);
['first_name', 'last_name', 'company'].forEach(name => {
  const input = form.elements[name];
  input.addEventListener('input', () => validateText(input));
  input.addEventListener('blur', () => { input.value = input.value.trim(); validateText(input); });
});
function setMode(next, updateUrl = true) {
  if (submitting) return;
  if (updateUrl && /^https?:$/.test(location.protocol) && location.pathname !== '/' + next) history.pushState({}, '', authUrl(next));
  mode = next; const signup = mode === 'signup';
  document.getElementById('auth-page').classList.toggle('signup-mode', signup);
  document.getElementById('signup-fields').hidden = !signup;
  form.querySelectorAll('#signup-fields input, #signup-fields select').forEach(input => { input.disabled = !signup; input.required = signup; });
  document.getElementById('auth-heading').textContent = signup ? 'Create your account' : 'Welcome back';
  document.getElementById('auth-description').textContent = signup ? 'Enter your details to get started.' : 'Sign in to your data quality workspace.';
  form.elements.password.minLength = signup ? 8 : 1;
  form.elements.password.autocomplete = signup ? 'new-password' : 'current-password';
  document.getElementById('password-hint').hidden = !signup;
  submit.textContent = signup ? 'Sign up' : 'Sign in';
  document.getElementById('auth-switch-prompt').textContent = signup ? "Already have an account?" : "Don't have an account?";
  document.getElementById('switch-auth').textContent = signup ? "Sign in" : "Sign up";
  message.textContent = '';
  form.querySelectorAll('input, select').forEach(input => input.setCustomValidity(''));
  form.elements.password.type = 'password';
  document.getElementById('toggle-password').textContent = 'Show';
  document.getElementById('toggle-password').setAttribute('aria-label', 'Show password');
  validatePhone();
}
window.setAuthMode = setMode;
window.showLogin = () => { document.body.classList.add('auth-pending'); if (/^https?:$/.test(location.protocol)) history.replaceState({}, '', authUrl('login')); setMode('login', false); };
function enter(user) { setWorkspaceUser(user); const next = returnPath(); if (next && /^https?:$/.test(location.protocol)) history.replaceState({}, '', next); document.getElementById('account-name').textContent = `${user.first_name} ${user.last_name}`; document.body.classList.remove('auth-pending'); form.reset(); initialize(); }
async function request(path, data) {
  let response;
  try { response = await fetch(`/api/auth/${path}`, data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {}); }
  catch { throw new Error('Unable to connect. Check your connection and try again.'); }
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : (Array.isArray(result.detail) ? result.detail.map(item => item.msg.replace(/^Value error, /, '')).join(' ') : 'Please check your details and try again.'));
  return result;
}
document.getElementById('switch-auth').onclick = () => setMode(mode === 'login' ? 'signup' : 'login');
document.getElementById('toggle-password').onclick = event => {
 const show = form.elements.password.type === 'password'; form.elements.password.type = show ? 'text' : 'password'; event.currentTarget.textContent = show ? 'Hide' : 'Show'; event.currentTarget.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
};
form.onsubmit = async event => {
 event.preventDefault(); if (submitting) return; validateAll(); if (!form.reportValidity()) return; const data = Object.fromEntries(new FormData(form));
 if (mode === 'signup') data.country_region = countryCode.options[countryCode.selectedIndex]?.dataset.region || 'IN';
 if (Object.entries(data).some(([key,value]) => key !== 'password' && !value.trim())) { message.textContent = 'Please complete all fields.'; return; }
 submitting = true; submit.disabled = true; document.querySelectorAll('#switch-auth').forEach(b => b.disabled = true); message.textContent = '';
 try { const result = await request(mode, data); if (mode === 'signup') { form.reset(); email.setCustomValidity(''); submitting = false; setMode('login'); form.elements.email.value = data.email; message.textContent = result.message; form.elements.password.focus(); } else enter(result.user); }
 catch (error) { message.textContent = error.message; }
 finally { submitting = false; submit.disabled = false; document.querySelectorAll('#switch-auth').forEach(b => b.disabled = false); }
};
document.getElementById('logout-button').onclick = async () => { try { await request('logout', {}); location.assign('/login'); } catch(error) { toast('Could not sign out: ' + error.message, true); } };
async function loadCountryCodes() {
  const countries = await request('countries');
  const displayNames = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['en'], {type:'region'}) : null;
  const named = countries.map(country => ({...country, name: displayNames ? displayNames.of(country.region) : country.region}));
  named.sort((a,b) => a.name.localeCompare(b.name, 'en'));
  const selected = countryCode.value;
  countryCode.replaceChildren(...named.map(country => {
    const option = document.createElement('option');
    option.value = country.code;
    option.dataset.region = country.region;
    option.dataset.lengths = JSON.stringify(country.lengths);
    option.textContent = `${country.name} (${country.code})`;
    option.defaultSelected = country.region === 'IN';
    return option;
  }));
  countryCode.value = selected || '+91';
  validatePhone();
}
loadCountryCodes().catch(() => { message.textContent = 'Unable to load country codes. Please refresh the page to try again.'; });
setMode(location.pathname.replace(/\/$/, '') === '/signup' ? 'signup' : 'login', false);
request('me').then(result => { if (['/login', '/signup'].includes(location.pathname.replace(/\/$/, ''))) { setMode(location.pathname.replace(/\/$/, '') === '/signup' ? 'signup' : 'login', false); return; } enter(result.user); }).catch(error => { const signup = location.pathname.replace(/\/$/, '') === '/signup'; if (/^https?:$/.test(location.protocol)) history.replaceState({}, '', authUrl(signup ? 'signup' : 'login')); setMode(signup ? 'signup' : 'login', false); if (error.message !== 'Please log in to continue.') message.textContent = error.message; });
})();
