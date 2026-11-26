# Medicine Packaging Detector — Flask API

This repository contains a modular Flask API and the existing MySQL schema in
[`database/schema.sql`](database/schema.sql). Authenticated users can upload
package images and request local OpenCV/Tesseract text extraction for an
inspection. OCR is a screening aid only; the application does not establish
medicine identity, validate packaging, or prove authenticity.

## Project overview

Medicine Packaging Detector is a college DBMS project for recording medicine
catalog and batch information, inspecting package images, and surfacing
potential differences between extracted text and registered records. It is a
screening and review aid, not a certified counterfeit detector or medicine
safety system.

### Problem statement

Manually comparing printed package details with registered medicine and batch
records can be slow and inconsistent. This prototype combines image checks,
OCR, code scanning, and database comparisons to flag observations for human
review; it does not replace professional verification.

### Technology stack

- Python and Flask for the web application and JSON API.
- MySQL with `mysql-connector-python` for normalized catalog and inspection
  records.
- HTML, CSS, and vanilla JavaScript for the same-origin responsive interface.
- OpenCV and Tesseract via `pytesseract` for image checks and OCR.
- OpenCV QR and optional barcode support for decoding available package
  identifiers (barcode support depends on the installed OpenCV build).
- ReportLab for downloadable inspection PDFs.
- A local HOG/linear-SVM training and evaluation scaffold; no trained model is
  supplied or integrated into inspection inference.

### Architecture and inspection flow

The browser sends authenticated requests to Flask. Route modules validate
requests and enforce roles, then call service modules for image handling,
quality checks, OCR, code scanning, comparison, and reporting. MySQL remains
the system of record for users, medicines, batches, inspections, detected
details, and findings. An inspection upload and its database records are
handled transactionally. PDF reports are generated for authorized requests;
the application does not store generated reports as database blobs.

### Project structure

```text
app.py, config.py              Flask application and configuration
database/                      MySQL connection, schema reference, migration
routes/                         Authentication, catalog, batch, inspection APIs
services/                       OCR, image quality, scanner, comparison, reports
templates/, static/             HTML templates, CSS, and browser JavaScript
tests/                           Automated tests (database interactions mocked)
ml/train_baseline.py             Dataset validation and grouped evaluation CLI
ml/dataset/                      Dataset provenance and preparation guidance
uploads/                         Local package images; excluded from Git
requirements.txt                 Python dependencies
```

### MySQL database setup

The application expects the existing `medicine_packaging_db` database and does
not create or migrate database objects at startup. The existing database
already has its schema; **do not rerun** `database/schema.sql` against it.
That file is retained as a schema reference and for a separately approved,
fresh-database setup only. The audit table is a separate, manual migration
described below. MySQL 8.0.16 or newer is required by the schema's enforced
`CHECK` constraints; duplicate-serial analytics use `JSON_TABLE` and require
MySQL 8.0.4 or newer.

## Important files

- [`app.py`](app.py): Flask application factory, API error handling, health
  route, and the secure initial-admin CLI command.
- [`config.py`](config.py): environment-backed Flask and MySQL settings.
- [`database/connection.py`](database/connection.py): per-request MySQL
  connection lifecycle, cursor helpers, and transaction context manager.
- [`routes/auth_routes.py`](routes/auth_routes.py): login/logout, current user,
  admin user management, and initial-admin creation.
- [`tests/test_user_management.py`](tests/test_user_management.py): mocked
  multi-user login, admin account management, role enforcement, and deactivation
  tests.
- [`routes/medicine_routes.py`](routes/medicine_routes.py): manufacturer and
  medicine operations.
- [`routes/batch_routes.py`](routes/batch_routes.py): medicine batch operations.
- [`routes/inspection_routes.py`](routes/inspection_routes.py): image upload,
  authenticated OCR processing, inspection history/report reads, and storage
  of caller-supplied detection results.
- [`services/image_service.py`](services/image_service.py): extension, MIME and
  image-signature checks, generated filenames, and safe resolution of stored
  inspection image paths.
- [`services/ocr_service.py`](services/ocr_service.py): OpenCV preprocessing
  and Tesseract text extraction.
- [`services/field_extraction.py`](services/field_extraction.py): conservative
  label/pattern-based extraction of medicine fields from OCR text.
