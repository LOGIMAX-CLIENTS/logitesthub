---
case_id: 1
login: none
mode: testing
---

# LogiTestHub - valid login

## Steps
Navigate to http://localhost:5050/login.
Type {{lth_user}} into the Username field (#u).
Type {{lth_password}} into the Password field (#p).
Click the Sign in button.
Assert the text 'Welcome, Login Test' is visible.
