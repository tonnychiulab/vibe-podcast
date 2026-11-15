export async function readErrorDetail(res) {
  const raw = await res.text();
  try {
    const parsed = JSON.parse(raw);
    return parsed.detail || raw || `HTTP ${res.status}`;
  } catch {
    return raw || `HTTP ${res.status}`;
  }
}

export async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}