- [`services/comparison_service.py`](services/comparison_service.py): normalized
  comparisons, confidence gating, and schema-compatible findings.
- [`services/validation.py`](services/validation.py): request data validation
  and JSON-safe database values.
- [`tests/test_backend.py`](tests/test_backend.py): database-independent API
  and validation tests.
- [`tests/test_comparison.py`](tests/test_comparison.py): comparison, formatting,
  confidence, and status-precedence tests.

## Setup on Windows

Use the already-created `medicine_packaging_db`; the Flask app never creates,
drops, or migrates database objects.

```powershell
cd C:\Users\Hp\Documents\Medicine-Packaging-Detector
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally. Set `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, and
`DB_NAME` for your MySQL account. Set `FLASK_SECRET_KEY` to a new random value;
for example, generate one locally with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Do not share or commit `.env`. `.gitignore` excludes it. The example file
contains no real credentials.

OCR requires the separate Tesseract executable in addition to the Python
packages installed from `requirements.txt`. On Windows, the application checks
`C:\Program Files\Tesseract-OCR\tesseract.exe` automatically, then searches
`PATH`. If Tesseract is installed elsewhere, set `TESSERACT_CMD` in `.env` to
the executable's actual local path. Do not put a machine-specific path in
source code. If no executable is found, OCR requests return a clear HTTP 503
diagnostic. `OCR_MAX_PIXELS` defaults to 25,000,000 decoded pixels.
`OCR_MIN_CONFIDENCE` defaults to 80 and accepts values from 0 to 100. It is
applied to each field's Tesseract line-confidence average; this prototype
threshold is a review policy, not a clinically validated safety threshold.

Create the initial admin account interactively; the password is requested
without echoing it:

```powershell
python -m flask --app app:create_app create-admin
```

## Run and test

Start the local development server:

```powershell
python -m flask --app app:create_app run --debug
```

The API listens at `http://127.0.0.1:5000`. Run the included tests with:

```powershell
python -m unittest discover -s tests -v
```

`GET /api/health` checks the Flask process without connecting to MySQL and
reports that OCR code is implemented; it does not check whether the separate
Tesseract executable is installed.
`GET /api/health/database` performs a read-only database connection/version
check when called.

## Web frontend

The Flask app serves the responsive, same-origin HTML/CSS/vanilla-JavaScript
interface at `/login`, `/dashboard`, `/medicines`, `/batches`,
`/inspections/new`, `/inspections`, and `/inspections/<inspection_id>`. Admins
also have access to `/users` for account management. Open
`http://127.0.0.1:5000/login` after starting Flask. Authentication uses the
existing HTTP-only Flask session cookie; the browser does not store passwords
or database credentials.

The dashboard reads inspection totals, monthly trends, batch summaries, expiry
alerts, common errors and repeated serial identifiers from
`GET /api/dashboard/summary`. Medicine management uses the existing medicine,
manufacturer, and batch read APIs; administrators can add or edit medicine
records. The inspection form uploads the selected image, then calls the OCR
endpoint and displays its saved report. History supports server-side status and
text filtering with pagination. Batch tracking supports server-side medicine,
manufacturer, batch and expiry filtering. Inspection reports can be downloaded
as authenticated PDF files.

### Accounts and roles

The existing `Users` table supports the roles `admin` and `pharmacist`; no
schema migration is required. The interface labels the `pharmacist` role as
“Inspector” for inspection work, while the database and authorization checks
continue to use the supported `pharmacist` value. The schema has `username`
but no separate full-name field, so the account name is stored and displayed
as the username.

Admins can open **User management** from the sidebar or directly at
`/users`. That page and the existing `GET /api/users`, `POST /api/users`, and
`PATCH /api/users/<id>` routes are protected by backend admin-role checks.
Admins can create accounts, update usernames/emails/roles, and activate or
deactivate accounts. An unused account can be permanently deleted; accounts
with inspection history cannot be deleted and must be deactivated instead.
Admins cannot delete their own account or the last active Admin. Passwords are
hashed server-side; hashes are omitted from API responses.

Login accepts a registered email address or the existing username, preserving
compatibility with the initial admin account. Inactive users are rejected at
login and on subsequent authenticated API requests.

To create and try an Inspector account:

1. Start Flask and sign in at `http://127.0.0.1:5000/login` using the existing
   admin email or username and password.
