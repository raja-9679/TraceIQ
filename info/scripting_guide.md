# TraceIQ Scripting Guide

The **Run Script** step allows you to execute custom code during your test runs. This is powerful for complex logic, data manipulation, or custom validations that standard steps cannot handle.

## 1. Supported Languages

| Feature | JavaScript | Python |
| :--- | :--- | :--- |
| **Execution Context** | **Browser (Client-Side)**. Runs inside the page being tested. | **Runner (Server-Side)**. Runs in the Docker container executing the test. |
| **Access** | Can access DOM (`document`, `window`), LocalStorage, Cookies. | Can access file system, complex math libraries, processed data. |
| **Best For** | extracting data from page, client-side validation, interacting with page JS. | Data processing, API chaining logic, complex assertions. |

---

## 2. Writing Scripts

### General Rules
1.  **Body Only**: Write *only* the code logic. Do NOT wrap it in a function definition (e.g., `def run():` or `function() { ... }`). The system does this automatically.
2.  **Return Value**: The value returned by your script is captured and can be stored in a variable.

### JavaScript
```javascript
// Access the DOM
var title = document.title;

// Access Variables
var orderId = variables['userId'];

// Return a value
return title + "_" + orderId;
```

### Python
```python
# Access Variables
user_id = context['variables']['userId']

# Logic
result = f"PROCESSED_{user_id}"

# Return a value
return result
```

---

## 3. Variables & Data Passing

You can pass data between steps using the **Variable Name** field in the UI.

1.  **Extract Value** step: extracts text from page -> stores in `myVar`.
2.  **Run Script** step: reads `myVar` -> processes it -> returns new value -> stores in `processedVar`.
3.  **Goto** step: uses `https://example.com/{{processedVar}}`.

**Access Syntax:**
-   **JavaScript**: `variables['variableName']`
-   **Python**: `context['variables']['variableName']`

---

## 4. Logging & Debugging

Logs appear in the Execution Engine output (and in the final test report).

### JavaScript
Use `console.log()`.
```javascript
console.log("Found element count: " + document.querySelectorAll('.item').length);
```
*Output prefix: `[Browser-Console]`*

### Python
Use `print()`.
```python
print(f"Processing item: {item_id}")
```
*Output prefix: `[Script-Py Logs]`*

---

## 5. Failing a Test

To explicitly fail a test step based on custom logic:

### JavaScript
Throw an `Error`.
```javascript
if (variables['status'] !== 'Active') {
    throw new Error("Test Failed: Account is not active");
}
```

### Python
Raise an `Exception`.
```python
if context['variables']['status'] != 'Active':
    raise Exception("Test Failed: Account is not active")
```

---

## 6. Common Examples

### Example A: Generate Dynamic Email (JS)
```javascript
const timestamp = new Date().getTime();
return `testuser_${timestamp}@example.com`;
```
*Store result in `dynamicEmail`. Use `{{dynamicEmail}}` in a Fill step.*

### Example B: Validate Mathematical Logic (Python)
```python
total = float(context['variables']['cartTotal'])
tax = float(context['variables']['taxAmount'])
expected = total * 0.1

if abs(tax - expected) > 0.01:
    raise Exception(f"Tax mismatch. Expected {expected}, got {tax}")
return True
```
