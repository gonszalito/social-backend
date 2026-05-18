# GoBig Backend Test Guide

Welcome! This guide will help you set up, run, and understand the test suite for the GoBig social backend API.

## Quick Start

If you just want to run all tests quickly:

```bash
# 1. Set up Python environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# 3. Run all tests
pytest -q
```

That's it! The tests are self-contained and don't require external services like Redis or Postgres.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Environment Setup](#environment-setup)
3. [Running Tests](#running-tests)
4. [Understanding the Test Suite](#understanding-the-test-suite)
5. [Test Configuration](#test-configuration)
6. [Troubleshooting](#troubleshooting)

---

## Prerequisites

**Required:**
- Python 3.10 or higher
- macOS, Linux, or WSL2 on Windows

**Not Required:**
- Redis (tests use an in-memory fake)
- PostgreSQL (tests mock database operations)
- External API services (tests use mocks)

---

## Environment Setup

### Step 1: Create a Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Step 2: Upgrade pip

```bash
python -m pip install --upgrade pip
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

This installs:
- **Application dependencies**: FastAPI, Redis client, JWT libraries, etc.
- **Test dependencies**: pytest, pytest-asyncio, httpx, websockets

### Verify Installation

```bash
pytest --version
```

You should see pytest version 8.0 or higher.

---

## Running Tests

### Basic Commands

**Run all tests (quiet mode):**
```bash
pytest -q
```

**Run all tests (normal output):**
```bash
pytest
```

**Run all tests (verbose, show all test names):**
```bash
pytest -vv
```

**Run tests with print statements visible:**
```bash
pytest -vv -s
```

### Run Specific Tests

**Run a single test file:**
```bash
pytest tests/test_auth_middleware.py
```

**Run tests matching a keyword:**
```bash
pytest -k "auth"
```

**Run a specific test function:**
```bash
pytest tests/test_auth_middleware.py::test_missing_authorization_header
```

### Understanding Test Output

When you run `pytest -q`, you'll see:

```
.........                                                    [100%]
9 passed in 2.34s
```

- Each `.` represents a passing test
- `F` indicates a failure
- `E` indicates an error
- `s` indicates a skipped test

For more detail, use `pytest -vv`:

```
tests/test_auth_middleware.py::test_missing_authorization_header PASSED  [ 11%]
tests/test_auth_middleware.py::test_invalid_token PASSED                 [ 22%]
```

---

## Understanding the Test Suite

The test suite covers 7 major feature areas:

### 1. Authentication & Authorization (`tests/test_auth_middleware.py`)

Tests JWT authentication middleware:
- Bearer token extraction and validation
- Role-based access control (Business vs Developer admin)
- Token revocation (blocklist functionality)
- Dev-only token generation endpoints

**Run:** `pytest tests/test_auth_middleware.py`

### 2. NLP Ingest (`tests/test_nlp_ingest.py`)

Tests recipe batch processing with NLP enrichment:
- Flag-based processing (staging vs enrichment)
- Request validation and error handling
- Idempotency checks
- Database persistence (when enabled)

**Run:** `pytest tests/test_nlp_ingest.py`

### 3. File Storage & Upload (`tests/test_storage_presign.py`)

Tests presigned URL generation and file uploads:
- Upload type validation (avatar, voice log, etc.)
- File size limit enforcement
- Multipart file upload handling
- Security (no exposed bucket names)

**Run:** `pytest tests/test_storage_presign.py`

### 4. Social Graph (`tests/test_social_graph.py`)

Tests social features:
- Follow/unfollow relationships
- Activity feed with cursor pagination
- User profiles with caching
- Recipe sharing and potluck invitations

**Run:** `pytest tests/test_social_graph.py`

### 5. WebSocket Signaling (`tests/test_websocket_signaling.py`)

Tests real-time WebRTC signaling:
- WebSocket connection authentication
- Message forwarding between clients
- Cross-pod communication (via Redis pub/sub)
- Disconnect cleanup

**Run:** `pytest tests/test_websocket_signaling.py`

### 6. Prometheus Metrics (`tests/test_prometheus_metrics.py`)

Tests observability features:
- Request latency histogram recording
- Metrics endpoint format
- Proper labeling (method, endpoint, status)
- Exclusion of health/metrics endpoints

**Run:** `pytest tests/test_prometheus_metrics.py`

### 7. Feature Flags (`tests/test_admin_flags.py`)

Tests admin feature flag management:
- Flag retrieval and updates
- Developer-only mutations
- Audit logging
- CloudFlare KV sync webhook

**Run:** `pytest tests/test_admin_flags.py`

---

## Test Configuration

### Test Fixtures

Tests use shared fixtures defined in `tests/conftest.py`:

- **`fake_redis`**: In-memory Redis replacement (no external service needed)
- **`test_keys`**: Dynamically generated RSA key pairs for JWT testing
- **`client`**: FastAPI test client with automatic setup/teardown
- **`ws_base_url`**: Live test server URL for WebSocket tests

### Environment Variables

Tests run with safe defaults. You generally don't need to set environment variables.

**Optional overrides for testing:**

```bash
# Enable Postgres persistence in tests (requires live DB)
export GOBIG_POSTGRES_ENABLED=1
export DATABASE_URL="postgresql://user:pass@localhost/testdb"

# Point to a mock AI wrapper (for manual integration testing)
export GOBIG_AI_WRAPPER_URL="http://localhost:8099"

# Enable dev token endpoints
export GOBIG_DEV_ALLOW_TOKEN_MINT=1
```

**Note**: Most tests mock external services, so these aren't required for normal test runs.

---

## Test Isolation

Each test is fully isolated:

1. **Fresh application state**: Each test gets a clean FastAPI app instance
2. **Separate Redis**: In-memory fake Redis is reset between tests
3. **No shared data**: Tests don't interfere with each other
4. **Deterministic**: Tests produce the same results every time

This means you can:
- Run tests in any order
- Run a single test without running others first
- Run tests in parallel (if needed)

---

## Common Test Scenarios

### Testing Auth Changes

```bash
# Run all auth-related tests
pytest -k "auth" -vv

# Test just the middleware
pytest tests/test_auth_middleware.py -vv

# Test token revocation specifically
pytest tests/test_auth_middleware.py::test_revoke_token -vv
```

### Testing API Endpoints

```bash
# Test all social features
pytest tests/test_social_graph.py -vv

# Test feed pagination
pytest tests/test_social_graph.py::test_feed_pagination -vv
```

### Debugging Failed Tests

```bash
# Show print statements and logging
pytest tests/test_nlp_ingest.py -vv -s

# Stop on first failure
pytest -x

# Show local variables on failure
pytest -l
```

---

## Continuous Integration

The test suite is designed to run in CI/CD pipelines:

```bash
# CI-friendly command (quiet output, fails fast)
pytest -q --tb=short

# Generate coverage report
pytest --cov=app --cov-report=html

# Run with strict markers (no warnings)
pytest --strict-markers
```

---

## Troubleshooting

### "pytest: command not found"

Make sure your virtual environment is activated:
```bash
source .venv/bin/activate
```

### "ModuleNotFoundError: No module named 'pytest'"

Install test dependencies:
```bash
pip install -r requirements-dev.txt
```

### "No tests ran" or "ERROR: file not found"

Make sure you're in the project root directory:
```bash
cd /path/to/gobig-social-backend
pytest
```

### Tests are slow

Use quiet mode to reduce output overhead:
```bash
pytest -q
```

Run specific test files instead of the entire suite:
```bash
pytest tests/test_auth_middleware.py
```

### Import errors

Ensure all dependencies are installed:
```bash
pip install -r requirements.txt -r requirements-dev.txt
```

### Port already in use (WebSocket tests)

WebSocket tests start a temporary server. If you see port conflicts:
```bash
# Kill any existing uvicorn processes
pkill -f uvicorn

# Or specify a different port (advanced)
pytest tests/test_websocket_signaling.py --ws-port=8001
```

---

## Test Coverage Summary

| Feature Area | Test File | Test Count | External Dependencies |
|--------------|-----------|------------|----------------------|
| Authentication | `test_auth_middleware.py` | 7 | None (uses fake Redis) |
| NLP Processing | `test_nlp_ingest.py` | 6 | None (mocked) |
| File Storage | `test_storage_presign.py` | 6 | None |
| Social Graph | `test_social_graph.py` | 5 | None (uses fake Redis) |
| WebSockets | `test_websocket_signaling.py` | 3 | None (uses fake Redis) |
| Metrics | `test_prometheus_metrics.py` | 4 | None |
| Feature Flags | `test_admin_flags.py` | 5 | None (uses fake Redis) |

**Total: ~36 tests covering all core functionality**

---

## Next Steps

After running tests successfully:

1. **Explore test files**: Read `tests/test_*.py` to understand test patterns
2. **Run the app**: Start the development server with `uvicorn app.main:app --reload`
3. **Manual testing**: Use tools like Postman or curl to interact with endpoints
4. **Add new tests**: Follow existing patterns when adding features

---

## Getting Help

If you encounter issues:

1. Check test output carefully (use `-vv -s` for maximum detail)
2. Verify your Python version: `python --version` (should be 3.10+)
3. Ensure all dependencies are installed: `pip list`
4. Try running a single simple test: `pytest tests/test_prometheus_metrics.py -vv`

---

**Happy Testing!**