2. Open **User management**, select **Add user**, enter the account name
   (username), email, a password of at least eight characters, and the
   **Inspector (pharmacist role)** option.
3. Create the account, log out, and sign in with that user's email and
   password. The Inspector can use inspection, OCR, and history features but
   receives `403` for user management and admin-only catalog writes.
4. Sign in as Admin again to view or deactivate the account. A deactivated
   account cannot log in, and its existing sessions stop authorizing requests.
   Permanent deletion is available only if the account has never created an
   inspection; history-bearing accounts display a deactivation recommendation.

No separate frontend build or Node package installation is required. The
frontend JavaScript syntax can be checked locally with:

```powershell
node --check static\js\api.js
node --check static\js\login.js
node --check static\js\app.js
```

The frontend test cases use Flask's test client and mocked database calls; they
cover page/static asset loading, summary and history API behavior, logout,
inspection report values, and protected image delivery. They do not verify a
live MySQL-backed browser session. All four saved statuses (MATCH, MISMATCH,
NEEDS_REVIEW, and EXPIRED) are displayed distinctly. The result remains a
screening aid and does not establish medicine authenticity or safety.

## API outline

API responses are JSON except the protected inspection-image route, which
returns image bytes, and successful `204` deletes. Login uses a server-signed,
HTTP-only session cookie (`SameSite=Strict`). Catalog writes and user
administration require the `admin` role; inspections and reads require an
authenticated user.

Web pages are served by Flask and are not themselves a substitute for the API's
authentication checks. API data, image requests, and write permissions remain
protected by the backend.

| Method and path | Purpose |
|---|---|
| `POST /api/auth/login` | Login with registered `email` or `username` and `password` |
| `POST /api/auth/logout` | Clear the current session |
| `GET /api/auth/me` | Return the authenticated user |
| `GET`, `POST /api/users` | List/create users (admin) |
| `PATCH /api/users/<id>` | Update user (admin); deactivate instead of deleting history |
| `DELETE /api/users/<id>` | Permanently delete only an account with no inspection history (admin) |
| `GET /api/dashboard/summary` | Database-derived totals, monthly trends, batch activity, expiry alerts, error counts, and repeated serial identifiers |
| `GET`, `POST /api/manufacturers` | Search/list and create manufacturers |
| `PUT`, `DELETE /api/manufacturers/<id>` | Update/delete manufacturer (FKs may restrict deletion) |
| `GET`, `POST /api/medicines` | Search/list and create medicines |
| `GET`, `PUT`, `DELETE /api/medicines/<id>` | Read/update/delete medicine |
| `GET`, `POST /api/medicines/<id>/batches` | List/create batches |
| `PUT`, `DELETE /api/batches/<id>` | Update/delete batch (inspection references may restrict deletion) |
| `POST /api/inspections` | Upload an image and create a `NEEDS_REVIEW` inspection |
| `POST /api/inspections/<id>/ocr` | Extract, compare, and store OCR fields/findings for the stored image |
| `GET /api/inspections` | Paginated history from `v_inspection_summary`; optional status filter |
| `GET /api/inspections/<id>` | Inspection, expected/detected details, comparison findings, and error logs |
| `GET /api/inspections/<id>/image` | Authenticated access to the stored package image |
| `POST /api/inspections/<id>/results` | Store caller-supplied detail/error rows transactionally |

Image upload is `multipart/form-data` with an `image` field. Optional
`medicine_id` and `batch_id` select expected catalog records. Only JPG/JPEG/PNG
extensions, matching MIME types, and matching file signatures are accepted.
This is signature-level validation, not a full image decode. The request size
limit defaults to 8 MiB and is configurable through `MAX_CONTENT_LENGTH`.
Images are stored in the configured upload folder (default `uploads/`) under
generated names. Their relative path is stored in MySQL. If inserting the
inspection fails, the request rolls back and the saved image is removed. An
upload creates a `NEEDS_REVIEW` inspection with `ocr_status: not_run`.

### OCR processing

