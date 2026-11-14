export type Data = Record<string, any>
export let operatorToken = sessionStorage.getItem('relay-token') || ''
export function setToken(token: string) { operatorToken = token; sessionStorage.setItem('relay-token', token) }
export async function api(path: string, options: RequestInit = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(operatorToken ? { Authorization: `Bearer ${operatorToken}` } : {}), ...options.headers } })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try { const body = await response.json(); message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail) } catch { /* preserve HTTP status */ }
    throw new Error(message)
  }
  return response.json()
}
export async function download(path: string, filename: string) {
  const response = await fetch(path, { headers: operatorToken ? {Authorization: `Bearer ${operatorToken}`} : {} })
  if (!response.ok) throw new Error(`Download failed (${response.status})`)
  const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url)
}
export const statusLabel = (status: string) => status?.replaceAll('_', ' ') || 'unavailable'
export const time = (value?: number) => value ? new Date(value * 1000).toLocaleString() : 'N/A'
export const number = (value: unknown, suffix = '') => typeof value === 'number' ? `${value.toLocaleString(undefined, {maximumFractionDigits: 1})}${suffix}` : 'N/A'
