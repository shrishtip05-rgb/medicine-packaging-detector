(function () {
  class ApiError extends Error {
    constructor(message, status, data) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.data = data || null;
    }
  }

  async function request(url, options) {
    const config = Object.assign(
      { credentials: "same-origin", headers: {} },
      options || {}
    );
    try {
      const response = await fetch(url, config);
      let payload = {};
      if (response.status !== 204) {
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
          payload = await response.json();
        } else {
          const text = await response.text();
          payload = text ? { error: text } : {};
        }
      }

      if (!response.ok) {
        if (response.status === 401 && !url.includes("/api/auth/login")) {
          window.dispatchEvent(new Event("mpd:unauthorized"));
        }
        throw new ApiError(
          payload.error || "The request could not be completed.",
          response.status,
          payload.data || null
        );
      }
      return payload;
    } catch (error) {
      if (error instanceof ApiError) {
        throw error;
      }
      throw new ApiError("Cannot reach the Flask application. Check that it is running.", 0);
    }
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[char];
    });
  }

  function formatDate(value, includeTime) {
    if (!value) {
      return "—";
    }
    const dateOnly = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
    const dateTime =
      typeof value === "string" &&
      /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?$/.test(
        value
      );
    if (typeof value === "string" && !dateOnly && !dateTime) {
      return value;
    }
    const parsed = dateOnly
      ? new Date(
          Number(value.slice(0, 4)),
          Number(value.slice(5, 7)) - 1,
          Number(value.slice(8, 10))
        )
      : new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return String(value);
    }
    const format = includeTime
      ? { dateStyle: "medium", timeStyle: "short" }
      : { dateStyle: "medium" };
    return new Intl.DateTimeFormat(undefined, format).format(parsed);
  }

  function formatConfidence(value) {
    if (value === null || value === undefined || value === "") {
      return "Unavailable";
    }
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(1) + "%" : "Unavailable";
  }

  function statusLabel(status) {
    return String(status || "UNKNOWN").replace(/_/g, " ");
  }

  function showAlert(element, message, tone) {
    if (!element) {
      return;
    }
    element.textContent = message;
    element.className = "alert" + (tone ? " alert-" + tone : "");
    element.hidden = !message;
  }

  window.MPDAPI = {
    ApiError: ApiError,
    request: request,
    escapeHtml: escapeHtml,
    formatDate: formatDate,
    formatConfidence: formatConfidence,
    statusLabel: statusLabel,
    showAlert: showAlert
  };
})();
