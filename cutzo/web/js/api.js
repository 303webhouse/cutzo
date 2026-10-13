// Talking to Cutzo's engine. Every change carries the X-Cutzo-Source header,
// which the engine requires (it blocks changes from other websites).

export class ApiError extends Error {
  constructor(message, status, hint, data) {
    super(message);
    this.status = status;
    this.hint = hint;
    this.data = data || {};
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: { Accept: "application/json" } };
  if (method !== "GET") {
    options.headers["Content-Type"] = "application/json";
    options.headers["X-Cutzo-Source"] = "app";
    options.body = JSON.stringify(body ?? {});
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError(
      "Cutzo's engine isn't answering. If you closed it, open Cutzo again from the desktop shortcut.",
      0,
    );
  }
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    throw new ApiError(data.error || `Cutzo answered with error ${response.status}.`, response.status, data.hint, data.data);
  }
  return data;
}

export const epPath = (id) => `/api/episodes/${encodeURIComponent(id)}`;
