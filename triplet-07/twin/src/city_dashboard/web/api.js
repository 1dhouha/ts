export async function requestJSON(path, body, timeout = 2000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
      credentials: 'same-origin', redirect: 'error', signal: controller.signal,
      ...(body === undefined ? {} : {
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
      })
    });
    const value = await response.json();
    if (!response.ok || value.ok !== true || typeof value.data !== 'object' || value.data === null) {
      const code = value.error?.code;
      throw new Error(typeof code === 'string' && /^[A-Z_]{1,40}$/.test(code) ? code : 'INVALID_RESPONSE');
    }
    return value.data;
  } finally {
    clearTimeout(timer);
  }
}
