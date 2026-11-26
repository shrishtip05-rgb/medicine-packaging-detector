(function () {
  const api = window.MPDAPI;
  const form = document.getElementById("login-form");
  const alertBox = document.getElementById("login-alert");
  const submitButton = document.getElementById("login-button");

  function setLoading(loading) {
    submitButton.disabled = loading;
    submitButton.innerHTML = loading
      ? '<span class="spinner" aria-hidden="true"></span><span>Signing in…</span>'
      : "<span>Sign in</span><span aria-hidden=\"true\">→</span>";
  }

  if (form.dataset.checkSession === "true") {
    api.request("/api/auth/me")
      .then(function () {
        window.location.replace("/dashboard");
      })
      .catch(function (error) {
        if (error.status !== 401) {
          api.showAlert(alertBox, error.message, "error");
        }
      });
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    api.showAlert(alertBox, "", "");
    if (!form.reportValidity()) {
      return;
    }
    setLoading(true);
    try {
      await api.request("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          identifier: document.getElementById("identifier").value.trim(),
          password: document.getElementById("password").value
        })
      });
      window.location.assign("/dashboard");
    } catch (error) {
      api.showAlert(alertBox, error.message, "error");
      setLoading(false);
    }
  });
})();