After upload, call `POST /api/inspections/<inspection_id>/ocr` with the same
authenticated session. The endpoint processes only the image path already
stored for that inspection; it does not accept a replacement image. It rejects
inspections outside `NEEDS_REVIEW`, existing results, invalid stored paths,
and unreadable images. It first checks image sharpness, luminance, resolution and approximate tilt
using configurable heuristics. Clearly unsuitable images receive HTTP 422 with
measurements and retake recommendations before Tesseract runs. Reasonable
images proceed with a warning where appropriate; these checks do not guarantee
OCR accuracy or reliably determine whether the complete package is framed.
The endpoint then preprocesses the image (grayscale, median denoising, and
adaptive thresholding where image dimensions allow), returns the original
line-structured OCR text, and stores the seven OCR fields plus an available
QR/barcode scan result in `Detected_Details` in a single transaction.

The raw OCR text is returned by the processing response but is not retained in
MySQL: the current seven-table schema has no raw-text column. The normalized
field values and their status/confidence are persisted for inspection history.

Supported fields are medicine name, manufacturer, strength, dosage form, batch
number, manufacturing date, and expiry date. Extraction is deliberately
conservative and uses visible labels/patterns; it does not infer a value from
the database or fill unreadable data. Each field reports `READ`, `NOT_FOUND`,
or `UNREADABLE`. OCR word confidence is averaged per source line where
Tesseract provides it; confidence can be null. The OpenCV QR and barcode detectors store decoded code text and conservatively
parsed GS1 product, batch, expiry and serial identifiers in the existing
`barcode` detail field. QR URLs are inert text and are never opened. An absent
or unreadable code requires review where appropriate; decoded values do not
authenticate a manufacturer.

Within the same transaction, the endpoint retrieves the inspection's selected
medicine and batch, verifies that the batch belongs to that medicine, compares
the OCR fields, inserts applicable `Error_Logs`, and finalizes status. Text
comparisons ignore case and repeated whitespace. Strength formatting
normalizes spaces, case, and decimal punctuation without converting units.
Unambiguous common date formats are normalized; ambiguous dates require review
instead of being guessed.

All seven supported fields are required for `MATCH`, along with registered
expected values, readable values at or above `OCR_MIN_CONFIDENCE`, successful
comparisons, and a non-expired registered batch. Missing expected references,
missing/unreadable values, unknown or ambiguous dates, and confidence below the
configured threshold require `NEEDS_REVIEW`. A confirmed difference in a field
with a matching `Error_Logs` type is logged at `high` severity and prevents `MATCH`; an
expired registered batch is logged at `critical` severity and takes precedence
over other status findings. Review findings use `medium` severity. Existing
database triggers preserve the `EXPIRED` > `MISMATCH` > `NEEDS_REVIEW`
precedence. The application sets `MATCH` only after every required comparison
passes and no review or expiry finding exists.

The schema's `Error_Logs.error_type` enum does not include `manufactured_on`.
To avoid a schema migration or mislabeling that finding, a manufacturing-date
difference is returned as a manual-review finding, leaves the inspection at
`NEEDS_REVIEW`, and is not inserted into `Error_Logs`. This specific finding is
therefore not retained in the database comparison log. No counterfeit or
authenticity determination is made.

If Tesseract is not installed or configured, the endpoint responds with an
availability error and saves no details.

### Upload tests

Run the complete backend tests from the project root:

```powershell
python -m unittest discover -s tests -v
```

Upload tests use a generated, in-memory 1x1 PNG, a temporary upload folder, and
a mocked connection. They verify authentication, successful `NEEDS_REVIEW`
creation, the `ocr_status: not_run` response, missing and
invalid files, the configured size limit, and cleanup/rollback after a
simulated insert failure. They do not connect to MySQL or leave inspection
records in the project database. A live upload would create a persistent
inspection and image; this test suite does not perform one.

OCR and comparison tests use generated synthetic images, mocked OCR where
appropriate, and mocked database connections. They cover field extraction,
confidence handling, catalog comparisons, missing/unreadable data, expired
batches, batch/medicine consistency, duplicate prevention, status precedence,
path traversal rejection, and transaction rollback. These tests do not connect
to the project database or process project/user images.

The results endpoint accepts JSON containing `detected_details` and optional
`error_logs`; it validates and stores those supplied values in one transaction.
It does not independently verify that submitted values are genuine OCR output
or run the OCR comparison workflow. Findings update status through the schema's
existing trigger. No endpoint establishes medicine authenticity.

The database schema's `Error_Logs` is append-only. The API intentionally has no
update/delete endpoints for findings or inspections. MySQL foreign keys remain
the final authority for restricted catalog deletions.

## Verification upgrade and operational limits

