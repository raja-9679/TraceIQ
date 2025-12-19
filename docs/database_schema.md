# Database Schema Documentation

This document outlines the current database schema for the Quality Intelligence Platform (TraceIQ).

## Core Tables

### **1. TestSuite** (`testsuite`)
Represents a folder or module of tests. Can be nested.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | Integer (PK) | Unique identifier |
| `name` | String | Name of the suite/module |
| `description` | String | Optional description |
| `execution_mode` | Enum | `continuous` (default) or `separate` |
| `parent_id` | Integer (FK) | ID of the parent suite (for nesting) |
| `settings` | JSON | Stores headers, params, allowed domains, etc. |
| `inherit_settings` | Boolean | Whether to inherit settings from parent (default: `True`) |
| `created_at` | DateTime | Creation timestamp |

### **2. TestCase** (`testcase`)
Represents an individual test case with steps.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | Integer (PK) | Unique identifier |
| `name` | String | Name of the test case |
| `steps` | JSON | List of test steps (goto, click, etc.) |
| `test_suite_id` | Integer (FK) | ID of the parent suite |

### **3. TestRun** (`testrun`)
Represents an execution of a suite or case.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | Integer (PK) | Unique identifier |
| `test_suite_id` | Integer (FK) | ID of the suite being run |
| `test_case_id` | Integer (FK) | ID of the case (if running single case) |
| `status` | Enum | `pending`, `running`, `passed`, `failed`, `error` |
| `total_tests` | Integer | Total tests in the run |
| `passed_tests` | Integer | Number of passed tests |
| `failed_tests` | Integer | Number of failed tests |
| `duration_ms` | Float | Total duration in milliseconds |
| `error_message` | String | Error message if failed |
| `trace_url` | String | Path to the Playwright trace file (zip) |
| `video_url` | String | Path to the execution video |
| `response_status` | Integer | HTTP status code (for API tests) |
| `request_headers` | JSON | Headers used in the request |
| `request_params` | JSON | Parameters used in the request |
| `response_headers` | JSON | Headers received in response |
| `allowed_domains` | JSON | List of allowed domains for this run |
| `domain_settings` | JSON | Domain-specific settings |
| `network_events` | JSON | Captured network events |
| `execution_log` | JSON | Per-case execution timings (start/end) |

### **4. TestCaseResult** (`testcaseresult`)
Stores individual results within a run.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | Integer (PK) | Unique identifier |
| `test_run_id` | Integer (FK) | ID of the parent run |
| `test_name` | String | Name of the test case |
| `status` | Enum | `passed`, `failed`, etc. |
| `duration_ms` | Float | Duration of this specific case |
| `error_message` | String | Error message if failed |
| `trace_url` | String | URL to specific trace (if applicable) |
| `video_url` | String | URL to specific video (if applicable) |
| `ai_analysis` | String | AI-generated analysis of failure |

### **5. User** (`users`)
User authentication and profile.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | Integer (PK) | Unique identifier |
| `email` | String | User email (Unique) |
| `full_name` | String | Full name |
| `hashed_password` | String | Hashed password |
| `is_active` | Boolean | Account status |

## Relationships

*   **TestSuite** has many **TestCases**.
*   **TestSuite** has many sub-modules (**TestSuite**).
*   **TestRun** belongs to a **TestSuite** (and optionally a **TestCase**).
*   **TestRun** has many **TestCaseResults**.
