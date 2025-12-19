# TraceIQ Test Step Documentation

This document provides a comprehensive guide to all available test step types in the TraceIQ Test Builder.

## Navigation Steps

### **Go to URL** (`goto`)
- **Description**: Navigates the browser to a specific web page.
- **Inputs**:
  - **Value**: The full URL (e.g., `https://www.google.com`).
- **Example**: `goto` -> `https://www.thehindu.com`

### **Switch Frame** (`switch-frame`)
- **Description**: Switches the execution context to an `<iframe>`.
- **Inputs**:
  - **Selector**: The CSS selector for the frame, or `main`/`top` to return to the root page.
- **Example**: `switch-frame` -> `#payment-iframe`

---

## Interaction Steps

### **Click** (`click`)
- **Description**: Performs a mouse click on an element.
- **Inputs**:
  - **Selector**: The CSS selector of the element to click.
- **Example**: `click` -> `button#submit`

### **Fill Input** (`fill`)
- **Description**: Types text into an input field or textarea.
- **Inputs**:
  - **Selector**: The CSS selector of the input field.
  - **Value**: The text to type.
- **Example**: `fill` -> `input[name="username"]`, `myuser123`

### **Check Box** (`check`)
- **Description**: Checks a checkbox or radio button.
- **Inputs**:
  - **Selector**: The CSS selector of the checkbox.
- **Example**: `check` -> `#terms-and-conditions`

### **Hover** (`hover`)
- **Description**: Hovers the mouse cursor over an element.
- **Inputs**:
  - **Selector**: The CSS selector of the element.
- **Example**: `hover` -> `.nav-item-dropdown`

### **Select Option** (`select-option`)
- **Description**: Selects an option from a `<select>` dropdown.
- **Inputs**:
  - **Selector**: The CSS selector of the select element.
  - **Value**: The value or label of the option to select.
- **Example**: `select-option` -> `select#country`, `US`

### **Press Key** (`press-key`)
- **Description**: Simulates a single key press on the keyboard.
- **Inputs**:
  - **Value**: The name of the key (e.g., `Enter`, `Escape`, `Tab`, `ArrowDown`).
- **Example**: `press-key` -> `Enter`

### **Scroll To** (`scroll-to`)
- **Description**: Scrolls the page until the specified element is in view.
- **Inputs**:
  - **Selector**: The CSS selector of the element.
- **Example**: `scroll-to` -> `footer`

---

## Assertion Steps

### **Expect Visible** (`expect-visible`)
- **Description**: Asserts that an element is present in the DOM and visible to the user.
- **Inputs**:
  - **Selector**: The CSS selector of the element.
- **Example**: `expect-visible` -> `.success-message`

### **Expect Hidden** (`expect-hidden`)
- **Description**: Asserts that an element is either not in the DOM or is hidden.
- **Inputs**:
  - **Selector**: The CSS selector of the element.
- **Example**: `expect-hidden` -> `.loading-spinner`

### **Expect Text** (`expect-text`)
- **Description**: Asserts that an element contains specific text.
- **Inputs**:
  - **Selector**: The CSS selector of the element.
  - **Value**: The text string expected to be found.
- **Example**: `expect-text` -> `h1.title`, `Welcome Back`

### **Expect URL** (`expect-url`)
- **Description**: Asserts that the current browser URL matches a specific pattern.
- **Inputs**:
  - **Value**: The expected URL or a glob pattern (e.g., `**/dashboard`).
- **Example**: `expect-url` -> `**/article70414441.ece`

---

## Utility Steps

### **Take Screenshot** (`screenshot`)
- **Description**: Captures a full-page screenshot of the current state.
- **Inputs**:
  - **Value**: (Optional) A custom name for the screenshot file.
- **Example**: `screenshot` -> `after-login-state`

### **Wait (ms)** (`wait-timeout`)
- **Description**: Pauses the test execution for a specified amount of time.
- **Inputs**:
  - **Value**: The duration in milliseconds.
- **Example**: `wait-timeout` -> `2000`