The inspection workflow applies configurable image-quality checks before OCR.
Thresholds are `IMAGE_BLUR_VARIANCE_MIN`, `IMAGE_DARK_MEAN_MAX`,
`IMAGE_BRIGHT_MEAN_MIN`, `IMAGE_MIN_WIDTH`, `IMAGE_MIN_HEIGHT`, and
`IMAGE_MAX_TILT_DEGREES`; defaults are set in `config.py` and may be overridden
in the local environment. A blocked image returns a quality score, issue codes,
measurements, and retake recommendations without running OCR. Tilt is an
approximation and incomplete framing cannot be reliably identified.

OpenCV QR/barcode scans are stored as JSON-compatible text in the existing
`Detected_Details.field_name = 'barcode'` column. Supported GS1 values are
compared with registered package barcode, batch, and expiry data. Serial reuse
across different inspection records creates a manual-review signal, not a
counterfeit finding. The system cannot distinguish reinspection of the same
physical package from reuse of that serial without a trusted package-level
identity service. QR URLs are never opened, and external manufacturer
verification is not implemented.

Use the protected `GET /api/inspections/<id>/report.pdf` route to download a
PDF containing the saved comparison, OCR details, image-quality assessment,
scan results, findings and limitations. The report route and stored-image route
require an authenticated session. A PDF does not certify authenticity, quality,
or safety.

The `/batches` page and `GET /api/batches/summary` provide database-derived
batch search, expiry filtering, and status totals. `GET
/api/batches/<id>/inspections` returns batch-specific history. Dashboard
statistics are read from current inspection, error, batch and barcode data;
repeated-serial alerts are explicitly review-only.
The duplicate scan uses MySQL `JSON_TABLE` and requires MySQL 8.0.4 or newer.

### Audit migration

Audit logging requires the additive migration
[`database/migrations/001_create_audit_logs.sql`](database/migrations/001_create_audit_logs.sql).
The app does not run it automatically, modify `database/schema.sql`, or create
the table at startup. First create and verify a backup of the existing
`medicine_packaging_db`. In MySQL Workbench, connect to the intended server,
open the migration with **File → Open SQL Script…**, verify that it selects
`medicine_packaging_db`, and execute only this migration. Do not execute
`database/schema.sql` for an existing installation. Confirm in Action Output
that it completed, then verify the table with:

```sql
SELECT TABLE_NAME
FROM information_schema.tables
WHERE TABLE_SCHEMA = 'medicine_packaging_db'
  AND TABLE_NAME = 'Audit_Logs';
```

The migration only creates `Audit_Logs` with an actor snapshot, timestamp,
action, target and non-sensitive request metadata, indexes, and an actor
foreign key that sets the actor reference to NULL if a user is deleted. It adds
no triggers and does not alter existing rows. Until applied, audit writes fail
explicitly in the Flask log and the response includes
`X-Audit-Log-Status: failed`; the `/api/audit` endpoint is not usable. Only
Admins can read audit records. The API exposes no audit update/delete route.
Database-level protection against a privileged DBA changing audit rows is not
provided by this prototype.

### Anomaly-detection dataset and baseline

No verified genuine/anomalous packaging image dataset is included. Dataset
structure, provenance guidance and a local HOG/linear-SVM training and
evaluation scaffold are in [`ml/dataset/README.md`](ml/dataset/README.md) and
[`ml/train_baseline.py`](ml/train_baseline.py). The script requires at least two
independently verified images per class and reports a holdout confusion matrix,
precision, recall, F1 and accuracy. Its deterministic holdout split groups
related package views to keep them from appearing in both training and
evaluation sets. No saved model is included or loaded by Flask. Real-world
model training, validation and inference remain pending a documented,
representative dataset.

Install dependencies from `requirements.txt`. Run the full test suite and
Python compilation checks from the project root:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py config.py database routes services ml tests
python ml\train_baseline.py --help
node --check static\js\api.js
node --check static\js\login.js
node --check static\js\app.js
```

Automated tests use synthetic images and mocked database interactions; they
are not live MySQL integration, real-browser end-to-end, or manufacturer
verification tests.

## Database documentation

See [`database/README.md`](database/README.md) for the ER diagram, relationships,
normalization, schema setup behavior, and read-only Workbench verification
queries. Do not execute `schema.sql` from the backend.
