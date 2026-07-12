const API_BASE = '';

export async function fetchWeather(city) {
  const response = await fetch(`${API_BASE}/weather`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ city }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Failed to fetch weather data' }));
    throw new Error(error.error || `HTTP ${response.status}`);
  }

  return response.json();
}

export async function fetchHealth() {
  const response = await fetch(`${API_BASE}/health`);
  return response.json();
}
