---
login: none
tags: [login, positive, smoke]
---

# Valid login with correct username and password

## Open the login page
Navigate to {{base_url}}index.php/admin/dashboard.
Assert the page title contains 'Login'.

## Sign in
Type {{username}} into the User Name field.
Type {{password}} into the Password field.
Select the first available option in the Select Branch dropdown (#branch_select).
Click the Sign In button.

## Lands on the dashboard
Assert the page title contains 'Dashboard'.
Assert the current URL contains 'admin/dashboard'.
