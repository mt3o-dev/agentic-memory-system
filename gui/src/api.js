async function handle(res) {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`)
  return body
}

export const get = (url) => fetch(url).then(handle)

export const post = (url, payload = {}) =>
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(handle)

export const tierBadge = {
  'short-term': 'text-bg-secondary',
  'mid-term': 'text-bg-info',
  'long-term': 'text-bg-primary',
  lifetime: 'text-bg-dark',
}
