export async function api<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`)
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  return response.json() as Promise<T>
}
