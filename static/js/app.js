(function () {
  const api = window.MPDAPI;
  const pageContent = document.getElementById("page-content");
  const pageAlert = document.getElementById("page-alert");
  const minimumConfidence = Number(window.MPD_CONFIG.ocrMinConfidence);
  const fieldLabels = {
    medicine_name: "Medicine name",
    manufacturer: "Manufacturer",
    strength: "Strength",
    dosage_form: "Dosage form",
    batch_number: "Batch number",
    manufactured_on: "Manufacturing date",
    expiry_date: "Expiry date"
  };
  const statusValues = ["MATCH", "MISMATCH", "NEEDS_REVIEW", "EXPIRED"];
  let currentUser = null;

  function escape(value) {
    return api.escapeHtml(value);
  }

  function showLoading(message) {
    pageContent.innerHTML =
      '<div class="loading-panel"><span class="spinner" aria-hidden="true"></span><span>' +
      escape(message || "Loading…") +
      "</span></div>";
  }

  function pageHeading(eyebrow, title, description, action) {
    return (
      '<div class="page-heading"><div><span class="eyebrow">' +
      escape(eyebrow) +
      "</span><h1>" +
      escape(title) +
      "</h1><p>" +
      escape(description) +
      "</p></div>" +
      (action || "") +
      "</div>"
    );
  }

  function statusBadge(status) {
    const value = statusValues.includes(status) ? status : "NEEDS_REVIEW";
    return (
      '<span class="status-badge status-' +
      value.toLowerCase() +
      '">' +
      escape(api.statusLabel(value)) +
      "</span>"
    );
  }

  function outcomeBadge(outcome) {
    const value = statusValues.includes(outcome) ? outcome : "NEEDS_REVIEW";
    return (
      '<span class="field-outcome outcome-' +
      value.toLowerCase() +
      '">' +
      escape(api.statusLabel(value)) +
      "</span>"
    );
  }

  function severityBadge(severity) {
    const values = ["low", "medium", "high", "critical"];
    const value = values.includes(String(severity).toLowerCase())
      ? String(severity).toLowerCase()
      : "medium";
    return (
      '<span class="severity-badge severity-' +
      value +
      '">' +
      escape(value.toUpperCase()) +
      "</span>"
    );
  }

  function emptyState(title, message) {
    return (
      '<div class="empty-state"><div><span class="empty-icon" aria-hidden="true">⌕</span>' +
      "<strong>" +
      escape(title) +
      "</strong><span>" +
      escape(message) +
      "</span></div></div>"
    );
  }

  function setActiveNavigation(path) {
    document.querySelectorAll(".side-nav a").forEach(function (link) {
      link.removeAttribute("aria-current");
      const target = link.dataset.nav;
      if (
        (target === "dashboard" && (path === "/" || path === "/dashboard")) ||
        (target === "medicines" && path === "/medicines") ||
        (target === "batches" && path.indexOf("/batches") === 0) ||
        (target === "users" && path === "/users") ||
        (target === "audit" && path === "/audit") ||
        (target === "new-inspection" && path === "/inspections/new") ||
        (target === "history" &&
          (path === "/inspections" || /^\/inspections\/\d+\/?$/.test(path)))
      ) {
        link.setAttribute("aria-current", "page");
      }
    });
  }

  function bindShell() {
    document.getElementById("user-name").textContent =
      currentUser.username || currentUser.email || "Account";
    document.getElementById("user-role").textContent =
      currentUser.role === "pharmacist"
        ? "Inspector"
        : currentUser.role === "admin"
          ? "Admin"
          : "User";
    const name = currentUser.username || "U";
    document.getElementById("user-avatar").textContent = name.charAt(0).toUpperCase();
    document.getElementById("users-nav").hidden = currentUser.role !== "admin";
    document.getElementById("audit-nav").hidden = currentUser.role !== "admin";

    document.getElementById("logout-button").addEventListener("click", async function () {
      this.disabled = true;
      try {
        await api.request("/api/auth/logout", { method: "POST" });
        window.location.replace("/login");
      } catch (error) {
        this.disabled = false;
        api.showAlert(pageAlert, "Could not log out: " + error.message, "error");
      }
    });

    const menuButton = document.getElementById("mobile-menu");
    const sidebar = document.getElementById("sidebar");
    menuButton.addEventListener("click", function () {
      const expanded = menuButton.getAttribute("aria-expanded") === "true";
      menuButton.setAttribute("aria-expanded", String(!expanded));
      sidebar.classList.toggle("is-open", !expanded);
    });
    document.querySelectorAll(".side-nav a").forEach(function (link) {
      link.addEventListener("click", function () {
        sidebar.classList.remove("is-open");
        menuButton.setAttribute("aria-expanded", "false");
      });
    });
  }

  function inspectionRows(rows) {
    if (!rows.length) {
      return emptyState(
        "No inspections yet",
        "Uploaded inspections and their results will appear here."
      );
    }
    return (
      '<div class="table-wrap"><table class="data-table"><thead><tr>' +
      "<th>Inspection</th><th>Date</th><th>Medicine</th><th>Batch</th><th>Inspected by</th><th>Status</th><th>Findings</th><th></th>" +
      "</tr></thead><tbody>" +
      rows
        .map(function (row) {
          return (
            "<tr><td class=\"table-primary\">#" +
            escape(row.inspection_id) +
            "</td><td>" +
            escape(api.formatDate(row.inspected_at, true)) +
            "</td><td>" +
            escape(row.medicine_name || "Not selected") +
            "</td><td>" +
            escape(row.batch_number || "Not selected") +
            "</td><td>" +
            escape(row.username || "Unknown") +
            "</td><td>" +
            statusBadge(row.status) +
            "</td><td>" +
            escape(Number(row.error_count || 0) + Number(row.review_finding_count || 0)) +
            "</td><td><a class=\"table-link\" href=\"/inspections/" +
            encodeURIComponent(row.inspection_id) +
            "\">View report →</a></td></tr>"
          );
        })
        .join("") +
      "</tbody></table></div>"
    );
  }

  function analyticsTable(headers, rows, emptyTitle, emptyMessage) {
    if (!rows.length) {
      return emptyState(emptyTitle, emptyMessage);
    }
    return (
      '<div class="table-wrap"><table class="data-table"><thead><tr>' +
      headers.map(function (header) { return "<th>" + escape(header) + "</th>"; }).join("") +
      "</tr></thead><tbody>" +
      rows.map(function (row) {
        return "<tr>" + row.map(function (cell) { return "<td>" + cell + "</td>"; }).join("") + "</tr>";
      }).join("") +
      "</tbody></table></div>"
    );
  }

  function monthlyBars(rows) {
    if (!rows.length) {
      return emptyState("No monthly activity", "Inspection totals will appear after records exist.");
    }
    const maximum = Math.max.apply(null, rows.map(function (row) {
      return Number(row.inspection_count) || 0;
    }).concat([1]));
    return '<div class="monthly-bars">' + rows.map(function (row) {
      const count = Number(row.inspection_count) || 0;
      const width = Math.max(count ? 4 : 0, Math.round((count / maximum) * 100));
      return (
        '<div class="monthly-row"><span>' + escape(row.month) + '</span>' +
        '<div class="monthly-track"><div class="monthly-fill" style="width:' + width + '%"></div></div>' +
        '<strong>' + escape(count) + '</strong></div>'
      );
    }).join("") + "</div>";
  }

  async function renderDashboard() {
    showLoading("Loading inspection summary…");
    try {
      const payload = await api.request("/api/dashboard/summary");
      const data = payload.data;
      const stats = data.statistics;
      const expiryRows = (data.expiry_alerts || []).map(function (row) {
        const days = Number(row.days_until_expiry);
        const due = days < 0 ? Math.abs(days) + " days overdue" : days + " days";
        return [
          escape(row.medicine_name),
          escape(row.manufacturer_name),
          escape(row.batch_number),
          escape(api.formatDate(row.expiry_date, false)),
          escape(due)
        ];
      });
      const errorRows = (data.common_errors || []).map(function (row) {
        return [
          escape(titleCase(row.error_type)),
          severityBadge(row.severity),
          escape(row.occurrence_count),
          escape(api.formatDate(row.most_recent_at, true))
        ];
      });
      const batchRows = (data.batch_summaries || []).map(function (row) {
        return [
          escape(row.medicine_name),
          escape(row.batch_number),
          escape(row.inspection_count),
          escape(row.match_count),
          escape(row.mismatch_count),
          escape(row.review_count),
          escape(row.expired_count)
        ];
      });
      const duplicateRows = (data.duplicate_serials || []).map(function (row) {
        return [
          '<span class="table-primary">' + escape(row.serial_number) + "</span>",
          escape(row.inspection_count),
          escape(row.inspection_ids)
        ];
      });
      const cards = [
        ["Total inspections", stats.total_inspections, ""],
        ["Match", stats.match_count, "stat-match"],
        ["Mismatch", stats.mismatch_count, "stat-mismatch"],
        ["Needs review", stats.needs_review_count, "stat-review"],
        ["Expired", stats.expired_count, "stat-expired"]
      ];
      pageContent.innerHTML =
        pageHeading(
          "OVERVIEW",
          "Inspection dashboard",
          "A current snapshot of saved inspection records and recent findings.",
          '<a class="button button-primary" href="/inspections/new">＋ New inspection</a>'
        ) +
        '<section class="stats-grid" aria-label="Inspection totals">' +
        cards
          .map(function (card) {
            return (
              '<article class="stat-card ' +
              card[2] +
              '"><span class="stat-label">' +
              escape(card[0]) +
              '</span><strong class="stat-value">' +
              escape(card[1]) +
              "</strong></article>"
            );
          })
          .join("") +
        '</section><section class="panel"><div class="panel-heading"><div><h2>Recent inspections</h2>' +
        "<p>Latest records from the inspection history.</p></div>" +
        '<a class="table-link" href="/inspections">View history →</a></div>' +
        inspectionRows(data.recent_inspections || []) +
        '</section><section class="analytics-grid"><article class="panel"><div class="panel-heading"><div><h2>Monthly inspection trend</h2><p>Saved inspection totals by calendar month.</p></div></div>' +
        monthlyBars(data.monthly_trend || []) +
        '</article><article class="panel"><div class="panel-heading"><div><h2>Most common findings</h2><p>Aggregated from recorded error logs.</p></div></div>' +
        analyticsTable(["Finding", "Severity", "Occurrences", "Most recent"], errorRows, "No logged findings", "Error summaries will appear when inspections record mismatches.") +
        '</article></section><section class="panel"><div class="panel-heading"><div><h2>Batch-level activity</h2><p>Inspection outcomes grouped by registered batch.</p></div><a class="table-link" href="/batches">Search batches →</a></div>' +
        analyticsTable(["Medicine", "Batch", "Inspections", "Match", "Mismatch", "Review", "Expired"], batchRows, "No batch activity", "Register batches and inspect packages to populate this summary.") +
        '</section><section class="analytics-grid"><article class="panel"><div class="panel-heading"><div><h2>Expiring and expired batches</h2><p>Registered batches expiring within 30 days or already expired.</p></div></div>' +
        analyticsTable(["Medicine", "Manufacturer", "Batch", "Expiry", "Timing"], expiryRows, "No expiry alerts", "No registered batches expire within the next 30 days or are overdue.") +
        '</article><article class="panel"><div class="panel-heading"><div><h2>Repeated serial identifiers · review</h2><p>A repeat may be the same package inspected again; the system cannot distinguish that from serial reuse.</p></div></div>' +
        analyticsTable(["Serial identifier", "Inspections", "Inspection IDs"], duplicateRows, "No repeated serial identifiers", "No decoded serial identifier has appeared across multiple inspections.") +
        '</article></section><div class="disclaimer"><span class="note-mark" aria-hidden="true">i</span>' +
        "<span>OCR comparisons are screening support only and do not certify medicine authenticity or safety.</span></div>";
    } catch (error) {
      pageContent.innerHTML =
        pageHeading("OVERVIEW", "Inspection dashboard", "Statistics are loaded from saved database records.") +
        emptyState("Summary unavailable", error.message);
    }
  }

  async function renderBatches(search, status, offset) {
    const pageSize = 25;
    showLoading("Loading registered batch activity…");
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String(offset)
    });
    if (search) params.set("q", search);
    if (status) params.set("status", status);
    try {
      const payload = await api.request("/api/batches/summary?" + params.toString());
      const rows = payload.data || [];
      const tableRows = rows.map(function (row) {
        const days = Number(row.days_until_expiry);
        const expiryLabel =
          days < 0
            ? "Expired · " + Math.abs(days) + " days ago"
            : days <= 30
              ? "Expires in " + days + " days"
              : "Active";
        const outcomes = [
          row.match_count,
          row.mismatch_count,
          row.review_count,
          row.expired_count
        ].map(escape).join(" / ");
        return [
          escape(row.medicine_name),
          escape(row.manufacturer_name),
          escape(row.batch_number),
          escape(api.formatDate(row.expiry_date, false)),
          escape(expiryLabel),
          escape(row.inspection_count),
          outcomes,
          '<a class="table-link" href="/batches/' +
            encodeURIComponent(row.batch_id) +
            '">History →</a>'
        ];
      });
      pageContent.innerHTML =
        pageHeading(
          "BATCH RECORDS",
          "Batch tracking",
          "Search registered batches, expiry dates and their saved inspection outcomes."
        ) +
        '<section class="panel"><form class="toolbar" id="batch-search-form"><label class="field"><span class="field-label">Medicine, manufacturer, or batch</span><input id="batch-search" type="search" value="' +
        escape(search) +
        '" placeholder="Search registered batches"></label>' +
        '<label class="field"><span class="field-label">Expiry filter</span><select id="batch-status"><option value="">All batches</option><option value="active"' +
        (status === "active" ? " selected" : "") +
        '>Active · over 30 days</option><option value="expiring"' +
        (status === "expiring" ? " selected" : "") +
        '>Expiring · within 30 days</option><option value="expired"' +
        (status === "expired" ? " selected" : "") +
        '>Expired</option></select></label><button class="button button-primary" type="submit">Search</button></form>' +
        '<p class="table-secondary">Outcome order: MATCH / MISMATCH / NEEDS REVIEW / EXPIRED.</p>' +
        analyticsTable(
          ["Medicine", "Manufacturer", "Batch", "Expiry date", "Expiry status", "Inspections", "Outcomes", "History"],
          tableRows,
          "No registered batches found",
          "Try a different search or expiry filter."
        ) +
        '<div class="pagination"><button class="button button-outline button-small" id="batch-previous" type="button"' +
        (offset === 0 ? " disabled" : "") +
        '>Previous</button><span>Page ' +
        (Math.floor(offset / pageSize) + 1) +
        '</span><button class="button button-outline button-small" id="batch-next" type="button"' +
        (rows.length < pageSize ? " disabled" : "") +
        ">Next</button></div></section>";
      document.getElementById("batch-search-form").addEventListener("submit", function (event) {
        event.preventDefault();
        renderBatches(
          document.getElementById("batch-search").value.trim(),
          document.getElementById("batch-status").value,
          0
        );
      });
      document.getElementById("batch-previous").addEventListener("click", function () {
        renderBatches(search, status, Math.max(0, offset - pageSize));
      });
      document.getElementById("batch-next").addEventListener("click", function () {
        renderBatches(search, status, offset + pageSize);
      });
    } catch (error) {
      pageContent.innerHTML =
        pageHeading(
          "BATCH RECORDS",
          "Batch tracking",
          "Batch summaries are loaded from registered database records."
        ) + emptyState("Batch summary unavailable", error.message);
    }
  }

  async function renderBatchHistory(batchId, offset) {
    const pageSize = 25;
    showLoading("Loading batch inspection history…");
    try {
      const payload = await api.request(
        "/api/batches/" +
          encodeURIComponent(batchId) +
          "/inspections?limit=" +
          pageSize +
          "&offset=" +
          offset
      );
      const rows = payload.data || [];
      pageContent.innerHTML =
        pageHeading(
          "BATCH HISTORY",
          "Batch #" + batchId,
          "Saved inspections associated with this registered batch.",
          '<a class="button button-outline" href="/batches">← Batch tracking</a>'
        ) +
        '<section class="panel"><div class="panel-heading"><div><h2>Inspection records</h2><p>Repeated inspection alone is not proof of duplicate or counterfeit packaging.</p></div></div>' +
        inspectionRows(rows) +
        '<div class="pagination"><button class="button button-outline button-small" id="batch-history-previous" type="button"' +
        (offset === 0 ? " disabled" : "") +
        '>Previous</button><span>Page ' +
        (Math.floor(offset / pageSize) + 1) +
        '</span><button class="button button-outline button-small" id="batch-history-next" type="button"' +
        (rows.length < pageSize ? " disabled" : "") +
        ">Next</button></div></section>";
      document.getElementById("batch-history-previous").addEventListener("click", function () {
        renderBatchHistory(batchId, Math.max(0, offset - pageSize));
      });
      document.getElementById("batch-history-next").addEventListener("click", function () {
        renderBatchHistory(batchId, offset + pageSize);
      });
    } catch (error) {
      pageContent.innerHTML =
        pageHeading("BATCH HISTORY", "Batch #" + batchId, "Inspection history is loaded from saved records.") +
        emptyState("Batch history unavailable", error.message);
    }
  }

  async function renderAuditHistory(offset) {
    const pageSize = 50;
    showLoading("Loading audit records…");
    try {
      const payload = await api.request(
        "/api/audit?limit=" + pageSize + "&offset=" + offset
      );
      const rows = payload.data || [];
      const tableRows = rows.map(function (row) {
        let details = row.details;
        if (typeof details === "string") {
          try {
            details = JSON.parse(details);
          } catch (_error) {
            details = { summary: "Details are not valid JSON." };
          }
        }
        return [
          escape(api.formatDate(row.created_at, true)),
          escape(row.actor_username || "Deleted account"),
          escape(row.action),
          escape(row.target_type + (row.target_id ? " #" + row.target_id : "")),
          escape(JSON.stringify(details || {}))
        ];
      });
      pageContent.innerHTML =
        pageHeading(
          "ADMINISTRATION",
          "Audit trail",
          "Read-only history of account, catalog, inspection and report actions. Sensitive credentials are not recorded."
        ) +
        '<section class="panel"><div class="panel-heading"><div><h2>Recorded actions</h2><p>Append-only records from the audit migration.</p></div></div>' +
        analyticsTable(
          ["Time", "Actor", "Action", "Target", "Details"],
          tableRows,
          "No audit records",
          "Audit events will appear after the audit table is installed and actions are recorded."
        ) +
        '<div class="pagination"><button class="button button-outline button-small" id="audit-previous" type="button"' +
        (offset === 0 ? " disabled" : "") +
        '>Previous</button><span>Page ' +
        (Math.floor(offset / pageSize) + 1) +
        '</span><button class="button button-outline button-small" id="audit-next" type="button"' +
        (rows.length < pageSize ? " disabled" : "") +
        ">Next</button></div></section>";
      document.getElementById("audit-previous").addEventListener("click", function () {
        renderAuditHistory(Math.max(0, offset - pageSize));
      });
      document.getElementById("audit-next").addEventListener("click", function () {
        renderAuditHistory(offset + pageSize);
      });
    } catch (error) {
      pageContent.innerHTML =
        pageHeading("ADMINISTRATION", "Audit trail", "Audit logs are protected and loaded from the database.") +
        emptyState("Audit records unavailable", error.message);
    }
  }

  function manufacturerOptions(manufacturers, selectedId) {
    return (
      '<option value="">Select manufacturer</option>' +
      manufacturers
        .map(function (manufacturer) {
          const selected =
            String(manufacturer.manufacturer_id) === String(selectedId) ? " selected" : "";
          return (
            '<option value="' +
            escape(manufacturer.manufacturer_id) +
            '"' +
            selected +
            ">" +
            escape(manufacturer.manufacturer_name) +
            "</option>"
          );
        })
        .join("")
    );
  }

  function showMedicineEditor(medicine, manufacturers) {
    const panel = document.getElementById("medicine-editor");
    const form = document.getElementById("medicine-form");
    form.reset();
    form.dataset.medicineId = medicine ? medicine.medicine_id : "";
    document.getElementById("medicine-editor-title").textContent = medicine
      ? "Edit medicine"
      : "Add medicine";
    document.getElementById("medicine-name").value = medicine
      ? medicine.medicine_name
      : "";
    document.getElementById("medicine-manufacturer").innerHTML =
      manufacturerOptions(manufacturers, medicine && medicine.manufacturer_id);
    document.getElementById("medicine-strength").value = medicine
      ? medicine.strength
      : "";
    document.getElementById("medicine-dosage").value = medicine
      ? medicine.dosage_form
      : "";
    document.getElementById("medicine-barcode").value = medicine
      ? medicine.package_barcode || ""
      : "";
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function renderMedicines(searchTerm) {
    const query = searchTerm || "";
    showLoading("Loading medicines and manufacturers…");
    try {
      const values = await Promise.all([
        api.request("/api/medicines?q=" + encodeURIComponent(query)),
        api.request("/api/manufacturers")
      ]);
      const medicines = values[0].data || [];
      const manufacturers = values[1].data || [];
      const medicineMap = new Map(
        medicines.map(function (medicine) {
          return [String(medicine.medicine_id), medicine];
        })
      );
      const adminActions =
        currentUser.role === "admin"
          ? '<button class="button button-primary" type="button" id="add-medicine">＋ Add medicine</button>'
          : "";

      pageContent.innerHTML =
        pageHeading(
          "CATALOG",
          "Medicine management",
          "Review registered medicines, manufacturers, and the batches associated with each medicine.",
          adminActions
        ) +
        '<section class="panel"><div class="toolbar"><form class="search-form" id="medicine-search-form">' +
        '<input id="medicine-search" type="search" value="' +
        escape(query) +
        '" placeholder="Search medicine name" aria-label="Search medicine name">' +
        '<button class="button button-outline" type="submit">Search</button></form></div>' +
        '<div id="medicine-editor" class="batch-panel editor-panel" hidden>' +
        '<div class="panel-heading"><div><h2 id="medicine-editor-title">Add medicine</h2><p>Only administrators can change catalog records.</p></div></div>' +
        '<form id="medicine-form" class="form-grid">' +
        '<label class="field"><span class="field-label">Medicine name</span><input id="medicine-name" maxlength="160" required></label>' +
        '<label class="field"><span class="field-label">Manufacturer</span><select id="medicine-manufacturer" required>' +
        manufacturerOptions(manufacturers, "") +
        '</select></label><label class="field"><span class="field-label">Strength</span><input id="medicine-strength" maxlength="80" required placeholder="e.g. 500 mg"></label>' +
        '<label class="field"><span class="field-label">Dosage form</span><input id="medicine-dosage" maxlength="80" required placeholder="e.g. tablet"></label>' +
        '<label class="field"><span class="field-label">Package barcode <span class="field-hint">(optional)</span></span><input id="medicine-barcode" maxlength="64"></label>' +
        '<div class="form-actions"><button class="button button-primary" type="submit" id="medicine-save">Save medicine</button>' +
        '<button class="button button-outline" type="button" id="medicine-cancel">Cancel</button></div></form></div>' +
        (medicines.length
          ? '<div class="table-wrap"><table class="data-table"><thead><tr><th>Medicine</th><th>Manufacturer</th><th>Strength</th><th>Dosage form</th><th>Barcode</th><th>Catalog</th></tr></thead><tbody>' +
            medicines
              .map(function (medicine) {
                return (
                  "<tr><td><span class=\"table-primary\">" +
                  escape(medicine.medicine_name) +
                  '</span><span class="table-secondary">#' +
                  escape(medicine.medicine_id) +
                  "</span></td><td>" +
                  escape(medicine.manufacturer_name) +
                  "</td><td>" +
                  escape(medicine.strength) +
                  "</td><td>" +
                  escape(medicine.dosage_form) +
                  "</td><td>" +
                  escape(medicine.package_barcode || "—") +
                  '</td><td><div class="button-row"><button type="button" class="button button-outline button-small" data-action="batches" data-id="' +
                  escape(medicine.medicine_id) +
                  '">View batches</button>' +
                  (currentUser.role === "admin"
                    ? '<button type="button" class="button button-quiet button-small" data-action="edit" data-id="' +
                      escape(medicine.medicine_id) +
                      '">Edit</button>'
                    : "") +
                  "</div></td></tr>"
                );
              })
              .join("") +
            "</tbody></table></div>"
          : emptyState(
              query ? "No matching medicines" : "No medicines registered",
              query
                ? "Try a different search term."
                : "Medicines added to the catalog will appear here."
            )) +
        '<div id="batch-panel" class="batch-panel" hidden></div></section>' +
        '<section class="panel"><div class="panel-heading"><div><h2>Manufacturers</h2><p>Registered manufacturer records available to the catalog.</p></div></div>' +
        (manufacturers.length
          ? '<div class="table-wrap"><table class="data-table"><thead><tr><th>Manufacturer</th><th>Contact email</th><th>Phone</th><th>Address</th></tr></thead><tbody>' +
            manufacturers
              .map(function (manufacturer) {
                return (
                  "<tr><td class=\"table-primary\">" +
                  escape(manufacturer.manufacturer_name) +
                  "</td><td>" +
                  escape(manufacturer.contact_email || "—") +
                  "</td><td>" +
                  escape(manufacturer.contact_phone || "—") +
                  "</td><td>" +
                  escape(manufacturer.address || "—") +
                  "</td></tr>"
                );
              })
              .join("") +
            "</tbody></table></div>"
          : emptyState("No manufacturers registered", "Manufacturer records will appear here.")) +
        "</section>";

      const searchForm = document.getElementById("medicine-search-form");
      searchForm.addEventListener("submit", function (event) {
        event.preventDefault();
        renderMedicines(document.getElementById("medicine-search").value.trim());
      });

      const addButton = document.getElementById("add-medicine");
      if (addButton) {
        addButton.addEventListener("click", function () {
          showMedicineEditor(null, manufacturers);
        });
      }
      const editorForm = document.getElementById("medicine-form");
      if (editorForm) {
        document.getElementById("medicine-cancel").addEventListener("click", function () {
          document.getElementById("medicine-editor").hidden = true;
        });
        editorForm.addEventListener("submit", async function (event) {
          event.preventDefault();
          if (!editorForm.reportValidity()) {
            return;
          }
          const id = editorForm.dataset.medicineId;
          const values = {
            manufacturer_id: Number(document.getElementById("medicine-manufacturer").value),
            medicine_name: document.getElementById("medicine-name").value.trim(),
            strength: document.getElementById("medicine-strength").value.trim(),
            dosage_form: document.getElementById("medicine-dosage").value.trim(),
            package_barcode: document.getElementById("medicine-barcode").value.trim() || null
          };
          const saveButton = document.getElementById("medicine-save");
          saveButton.disabled = true;
          try {
            await api.request(
              id ? "/api/medicines/" + encodeURIComponent(id) : "/api/medicines",
              {
                method: id ? "PUT" : "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(values)
              }
            );
            api.showAlert(pageAlert, id ? "Medicine updated." : "Medicine added.", "success");
            await renderMedicines(query);
          } catch (error) {
            api.showAlert(pageAlert, error.message, "error");
            saveButton.disabled = false;
          }
        });
      }

      pageContent.querySelectorAll("[data-action]").forEach(function (button) {
        button.addEventListener("click", async function () {
          const medicine = medicineMap.get(button.dataset.id);
          if (!medicine) {
            return;
          }
          if (button.dataset.action === "edit") {
            showMedicineEditor(medicine, manufacturers);
            return;
          }
          const batchPanel = document.getElementById("batch-panel");
          batchPanel.hidden = false;
          batchPanel.innerHTML =
            '<div class="loading-panel"><span class="spinner"></span><span>Loading batches…</span></div>';
          try {
            const batchPayload = await api.request(
              "/api/medicines/" + encodeURIComponent(medicine.medicine_id) + "/batches"
            );
            const batches = batchPayload.data || [];
            batchPanel.innerHTML =
              "<h3>Batches · " +
              escape(medicine.medicine_name) +
              "</h3>" +
              (batches.length
                ? '<div class="table-wrap"><table class="data-table"><thead><tr><th>Batch number</th><th>Manufactured</th><th>Expires</th></tr></thead><tbody>' +
                  batches
                    .map(function (batch) {
                      return (
                        "<tr><td class=\"table-primary\">" +
                        escape(batch.batch_number) +
                        "</td><td>" +
                        escape(api.formatDate(batch.manufactured_on, false)) +
                        "</td><td>" +
                        escape(api.formatDate(batch.expiry_date, false)) +
                        "</td></tr>"
                      );
                    })
                    .join("") +
                  "</tbody></table></div>"
                : '<p class="field-hint">No batches are registered for this medicine.</p>');
          } catch (error) {
            batchPanel.innerHTML =
              '<p class="field-state-missing">' + escape(error.message) + "</p>";
          }
        });
      });
    } catch (error) {
      pageContent.innerHTML =
        pageHeading("CATALOG", "Medicine management", "Catalog data is loaded from the Flask API.") +
        emptyState("Catalog unavailable", error.message);
    }
  }

  function isUserActive(user) {
    return user.is_active === true || user.is_active === 1 || user.is_active === "1";
  }

  function userHasInspectionHistory(user) {
    return (
      user.has_inspection_history === true ||
      user.has_inspection_history === 1 ||
      user.has_inspection_history === "1"
    );
  }

  function userRoleLabel(role) {
    return role === "admin" ? "Admin" : "Inspector (pharmacist role)";
  }

  function showUserForm(user) {
    const panel = document.getElementById("user-editor");
    const form = document.getElementById("user-form");
    const isEditing = Boolean(user);
    const isCurrentUser =
      user && String(user.user_id) === String(currentUser.user_id);
    form.reset();
    form.dataset.userId = user ? user.user_id : "";
    document.getElementById("user-editor-title").textContent = isEditing
      ? "Edit user"
      : "Add user";
    document.getElementById("user-username").value = user ? user.username : "";
    document.getElementById("user-email").value = user ? user.email : "";
    document.getElementById("user-role-select").value = user
      ? user.role
      : "pharmacist";
    document.getElementById("user-role-select").disabled = Boolean(isCurrentUser);
    document.getElementById("user-save").textContent = isEditing
      ? "Save changes"
      : "Create user";
    document.getElementById("user-password-field").hidden = isEditing;
    document.getElementById("user-password").required = !isEditing;
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function renderUsers() {
    showLoading("Loading registered users…");
    try {
      const payload = await api.request("/api/users");
      const users = payload.data || [];
      pageContent.innerHTML =
        pageHeading(
          "ADMINISTRATION",
          "User management",
          "Create and maintain sign-in accounts. The schema stores the account name as username; it has no separate full-name column.",
          '<button class="button button-primary" type="button" id="add-user">＋ Add user</button>'
        ) +
        '<section class="panel"><div id="user-editor" class="batch-panel editor-panel" hidden>' +
        '<div class="panel-heading"><div><h2 id="user-editor-title">Add user</h2><p>Passwords are hashed by the server and never returned by the API.</p></div></div>' +
        '<form id="user-form" class="form-grid">' +
        '<label class="field"><span class="field-label">Name / username</span><input id="user-username" maxlength="80" autocomplete="username" required></label>' +
        '<label class="field"><span class="field-label">Email</span><input id="user-email" type="email" maxlength="254" autocomplete="email" required></label>' +
        '<label class="field"><span class="field-label">Role</span><select id="user-role-select"><option value="pharmacist">Inspector (pharmacist role)</option><option value="admin">Admin</option></select></label>' +
        '<label class="field" id="user-password-field"><span class="field-label">Initial password</span><input id="user-password" type="password" minlength="8" autocomplete="new-password" required><span class="field-hint">At least 8 characters. There is no separate password-reset feature in this form.</span></label>' +
        '<div class="form-actions"><button class="button button-primary" id="user-save" type="submit">Create user</button>' +
        '<button class="button button-outline" id="user-cancel" type="button">Cancel</button></div></form></div>' +
        '<div class="panel-heading"><div><h2>Registered accounts</h2><p>Role permissions are checked by Flask on every protected API request.</p></div></div>' +
        (users.length
          ? '<div class="table-wrap"><table class="data-table"><thead><tr><th>Name / username</th><th>Email</th><th>Role</th><th>Account</th><th>Created</th><th>Actions</th></tr></thead><tbody>' +
            users
              .map(function (user) {
                const active = isUserActive(user);
                const hasHistory = userHasInspectionHistory(user);
                const self =
                  String(user.user_id) === String(currentUser.user_id);
                return (
                  "<tr><td class=\"table-primary\">" +
                  escape(user.username) +
                  '<span class="table-secondary">#' +
                  escape(user.user_id) +
                  "</span></td><td>" +
                  escape(user.email) +
                  "</td><td>" +
                  escape(userRoleLabel(user.role)) +
                  '</td><td><span class="user-state ' +
                  (active ? "user-active" : "user-inactive") +
                  '">' +
                  (active ? "ACTIVE" : "INACTIVE") +
                  '</span></td><td>' +
                  escape(api.formatDate(user.created_at, false)) +
                  '</td><td><div class="button-row"><button type="button" class="button button-outline button-small" data-user-action="edit" data-user-id="' +
                  escape(user.user_id) +
                  '">Edit</button><button type="button" class="button button-quiet button-small" data-user-action="toggle" data-user-id="' +
                  escape(user.user_id) +
                  '" ' +
                  (self ? 'disabled title="You cannot deactivate your own account."' : "") +
                  ">" +
                  (active ? "Deactivate" : "Activate") +
                  '</button>' +
                  (hasHistory
                    ? '<span class="delete-unavailable" title="This account has inspection history and cannot be permanently deleted. Deactivate it instead.">History retained · deactivate instead</span>'
                    : '<button type="button" class="button button-outline button-small button-danger-outline" data-user-action="delete" data-user-id="' +
                      escape(user.user_id) +
                      '">Delete</button>') +
                  "</div></td></tr>"
                );
              })
              .join("") +
            "</tbody></table></div>"
          : emptyState("No user accounts found", "Use Add user to create an account.")) +
        "</section>";

      document.getElementById("add-user").addEventListener("click", function () {
        showUserForm(null);
      });
      document.getElementById("user-cancel").addEventListener("click", function () {
        document.getElementById("user-editor").hidden = true;
      });
      document.getElementById("user-form").addEventListener("submit", async function (event) {
        event.preventDefault();
        const form = event.currentTarget;
        if (!form.reportValidity()) {
          return;
        }
        const userId = form.dataset.userId;
        const values = {
          username: document.getElementById("user-username").value.trim(),
          email: document.getElementById("user-email").value.trim()
        };
        if (!userId) {
          values.password = document.getElementById("user-password").value;
          values.role = document.getElementById("user-role-select").value;
        } else if (!document.getElementById("user-role-select").disabled) {
          values.role = document.getElementById("user-role-select").value;
        }
        const saveButton = document.getElementById("user-save");
        saveButton.disabled = true;
        try {
          await api.request(
            userId ? "/api/users/" + encodeURIComponent(userId) : "/api/users",
            {
              method: userId ? "PATCH" : "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(values)
            }
          );
          await renderUsers();
          api.showAlert(
            pageAlert,
            userId ? "User account updated." : "User account created.",
            "success"
          );
        } catch (error) {
          api.showAlert(pageAlert, error.message, "error");
          saveButton.disabled = false;
        }
      });

      pageContent.querySelectorAll("[data-user-action]").forEach(function (button) {
        button.addEventListener("click", async function () {
          const user = users.find(function (item) {
            return String(item.user_id) === button.dataset.userId;
          });
          if (!user) {
            return;
          }
          if (button.dataset.userAction === "edit") {
            showUserForm(user);
            return;
          }
          if (button.dataset.userAction === "delete") {
            const confirmed = window.confirm(
              "Permanently delete this account?\n\nUsername: " +
                user.username +
                "\nEmail: " +
                user.email +
                "\n\nThis cannot be undone. Accounts with inspection history must be deactivated instead."
            );
            if (!confirmed) {
              return;
            }
            button.disabled = true;
            try {
              const response = await api.request(
                "/api/users/" + encodeURIComponent(user.user_id),
                { method: "DELETE" }
              );
              await renderUsers();
              api.showAlert(
                pageAlert,
                response.message || "User permanently deleted.",
                "success"
              );
            } catch (error) {
              api.showAlert(
                pageAlert,
                error.message +
                  (error.status === 409
                    ? " Deactivate it instead to preserve its history."
                    : ""),
                "error"
              );
              button.disabled = false;
            }
            return;
          }
          const active = isUserActive(user);
          button.disabled = true;
          try {
            await api.request("/api/users/" + encodeURIComponent(user.user_id), {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ is_active: !active })
            });
            await renderUsers();
            api.showAlert(
              pageAlert,
              active ? "User account deactivated." : "User account activated.",
              "success"
            );
          } catch (error) {
            api.showAlert(pageAlert, error.message, "error");
            button.disabled = false;
          }
        });
      });
    } catch (error) {
      pageContent.innerHTML =
        pageHeading("ADMINISTRATION", "User management", "Only administrators can manage accounts.") +
        emptyState("User list unavailable", error.message);
    }
  }

  function uploadWithProgress(formData, progressCallback) {
    return new Promise(function (resolve, reject) {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/inspections");
      xhr.withCredentials = true;
      xhr.upload.addEventListener("progress", function (event) {
        if (event.lengthComputable) {
          progressCallback(Math.round((event.loaded / event.total) * 100));
        }
      });
      xhr.addEventListener("load", function () {
        let payload = {};
        try {
          payload = xhr.responseText ? JSON.parse(xhr.responseText) : {};
        } catch (_error) {
          payload = {};
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          if (xhr.status === 401) {
            window.dispatchEvent(new Event("mpd:unauthorized"));
          }
          reject(new api.ApiError(payload.error || "Image upload failed.", xhr.status, payload.data || null));
          return;
        }
        resolve(payload);
      });
      xhr.addEventListener("error", function () {
        reject(new api.ApiError("The upload could not reach the Flask application.", 0));
      });
      xhr.send(formData);
    });
  }

  async function performOcr(inspectionId, retryButton, statusElement) {
    if (retryButton) {
      retryButton.disabled = true;
    }
    statusElement.innerHTML =
      '<span class="spinner" aria-hidden="true"></span> Running OCR and comparing registered fields…';
    try {
      await api.request("/api/inspections/" + encodeURIComponent(inspectionId) + "/ocr", {
        method: "POST"
      });
      window.location.assign("/inspections/" + encodeURIComponent(inspectionId));
    } catch (error) {
      const submitButton = document.getElementById("inspection-submit");
      if (submitButton) {
        submitButton.disabled = false;
      }
      statusElement.textContent =
        "Inspection #" +
        inspectionId +
        " was uploaded, but OCR/comparison did not finish: " +
        error.message;
      const quality = error.data && error.data.image_quality;
      if (quality) {
        const recommendations = (quality.recommendations || []).map(function (item) {
          return "<li>" + escape(item) + "</li>";
        }).join("");
        statusElement.innerHTML =
          '<div class="quality-warning"><strong>Image quality: ' +
          escape(quality.status || "UNSUITABLE") +
          " · score " +
          escape(quality.quality_score) +
          "/100</strong><ul>" +
          recommendations +
          "</ul><span>Retake and upload a clearer image to continue. The quality check does not guarantee OCR accuracy.</span></div>";
      }
      if (retryButton && (error.status === 0 || error.status >= 500)) {
        retryButton.hidden = false;
        retryButton.disabled = false;
      }
    }
  }

  async function renderNewInspection() {
    showLoading("Loading medicine options…");
    try {
      const payload = await api.request("/api/medicines");
      const medicines = payload.data || [];
      pageContent.innerHTML =
        pageHeading(
          "NEW REVIEW",
          "Inspect a package",
          "Upload a package image to create an inspection, extract OCR fields, and compare them with the selected catalog records."
        ) +
        '<section class="panel"><form id="inspection-form">' +
        '<div class="form-grid">' +
        '<label class="field"><span class="field-label">Expected medicine</span><select id="inspection-medicine"><option value="">Not selected · requires review</option>' +
        medicines
          .map(function (medicine) {
            return (
              '<option value="' +
              escape(medicine.medicine_id) +
              '">' +
              escape(medicine.medicine_name) +
              " · " +
              escape(medicine.strength) +
              " · " +
              escape(medicine.manufacturer_name) +
              "</option>"
            );
          })
          .join("") +
        '</select></label><label class="field"><span class="field-label">Expected batch</span><select id="inspection-batch" disabled><option value="">Not selected · requires review</option></select></label></div>' +
        '<div class="upload-grid" style="margin-top:18px"><label class="drop-zone" for="package-image"><span><span class="drop-icon" aria-hidden="true">↑</span><strong>Select a package image</strong><small>JPG, JPEG, or PNG · maximum ' +
        escape(formatBytes(window.MPD_CONFIG.maxUploadBytes)) +
        '</small><small id="selected-file-name">No file selected</small></span><input id="package-image" name="image" type="file" accept=".jpg,.jpeg,.png,image/jpeg,image/png" required></label>' +
        '<div class="preview-frame" id="preview-frame"><div class="preview-placeholder">The selected image preview will appear here.</div></div></div>' +
        '<div class="quality-guidance"><strong>For a useful OCR attempt</strong><span>Keep the package steady and in focus, use even lighting, include the full label, and align the camera with the package.</span><span>Very blurry, dark, overexposed, or low-resolution images may be blocked. Quality checks do not guarantee OCR accuracy.</span></div>' +
        '<div class="form-actions" style="margin-top:18px"><button class="button button-primary" id="inspection-submit" type="submit">Upload and inspect</button></div>' +
        '<div id="upload-status" class="field-hint" style="margin-top:12px" aria-live="polite"></div><div class="progress-track" id="upload-progress-track" hidden><div class="progress-fill" id="upload-progress"></div></div>' +
        '<button class="button button-outline button-small" id="retry-ocr" type="button" hidden>Retry OCR and comparison</button></form></section>' +
        '<div class="disclaimer"><span class="note-mark" aria-hidden="true">i</span><span>Comparison is a screening aid. A result is not proof of authenticity or pharmaceutical safety.</span></div>';

      const form = document.getElementById("inspection-form");
      const medicineSelect = document.getElementById("inspection-medicine");
      const batchSelect = document.getElementById("inspection-batch");
      const fileInput = document.getElementById("package-image");
      const previewFrame = document.getElementById("preview-frame");
      const fileName = document.getElementById("selected-file-name");
      const progressTrack = document.getElementById("upload-progress-track");
      const progressFill = document.getElementById("upload-progress");
      const statusElement = document.getElementById("upload-status");
      const retryButton = document.getElementById("retry-ocr");
      let previewUrl = null;
      let inspectionId = null;

      medicineSelect.addEventListener("change", async function () {
        batchSelect.disabled = true;
        batchSelect.innerHTML = '<option value="">Not selected · requires review</option>';
        if (!medicineSelect.value) {
          return;
        }
        try {
          const batchPayload = await api.request(
            "/api/medicines/" + encodeURIComponent(medicineSelect.value) + "/batches"
          );
          batchSelect.innerHTML =
            '<option value="">Not selected · requires review</option>' +
            (batchPayload.data || [])
              .map(function (batch) {
                return (
                  '<option value="' +
                  escape(batch.batch_id) +
                  '">' +
                  escape(batch.batch_number) +
                  " · expires " +
                  escape(api.formatDate(batch.expiry_date, false)) +
                  "</option>"
                );
              })
              .join("");
          batchSelect.disabled = false;
        } catch (error) {
          api.showAlert(pageAlert, error.message, "error");
        }
      });

      fileInput.addEventListener("change", function () {
        const file = fileInput.files && fileInput.files[0];
        if (previewUrl) {
          URL.revokeObjectURL(previewUrl);
          previewUrl = null;
        }
        if (!file) {
          fileName.textContent = "No file selected";
          previewFrame.innerHTML =
            '<div class="preview-placeholder">The selected image preview will appear here.</div>';
          return;
        }
        const extension = file.name.split(".").pop().toLowerCase();
        const accepted = ["jpg", "jpeg", "png"].includes(extension);
        const allowedMime =
          !file.type || ["image/jpeg", "image/png", "image/jpg"].includes(file.type);
        if (!accepted || !allowedMime) {
          fileInput.value = "";
          fileName.textContent = "Unsupported image type";
          previewFrame.innerHTML =
            '<div class="preview-placeholder field-state-missing">Choose a JPG, JPEG, or PNG image.</div>';
          api.showAlert(pageAlert, "Only JPG, JPEG, and PNG images are accepted.", "error");
          return;
        }
        const requestLimit = Number(window.MPD_CONFIG.maxUploadBytes);
        const estimatedFileLimit = Number.isFinite(requestLimit)
          ? Math.max(
              0,
              requestLimit - Math.min(4096, Math.max(256, requestLimit * 0.01))
            )
          : null;
        if (estimatedFileLimit !== null && file.size > estimatedFileLimit) {
          fileInput.value = "";
          fileName.textContent = "Image exceeds the server upload limit";
          previewFrame.innerHTML =
            '<div class="preview-placeholder field-state-missing">Choose a smaller image.</div>';
          api.showAlert(
            pageAlert,
            "The selected file exceeds the configured upload-size limit.",
            "error"
          );
          return;
        }
        api.showAlert(pageAlert, "", "");
        fileName.textContent = file.name + " · " + formatBytes(file.size);
        previewUrl = URL.createObjectURL(file);
        previewFrame.innerHTML =
          '<img src="' + previewUrl + '" alt="Preview of selected medicine package">';
      });

      retryButton.addEventListener("click", function () {
        if (inspectionId) {
          performOcr(inspectionId, retryButton, statusElement);
        }
      });

      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        api.showAlert(pageAlert, "", "");
        if (!form.reportValidity()) {
          return;
        }
        const file = fileInput.files && fileInput.files[0];
        if (!file) {
          api.showAlert(pageAlert, "Select a package image before submitting.", "error");
          return;
        }
        const formData = new FormData();
        formData.append("image", file);
        if (medicineSelect.value) {
          formData.append("medicine_id", medicineSelect.value);
        }
        if (batchSelect.value) {
          formData.append("batch_id", batchSelect.value);
        }
        const submitButton = document.getElementById("inspection-submit");
        submitButton.disabled = true;
        retryButton.hidden = true;
        progressTrack.hidden = false;
        statusElement.textContent = "Uploading image…";
        progressFill.style.width = "0%";
        try {
          const uploaded = await uploadWithProgress(formData, function (percent) {
            progressFill.style.width = percent + "%";
            statusElement.textContent = "Uploading image… " + percent + "%";
          });
          inspectionId = uploaded.data.inspection_id;
          progressFill.style.width = "100%";
          await performOcr(inspectionId, retryButton, statusElement);
        } catch (error) {
          statusElement.textContent = error.message;
          api.showAlert(pageAlert, error.message, "error");
          submitButton.disabled = false;
        }
      });
    } catch (error) {
      pageContent.innerHTML =
        pageHeading("NEW REVIEW", "Inspect a package", "Upload an image for OCR-assisted review.") +
        emptyState("Catalog unavailable", error.message);
    }
  }

  function outcomeDetails(finding, detail, expectedValue) {
    if (finding) {
      return {
        outcome: finding.outcome,
        reason: finding.reason
      };
    }
    if (detail && detail.extraction_status !== "READ") {
      return {
        outcome: "NEEDS_REVIEW",
        reason: detail.extraction_status === "UNREADABLE" ? "Field unreadable" : "Field not found"
      };
    }
    if (detail && detail.ocr_confidence != null && Number(detail.ocr_confidence) < minimumConfidence) {
      return { outcome: "NEEDS_REVIEW", reason: "OCR confidence below threshold" };
    }
    if (expectedValue == null) {
      return { outcome: "NEEDS_REVIEW", reason: "Expected catalog value unavailable" };
    }
    return { outcome: "NEEDS_REVIEW", reason: "Comparison unavailable" };
  }

  function renderInspectionResult(data) {
    const id = data.inspection_id;
    const expected = data.expected_details || {};
    const detected = new Map(
      (data.detected_details || []).map(function (detail) {
        return [detail.field_name, detail];
      })
    );
    let scanResult = null;
    const scanDetail = detected.get("barcode");
    if (scanDetail && scanDetail.detected_value) {
      try {
        scanResult = JSON.parse(scanDetail.detected_value);
      } catch (_error) {
        scanResult = { status: "UNREADABLE", codes: [] };
      }
    }
    const findings = new Map(
      (data.comparison_findings || []).map(function (finding) {
        return [finding.field_name, finding];
      })
    );
    const fields = Object.keys(fieldLabels);
    const fieldRows = fields
      .map(function (fieldName) {
        const detail = detected.get(fieldName);
        const expectedValue = expected[fieldName];
        const detectedValue = detail && detail.detected_value;
        const result = outcomeDetails(findings.get(fieldName), detail, expectedValue);
        let stateClass = "";
        let extractionLabel = detail ? detail.extraction_status : "NOT_FOUND";
        if (!detail || detail.extraction_status !== "READ") {
          stateClass = "field-state-missing";
        } else if (
          detail.ocr_confidence == null ||
          Number(detail.ocr_confidence) < minimumConfidence
        ) {
          stateClass = "field-state-low";
        }
        const confidenceValue = detail
          ? api.formatConfidence(detail.ocr_confidence)
          : "Unavailable";
        const expectedDisplay =
          expectedValue == null
            ? '<span class="value-empty">Not selected</span>'
            : ["manufactured_on", "expiry_date"].includes(fieldName)
              ? escape(api.formatDate(expectedValue, false))
              : escape(expectedValue);
        return (
          "<tr><td class=\"table-primary\">" +
          escape(fieldLabels[fieldName]) +
          "</td><td>" +
          expectedDisplay +
          "</td><td>" +
          (detectedValue == null
            ? '<span class="value-empty">No readable value</span>'
            : escape(detectedValue)) +
          '<span class="field-state ' +
          stateClass +
          '">' +
          escape(extractionLabel) +
          " · confidence " +
          escape(confidenceValue) +
          "</span></td><td>" +
          outcomeBadge(result.outcome) +
          '<span class="field-state">' +
          escape(readableReason(result.reason)) +
          "</span></td></tr>"
        );
      })
      .join("");

    const errorLogs = data.error_logs || [];
    const findingsHtml = errorLogs.length
      ? '<div class="table-wrap"><table class="data-table"><thead><tr><th>Finding</th><th>Expected</th><th>Detected</th><th>Severity</th></tr></thead><tbody>' +
        errorLogs
          .map(function (finding) {
            return (
              "<tr><td class=\"table-primary\">" +
              escape(fieldLabels[finding.error_type] || titleCase(finding.error_type)) +
              "</td><td>" +
              escape(finding.expected_value == null ? "Unavailable" : finding.expected_value) +
              "</td><td>" +
              escape(finding.detected_value == null ? "Unavailable" : finding.detected_value) +
              "</td>" +
              '<td class="severity-cell">' +
              severityBadge(finding.severity) +
              "</td></tr>"
            );
          })
          .join("") +
        "</tbody></table></div>"
      : emptyState("No logged mismatches", "No Error_Logs findings are recorded for this inspection.");

    const comparisonNotes = (data.comparison_findings || []).filter(function (finding) {
      return (
        finding.outcome === "NEEDS_REVIEW" ||
        finding.field_name === "expired_batch"
      );
    });
    const noteHtml = comparisonNotes.length
      ? '<div class="result-cards">' +
        comparisonNotes
          .map(function (finding) {
            return (
              '<article class="result-card"><span>' +
              escape(
                finding.field_name === "expired_batch"
                  ? "Expiry finding"
                  : fieldLabels[finding.field_name] || "Manual review"
              ) +
              "</span><strong>" +
              escape(readableReason(finding.reason)) +
              '</strong><span class="field-state">' +
              escape(finding.expected_value == null ? "Expected value unavailable" : "Expected: " + finding.expected_value) +
              (finding.detected_value == null
                ? ""
                : " · Detected: " + finding.detected_value) +
              "</span></article>"
            );
          })
          .join("") +
        "</div>"
      : emptyState("No manual-review flags", "All required fields were readable and above the configured confidence threshold.");
    const scanCodes = scanResult && Array.isArray(scanResult.codes) ? scanResult.codes : [];
    const serialDuplicates =
      scanResult && Array.isArray(scanResult.duplicate_serials_across_inspections)
        ? scanResult.duplicate_serials_across_inspections
        : [];
    const scanMarkup = (scanCodes.length
      ? '<div class="scan-list">' + scanCodes.map(function (code) {
          const identifiers = code.identifiers || {};
          const identifierSummary = [
            identifiers.gtin ? "GTIN: " + identifiers.gtin : "",
            identifiers.batch_number ? "Batch: " + identifiers.batch_number : "",
            identifiers.expiry_date ? "Expiry: " + identifiers.expiry_date : "",
            identifiers.serial_number ? "Serial: " + identifiers.serial_number : ""
          ].filter(Boolean).join(" · ");
          return '<article class="result-card"><span>' + escape(code.format || "Code") +
            (code.is_url ? " · URL text only" : "") + '</span><strong>' +
            escape(code.value) + '</strong><span class="field-state">' +
            escape(identifierSummary || "No supported product identifiers extracted") +
            "</span></article>";
        }).join("") + "</div>"
      : emptyState(
          "No code decoded",
          "No supported QR/barcode value was stored. This does not by itself indicate a packaging problem."
        )) +
      (serialDuplicates.length
        ? '<div class="quality-warning"><strong>Repeated serial identifier · manual review</strong><ul>' +
          serialDuplicates.map(function (duplicate) {
            return "<li>" + escape(duplicate.serial_number) + " also appeared in inspection #" +
              escape(duplicate.other_inspection_id) + ".</li>";
          }).join("") +
          "</ul><span>This may be another inspection of the same physical package; this system cannot distinguish that from serial reuse.</span></div>"
        : "");
    const quality = data.image_quality || {};
    const qualityRecommendations = Array.isArray(quality.recommendations)
      ? quality.recommendations
      : [];
    const qualityMarkup = quality.status
      ? '<div class="quality-summary"><strong>' +
        escape(quality.status) +
        (quality.quality_score == null
          ? ""
          : " · score " + escape(quality.quality_score) + "/100") +
        "</strong>" +
        (quality.dimensions
          ? '<span>' +
            escape(quality.dimensions.width) +
            " × " +
            escape(quality.dimensions.height) +
            " pixels · blur variance " +
            escape(quality.blur_variance) +
            " · mean luminance " +
            escape(quality.mean_luminance) +
            "</span>"
          : "") +
        '<span class="field-state">' +
        escape((quality.issues || []).map(function (issue) { return issue.code; }).join(", ") || "No quality issues detected") +
        "</span>" +
        (qualityRecommendations.length
          ? "<ul>" +
            qualityRecommendations.map(function (item) { return "<li>" + escape(item) + "</li>"; }).join("") +
            "</ul>"
          : "") +
        '<span class="field-state">Heuristic assessment only; it does not guarantee OCR accuracy.</span></div>'
      : emptyState(
          "Quality assessment unavailable",
          "The saved image could not be assessed."
        );

    pageContent.innerHTML =
      pageHeading(
        "INSPECTION REPORT",
        "Inspection #" + id,
        "Review expected catalog values alongside OCR-extracted package fields.",
        '<div class="heading-actions"><a class="button button-outline" href="/api/inspections/' +
          encodeURIComponent(id) +
          '/report.pdf" target="_blank" rel="noopener">Download PDF</a><a class="button button-outline" href="/inspections">← Inspection history</a></div>'
      ) +
      '<section class="inspection-banner"><div><span class="field-hint">Inspection date</span><div class="table-primary">' +
      escape(api.formatDate(data.inspected_at, true)) +
      '</div><span class="field-hint">Inspected by ' +
      escape(data.username || "Unknown user") +
      "</span></div>" +
      statusBadge(data.status) +
      '</section><div class="result-layout"><div><section class="panel"><div class="panel-heading"><div><h2>Expected vs detected</h2>' +
      "<p>Per-field OCR confidence threshold: " +
      escape(minimumConfidence) +
      "%</p></div></div>" +
      '<div class="table-wrap"><table class="data-table"><thead><tr><th>Field</th><th>Expected</th><th>Detected</th><th>Assessment</th></tr></thead><tbody>' +
      fieldRows +
      "</tbody></table></div></section>" +
      '<section class="panel"><div class="panel-heading"><div><h2>Recorded findings</h2><p>Append-only database error and review logs.</p></div></div>' +
      findingsHtml +
      '</section><section class="panel"><div class="panel-heading"><div><h2>Manual-review flags</h2><p>Unreadable, missing, uncertain, or non-loggable differences.</p></div></div>' +
      noteHtml +
      '</section><section class="panel"><div class="panel-heading"><div><h2>Image quality</h2><p>Heuristic checks performed before OCR processing.</p></div></div>' +
      qualityMarkup +
      '</section><section class="panel"><div class="panel-heading"><div><h2>QR / barcode scan</h2><p>Decoded text and identifiers are not proof of authenticity; links are not opened.</p></div></div>' +
      scanMarkup +
      "</section></div><aside><section class=\"panel\"><div class=\"panel-heading\"><div><h2>Package image</h2><p>Stored image for this inspection.</p></div></div>" +
      '<img class="result-image" id="result-image" src="/api/inspections/' +
      encodeURIComponent(id) +
      '/image" alt="Uploaded medicine package image"><div class="image-unavailable" id="image-unavailable" hidden>Stored image is unavailable.</div></section>' +
      '<div class="disclaimer"><span class="note-mark" aria-hidden="true">i</span><span>This screening result is not proof of authenticity or pharmaceutical safety.</span></div></aside></div>';

    document.getElementById("result-image").addEventListener("error", function () {
      this.hidden = true;
      document.getElementById("image-unavailable").hidden = false;
    });
  }

  async function renderInspection(id) {
    showLoading("Loading inspection report…");
    try {
      const payload = await api.request("/api/inspections/" + encodeURIComponent(id));
      renderInspectionResult(payload.data);
    } catch (error) {
      pageContent.innerHTML =
        pageHeading("INSPECTION REPORT", "Inspection #" + id, "The report is loaded from saved inspection data.") +
        emptyState("Report unavailable", error.message);
    }
  }

  function readableReason(reason) {
    const reasons = {
      expected_value_unavailable: "Expected catalog value unavailable",
      detected_value_unavailable: "OCR value not found or unreadable",
      ocr_confidence_below_threshold: "OCR confidence below threshold",
      date_format_unrecognized_or_ambiguous: "Date format is ambiguous; manual review required",
      strength_format_unrecognized: "Strength format could not be compared safely",
      manufacturing_date_difference_requires_manual_review:
        "Manufacturing date differs; manual review required",
      registered_batch_expired_at_inspection: "Registered batch was expired at inspection",
      values_differ: "Expected and detected values differ"
    };
    return reasons[reason] || reason || "No review reason";
  }

  function titleCase(value) {
    return String(value || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, function (letter) {
        return letter.toUpperCase();
      });
  }

  async function renderHistory(status, offset, search) {
    const pageSize = 25;
    showLoading("Loading inspection history…");
    const query = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
    if (status) query.set("status", status);
    if (search) query.set("q", search);
    try {
      const payload = await api.request("/api/inspections?" + query.toString());
      const rows = payload.data || [];
      pageContent.innerHTML =
        pageHeading(
          "RECORDS",
          "Inspection history",
          "Review saved inspections and open an individual result report.",
          '<a class="button button-primary" href="/inspections/new">＋ New inspection</a>'
        ) +
        '<section class="panel"><form class="toolbar" id="history-filter-form"><label class="field"><span class="field-label">Medicine, manufacturer, or batch</span><input id="history-search" type="search" value="' + escape(search || "") + '" placeholder="Search history"></label>' +
        '<label class="field"><span class="field-label">Status</span><select id="history-status"><option value="">All statuses</option>' +
        statusValues
          .map(function (value) {
            return (
              '<option value="' +
              value +
              '"' +
              (value === status ? " selected" : "") +
              ">" +
              escape(api.statusLabel(value)) +
              "</option>"
            );
          })
          .join("") +
        '</select></label><button class="button button-primary" type="submit">Filter</button></form>' +
        inspectionRows(rows) +
        '<div class="pagination"><button class="button button-outline button-small" id="history-previous" type="button"' +
        (offset === 0 ? " disabled" : "") +
        '>Previous</button><span>Page ' +
        (Math.floor(offset / pageSize) + 1) +
        '</span><button class="button button-outline button-small" id="history-next" type="button"' +
        (rows.length < pageSize ? " disabled" : "") +
        ">Next</button></div></section>";
      document.getElementById("history-filter-form").addEventListener("submit", function (event) {
        event.preventDefault();
        renderHistory(
          document.getElementById("history-status").value,
          0,
          document.getElementById("history-search").value.trim()
        );
      });
      document.getElementById("history-previous").addEventListener("click", function () {
        renderHistory(status, Math.max(0, offset - pageSize), search);
      });
      document.getElementById("history-next").addEventListener("click", function () {
        renderHistory(status, offset + pageSize, search);
      });
    } catch (error) {
      pageContent.innerHTML =
        pageHeading("RECORDS", "Inspection history", "Saved records are loaded from the Flask API.") +
        emptyState("History unavailable", error.message);
    }
  }

  function formatBytes(bytes) {
    const number = Number(bytes);
    if (!Number.isFinite(number) || number < 0) {
      return "server-configured limit";
    }
    if (number < 1024 * 1024) {
      return Math.max(1, Math.round(number / 1024)) + " KiB";
    }
    return (number / (1024 * 1024)).toFixed(1) + " MiB";
  }

  async function start() {
    const path = window.location.pathname;
    setActiveNavigation(path);
    window.addEventListener("mpd:unauthorized", function () {
      window.location.replace("/login");
    });
    try {
      const userPayload = await api.request("/api/auth/me");
      currentUser = userPayload.data;
      bindShell();
    } catch (error) {
      if (error.status === 401) {
        window.location.replace("/login");
        return;
      }
      api.showAlert(pageAlert, error.message, "error");
      return;
    }

    if (path === "/" || path === "/dashboard") {
      await renderDashboard();
    } else if (path === "/medicines") {
      await renderMedicines(new URLSearchParams(window.location.search).get("q") || "");
    } else if (path === "/users" && currentUser.role === "admin") {
      await renderUsers();
    } else if (path === "/audit" && currentUser.role === "admin") {
      const offset = Math.max(
        0,
        Number(new URLSearchParams(window.location.search).get("offset") || 0)
      );
      await renderAuditHistory(offset);
    } else if (path === "/inspections/new") {
      await renderNewInspection();
    } else if (path === "/batches") {
      const params = new URLSearchParams(window.location.search);
      await renderBatches(
        params.get("q") || "",
        params.get("status") || "",
        Math.max(0, Number(params.get("offset") || 0))
      );
    } else if (/^\/batches\/\d+\/?$/.test(path)) {
      const match = path.match(/^\/batches\/(\d+)\/?$/);
      await renderBatchHistory(match[1], 0);
    } else if (path === "/inspections") {
      const params = new URLSearchParams(window.location.search);
      const status = params.get("status") || "";
      const offset = Math.max(0, Number(params.get("offset") || 0));
      await renderHistory(
        statusValues.includes(status) ? status : "",
        offset,
        params.get("q") || ""
      );
    } else {
      const match = path.match(/^\/inspections\/(\d+)\/?$/);
      if (match) {
        await renderInspection(match[1]);
      } else {
        pageContent.innerHTML = emptyState("Page not found", "Use the navigation to open a workspace page.");
      }
    }
  }

  start();
})();
